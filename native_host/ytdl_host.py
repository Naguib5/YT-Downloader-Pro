#!/usr/bin/env python3
"""
ytdl_host.py v18 — Production Ready
Improvements:
  - Intelligent quality selection with codec preference (avc1 > vp9 > other)
  - Download cancellation support
  - ffmpeg auto-detection with graceful fallback
  - Intercepted network URL support from browser
  - Thread safety improvements
  - Better error isolation and logging
  - All original YouTube/Facebook/cookies logic preserved
"""
import sys, json, struct, threading, os, traceback, logging, re, time, signal
class DownloadCancelled(Exception):
    pass

signal.signal(signal.SIGINT, signal.SIG_DFL)

_real_stdout = sys.stdout.buffer
sys.stdout   = sys.stderr  # Redirect stdout to stderr to prevent corruption

_dir     = os.path.dirname(os.path.abspath(__file__))
log_path = os.path.join(_dir, "ytdl_host.log")
logging.basicConfig(
    filename=log_path, level=logging.DEBUG,
    format="%(asctime)s %(levelname)s %(message)s",
    encoding="utf-8"
)
log = logging.getLogger("ytdl_host")

# ── Global state ──────────────────────────────────────────────────────────────
_send_lock      = threading.Lock()
_download_event = threading.Event()   # set() to cancel current download
_active_ydl     = None                # current YoutubeDL instance for cancellation
_active_ydl_lock = threading.Lock()

# ── ffmpeg detection ──────────────────────────────────────────────────────────
def find_ffmpeg():
    """Returns ffmpeg PATH directory (ffmpeg_location) or None."""
    import shutil, subprocess

    def _test(path):
        """تأكد إن ffmpeg يشتغل فعلاً"""
        try:
            r = subprocess.run([path, "-version"], capture_output=True, timeout=5)
            return r.returncode == 0
        except Exception:
            return False

    def _dir_of(path):
        """yt-dlp بيحتاج المجلد مش الملف"""
        return os.path.dirname(path)

    # 1. PATH — الأسرع
    found = shutil.which("ffmpeg")
    if found and _test(found):
        log.info(f"ffmpeg found in PATH: {found}")
        return _dir_of(found)

    # 2. بحث واسع — كل المجلدات اللي ممكن يكون فيها ffmpeg
    candidates = [
        # جوار السكريبت
        os.path.join(_dir, "ffmpeg.exe"),
        os.path.join(_dir, "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(_dir, "ffmpeg", "ffmpeg.exe"),
        # مجلد أعلى
        os.path.join(os.path.dirname(_dir), "ffmpeg", "bin", "ffmpeg.exe"),
        os.path.join(os.path.dirname(_dir), "ffmpeg.exe"),
    ]

    # ابحث عن ffmpeg*.exe في كل المجلدات اللي في PATH
    path_dirs = os.environ.get("PATH", "").split(os.pathsep)
    for d in path_dirs:
        for name in ("ffmpeg.exe", "ffmpeg"):
            candidates.append(os.path.join(d, name))

    # Common Windows install paths
    common = [
        r"C:\ffmpeg\bin\ffmpeg.exe",
        r"C:\ffmpeg\ffmpeg.exe",
        r"C:\Program Files\ffmpeg\bin\ffmpeg.exe",
        r"C:\Program Files (x86)\ffmpeg\bin\ffmpeg.exe",
        r"C:\tools\ffmpeg\bin\ffmpeg.exe",
        os.path.expanduser(r"~\ffmpeg\bin\ffmpeg.exe"),
        os.path.expanduser(r"~\AppData\Local\Programs\ffmpeg\bin\ffmpeg.exe"),
    ]

    # بحث recursive في مجلد البرنامج (بحثين مستويين)
    for root, dirs, files in os.walk(_dir):
        depth = root.replace(_dir, "").count(os.sep)
        if depth > 3:
            continue
        for f in files:
            if f.lower() in ("ffmpeg.exe", "ffmpeg"):
                candidates.append(os.path.join(root, f))
        dirs[:] = [d for d in dirs if not d.startswith(".")]

    for c in (candidates + common):
        if c and os.path.isfile(c) and _test(c):
            log.info(f"ffmpeg found: {c}")
            return _dir_of(c)

    log.warning("ffmpeg NOT FOUND — merge disabled, will use single-file format")
    return None

FFMPEG_PATH = find_ffmpeg()
log.info(f"ffmpeg: {FFMPEG_PATH or 'NOT FOUND (merge will be skipped)'}")

# ══════════════════════════════════════════════════════════════════════════════
#  Self-Registration — يسجّل الـ native host أوتوماتيك على أي جهاز جديد
# ══════════════════════════════════════════════════════════════════════════════
def self_register():
    """
    يسجّل الـ native host في Windows Registry تلقائياً.
    بيشتغل مرة واحدة لما يلاقي الـ manifest مش موجود أو بمسار قديم.
    """
    try:
        import winreg, shutil, subprocess

        _bat  = os.path.join(_dir, "ytdl_host.bat")
        _json = os.path.join(_dir, "com.ytdl.pro.json")

        # ── اعمل ytdl_host.bat لو مش موجود أو فيه path غلط ──
        _py_exe = sys.executable
        _needs_bat_update = True
        if os.path.exists(_bat):
            try:
                with open(_bat, encoding="utf-8", errors="ignore") as f:
                    content = f.read()
                # لو فيه reference للـ script الصح مع الـ python الصح، مش محتاج تحديث
                if sys.executable.replace("\\", "/").lower() in content.replace("\\", "/").lower():
                    _needs_bat_update = False
            except Exception:
                pass

        if _needs_bat_update:
            with open(_bat, "w", encoding="utf-8") as f:
                f.write(f'@echo off\n"{_py_exe}" "{os.path.join(_dir, "ytdl_host.py")}"\n')
            log.info(f"self_register: updated ytdl_host.bat -> {_py_exe}")

        # ── اعمل manifest.json لو مش موجود أو بمسار قديم ──
        _needs_json_update = True
        _ext_id = "imfkhbibjnlddbkkmlokmpddkhkpdiid"

        if os.path.exists(_json):
            try:
                import json as _j
                with open(_json, encoding="utf-8") as f:
                    d = _j.load(f)
                # لو المسار المسجل فيه هو نفسه الـ bat الحالي، مش محتاج تحديث
                if d.get("path", "").lower() == _bat.lower():
                    _needs_json_update = False
                    # احتفظ بالـ Extension ID الموجود
                    for origin in d.get("allowed_origins", []):
                        if "chrome-extension://" in origin:
                            parts = origin.replace("chrome-extension://", "").strip("/")
                            if parts and parts != "imfkhbibjnlddbkkmlokmpddkhkpdiid":
                                _ext_id = parts
            except Exception:
                pass

        if _needs_json_update:
            import json as _j
            manifest = {
                "name": "com.ytdl.pro",
                "description": "YT Downloader Pro Native Host",
                "path": _bat,
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{_ext_id}/"]
            }
            with open(_json, "w", encoding="utf-8") as f:
                _j.dump(manifest, f, indent=2, ensure_ascii=False)
            log.info(f"self_register: updated manifest -> {_bat}")

        # ── سجّل في Registry ──
        _reg_key = r"Software\Google\Chrome\NativeMessagingHosts\com.ytdl.pro"
        _current_reg = None
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _reg_key) as k:
                _current_reg, _ = winreg.QueryValueEx(k, "")
        except Exception:
            pass

        if _current_reg != _json:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _reg_key) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _json)
            log.info(f"self_register: Registry updated -> {_json}")
        else:
            log.debug("self_register: Registry already OK")

    except ImportError:
        log.debug("self_register: not Windows, skipping")
    except Exception as e:
        log.warning(f"self_register: {e}")

# شغّل التسجيل الأوتوماتيك عند بداية الـ host
self_register()

# ══════════════════════════════════════════════════════════════════════════════
#  Native Messaging protocol
# ══════════════════════════════════════════════════════════════════════════════
def read_message():
    try:
        raw = sys.stdin.buffer.read(4)
        if not raw or len(raw) < 4: return None
        size = struct.unpack('<I', raw)[0]
        if size > 1_048_576:
            log.error(f"Message too large: {size}")
            return None
        data = sys.stdin.buffer.read(size)
        return json.loads(data.decode('utf-8'))
    except Exception as e:
        log.error(f"read_message: {e}")
        return None

def send_message(obj):
    with _send_lock:
        try:
            data = json.dumps(obj, ensure_ascii=False).encode('utf-8')
            _real_stdout.write(struct.pack('<I', len(data)))
            _real_stdout.write(data)
            _real_stdout.flush()
        except Exception as e:
            log.error(f"send_message: {e}")

def fmt_bytes(b):
    if not b: return ""
    if b >= 1 << 30: return f"{b/(1<<30):.1f} GB"
    if b >= 1 << 20: return f"{b/(1<<20):.1f} MB"
    return f"{b/1024:.0f} KB"

def send_progress(pct, speed="", size=""):
    send_message({"type": "progress", "pct": pct, "speed": speed, "size": size})

# ══════════════════════════════════════════════════════════════════════════════
#  Progress hook factory
# ══════════════════════════════════════════════════════════════════════════════
def make_hook():
    def hook(d):
        try:
            if _download_event.is_set():
                raise DownloadCancelled()
            st = d["status"]
            if st == "downloading":
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                done  = d.get("downloaded_bytes", 0)
                speed = d.get("speed") or 0
                pct   = int(done / total * 100) if total else -1
                spd   = (f"{speed/1_048_576:.1f} MB/s" if speed >= 1_048_576
                         else f"{speed/1024:.0f} KB/s"  if speed else "—")
                send_progress(pct, spd,
                    f"{fmt_bytes(done)} / {fmt_bytes(total)}" if total else fmt_bytes(done))
            elif st == "finished":
                send_message({"type": "merging"})
        except Exception as e:
            log.error(f"hook: {e}")
    return hook

# ══════════════════════════════════════════════════════════════════════════════
#  Browse folder (Windows PowerShell dialog)
# ══════════════════════════════════════════════════════════════════════════════
def browse_folder():
    try:
        import subprocess
        ps = (
            'Add-Type -AssemblyName System.Windows.Forms;'
            '$f=New-Object System.Windows.Forms.FolderBrowserDialog;'
            '$f.Description="اختر مجلد الحفظ";'
            '$f.ShowNewFolderButton=$true;'
            'if($f.ShowDialog() -eq "OK"){$f.SelectedPath}else{"CANCELLED"}'
        )
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        r = subprocess.run(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", ps],
            capture_output=True, text=True, timeout=60,
            startupinfo=si
        )
        path = r.stdout.strip()
        return path if path and path != "CANCELLED" else None
    except Exception as e:
        log.error(f"browse_folder: {e}")
        return None

# ══════════════════════════════════════════════════════════════════════════════
#  HTTP helper
# ══════════════════════════════════════════════════════════════════════════════
def http_get(url, cookies_str="", referer="", timeout=20):
    import urllib.request, ssl, gzip
    from urllib.parse import urlparse
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    p      = urlparse(url)
    origin = f"{p.scheme}://{p.netloc}"
    ref    = referer or origin + "/"
    hdrs = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,*/*;q=0.8",
        "Accept-Language": "ar-EG,ar;q=0.9,en-US;q=0.8",
        "Accept-Encoding": "gzip, deflate",
        "Referer":         ref,
        "Origin":          origin,
        "Connection":      "keep-alive",
    }
    if cookies_str:
        hdrs["Cookie"] = cookies_str
    try:
        req = urllib.request.Request(url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            final = r.geturl()
            raw   = r.read()
            enc   = r.headers.get("Content-Encoding", "")
            if "gzip" in enc:
                try: raw = gzip.decompress(raw)
                except Exception: pass
            html = raw.decode("utf-8", errors="ignore")
        return html, final
    except Exception as e:
        log.error(f"http_get({url[:80]}): {e}")
        return None, None

# ══════════════════════════════════════════════════════════════════════════════
#  Cookies loader
# ══════════════════════════════════════════════════════════════════════════════
def load_cookies(cookies_file, domain):
    result = []
    try:
        with open(cookies_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"): continue
                parts = line.split("\t")
                if len(parts) < 7: continue
                cd = parts[0].lstrip(".")
                if domain.endswith(cd) or cd.endswith(domain):
                    result.append(f"{parts[5]}={parts[6]}")
    except Exception as e:
        log.warning(f"load_cookies: {e}")
    return "; ".join(result)

# ══════════════════════════════════════════════════════════════════════════════
#  Find video URLs in HTML (regex extraction)
# ══════════════════════════════════════════════════════════════════════════════
def find_video_urls(html, base_url=""):
    from urllib.parse import urlparse, urljoin
    p = urlparse(base_url)
    found = []
    patterns = [
        r'<(?:video|source)[^>]+\bsrc=["\']([^"\']+\.(?:mp4|m3u8|webm|mov)[^"\']*)["\']',
        r'\bfile\s*:\s*["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']',
        r'\bsrc\s*:\s*["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']',
        r'"(?:url|src|file|videoUrl|video_url|hd_src|sd_src|playable_url|hls_url|dash_manifest_url)"\s*:\s*"([^"]+\.(?:mp4|m3u8|webm|mpd)[^"]*)"',
        r"'(?:url|src|file)'\s*:\s*'([^']+\.(?:mp4|m3u8|webm)[^']*)'",
        r'data-(?:src|video-src|hd-src|sd-src)=["\']([^"\']+\.(?:mp4|m3u8|webm)[^"\']*)["\']',
        r'["\']([^"\']{10,}\.mp4[^"\'?#]{0,100})["\']',
        r'["\']([^"\']{10,}\.m3u8[^"\'?#]{0,100})["\']',
        r'["\']([^"\']{10,}\.mpd[^"\'?#]{0,100})["\']',
    ]
    for pat in patterns:
        for m in re.finditer(pat, html, re.IGNORECASE):
            u = m.group(1).strip().replace("\\u0026", "&").replace("\\/", "/")
            if u.startswith("//"): u = p.scheme + ":" + u
            elif u.startswith("/"): u = urljoin(base_url, u)
            elif not u.startswith("http"): u = urljoin(base_url, u)
            if u.startswith("http") and u not in found:
                found.append(u)
    return found

# ══════════════════════════════════════════════════════════════════════════════
#  Facebook URL fixer — handles incomplete /reel/ URLs
# ══════════════════════════════════════════════════════════════════════════════
def fix_facebook_url(url, cookies_file):
    from urllib.parse import urlparse
    p = urlparse(url)
    parts = [x for x in p.path.split("/") if x]
    need_fix = (
        (len(parts) == 1 and parts[0] in ("reel", "videos", "watch")) or
        len(parts) == 0
    )
    if not need_fix:
        return url
    log.info(f"Facebook URL incomplete ({url}), fetching real URL from page...")
    ck = load_cookies(cookies_file, "facebook.com") if os.path.exists(cookies_file) else ""
    html, final = http_get(url, cookies_str=ck, timeout=15)
    if not html:
        return url
    m = re.search(r'<meta[^>]+property=["\']og:url["\'][^>]+content=["\']([^"\']+)["\']', html)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:url["\']', html)
    if m:
        real = m.group(1).replace("&amp;", "&")
        log.info(f"Facebook real URL: {real}")
        return real
    return final or url

# ══════════════════════════════════════════════════════════════════════════════
#  Quality / format selection helpers
# ══════════════════════════════════════════════════════════════════════════════
def build_format_string(height, is_audio):
    """
    Build yt-dlp format string with intelligent fallback chain.
    Codec preference: avc1/h264 > vp9 > any
    Quality: exact match → closest lower → best available
    """
    if is_audio:
        return "bestaudio[ext=m4a]/bestaudio/best"

    # Try exact height with preferred codecs, then fall back gracefully
    fmt = (
        f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio/"
        f"bestvideo[height<={height}][vcodec^=vp9]+bestaudio[ext=m4a]/"
        f"bestvideo[height<={height}][vcodec^=vp9]+bestaudio/"
        f"bestvideo[height<={height}]+bestaudio/"
        f"bestvideo+bestaudio/"
        f"best[height<={height}]/"
        f"best"
    )
    return fmt

def build_format_sort(height):
    """format_sort ensures closest resolution is picked first."""
    return [f"res:{height}", "codec:avc1:vp9", "fps", "tbr"]

# ══════════════════════════════════════════════════════════════════════════════
#  YouTube download — android client + format_sort
# ══════════════════════════════════════════════════════════════════════════════
def download_youtube(url, height, is_audio, save_path, cookies_file):
    """
    YouTube download with intelligent multi-client fallback.
    Client order: ios → tv_embedded → web → android
    Format: exact height → closest lower → best available
    """
    import yt_dlp

    if is_audio:
        fmt = "bestaudio[ext=m4a]/bestaudio/best"
    else:
        fmt = (
            f"bestvideo[height={height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height={height}][vcodec^=avc1]+bestaudio/"
            f"bestvideo[height={height}]+bestaudio/"
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio[ext=m4a]/"
            f"bestvideo[height<={height}][vcodec^=avc1]+bestaudio/"
            f"bestvideo[height<={height}][vcodec^=vp9]+bestaudio/"
            f"bestvideo[height<={height}]+bestaudio/"
            f"best[height<={height}]/"
            f"bestvideo+bestaudio/"
            f"best"
        )

    base_opts = {
        "format":              fmt,
        "format_sort":         [f"res:{height}", "codec:avc1:vp9", "fps:60", "tbr"],
        "merge_output_format": "mp4" if not is_audio else None,
        "outtmpl":             os.path.join(save_path, "%(title)s.%(ext)s"),
        "progress_hooks":      [make_hook()],
        "quiet":               True,
        "no_warnings":         True,
        "nocheckcertificate":  True,
        "noprogress":          True,
        "overwrites":          True,
        "retries":             8,
        "fragment_retries":    8,
        "noplaylist":          True,
        "socket_timeout":      30,
    }

    if FFMPEG_PATH:
        base_opts["ffmpeg_location"] = FFMPEG_PATH
    else:
        log.warning("YouTube: ffmpeg not found — single-file format")
        base_opts["format"] = (
            f"best[height<={height}]/best" if not is_audio else "bestaudio/best"
        )

    if is_audio and FFMPEG_PATH:
        base_opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    if cookies_file and os.path.exists(cookies_file):
        base_opts["cookiefile"] = cookies_file

    # Multi-client fallback — ios/tv_embedded أولاً لأنهم بيرجعوا أعلى جودة
    clients_order = [
        ["ios"],
        ["tv_embedded"],
        ["web"],
        ["ios", "web"],
        ["android"],
        ["android", "web"],
    ]

    last_err = ""
    for clients in clients_order:
        client_str = "+".join(clients)
        opts = dict(base_opts)
        opts["extractor_args"] = {"youtube": {"player_client": clients}}

        log.info(f"YouTube: trying client={client_str}")
        send_progress(-1, f"جاري التحضير ({client_str})...", "")

        try:
            global _active_ydl
            with yt_dlp.YoutubeDL(opts) as ydl:
                with _active_ydl_lock:
                    _active_ydl = ydl
                info = ydl.extract_info(url, download=True)
                if info and info.get("_type") == "playlist":
                    info = (info.get("entries") or [info])[0]

                fname     = ydl.prepare_filename(info)
                actual_h  = info.get("height")
                actual_vc = info.get("vcodec", "?")
                actual_fmt= info.get("format_id", "?")

                if not is_audio:
                    fname = os.path.splitext(fname)[0] + ".mp4"

            log.info(
                f"YouTube OK: client={client_str} "
                f"fmt={actual_fmt} res={actual_h}p codec={actual_vc}"
            )

            # لو الجودة أقل من 60% من المطلوب، جرب client تاني
            if (not is_audio and actual_h and isinstance(actual_h, int)
                    and actual_h < max(360, height * 0.75)
                    and client_str not in ("android+web", "android")):
                log.warning(
                    f"YouTube: got {actual_h}p but requested {height}p "
                    f"(client={client_str}) — trying next client"
                )
                try:
                    full = os.path.join(save_path, os.path.basename(fname))
                    if os.path.exists(full):
                        os.remove(full)
                except Exception:
                    pass
                last_err = f"client {client_str} returned {actual_h}p"
                continue

            return os.path.basename(fname), None

        except Exception as e:
            err_str = str(e)
            log.warning(f"YouTube client={client_str} failed: {err_str[:100]}")
            last_err = err_str
            err_low = err_str.lower()
            if not any(k in err_low for k in [
                "requested format", "not available", "no video formats",
                "formats", "unavailable"
            ]):
                break
            continue
        finally:
            with _active_ydl_lock:
                _active_ydl = None

    log.error(f"YouTube: all clients failed. last_err={last_err[:150]}")
    return None, last_err

def download_facebook(url, height, is_audio, save_path, cookies_file):
    import yt_dlp

    url = fix_facebook_url(url, cookies_file)
    log.info(f"Facebook download URL: {url}")

    fmt = build_format_string(height, is_audio)

    opts = {
        "format":              fmt,
        "format_sort":         build_format_sort(height),
        "merge_output_format": "mp4" if not is_audio else None,
        "outtmpl":             os.path.join(save_path, "%(title)s.%(ext)s"),
        "progress_hooks":      [make_hook()],
        "quiet":               True,
        "no_warnings":         True,
        "nocheckcertificate":  True,
        "noprogress":          True,
        "overwrites":          True,
        "retries":             5,
        "fragment_retries":    5,
        "noplaylist":          True,
        "socket_timeout":      30,
    }

    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH
    else:
        opts["format"] = f"best[height<={height}]/best" if not is_audio else "bestaudio/best"

    if is_audio and FFMPEG_PATH:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    def _try_download(extra_opts):
        o = dict(opts)
        o.update(extra_opts)
        try:
            with yt_dlp.YoutubeDL(o) as ydl:
                with _active_ydl_lock:
                    global _active_ydl
                    _active_ydl = ydl
                info  = ydl.extract_info(url, download=True)
                if info and info.get("_type") == "playlist":
                    info = (info.get("entries") or [info])[0]
                fname = ydl.prepare_filename(info)
                if not is_audio:
                    fname = os.path.splitext(fname)[0] + ".mp4"
            return os.path.basename(fname), None
        except Exception as e:
            return None, str(e)
        finally:
            with _active_ydl_lock:
                _active_ydl = None

    # Attempt 1: with cookies
    if cookies_file and os.path.exists(cookies_file):
        ck_str = load_cookies(cookies_file, "facebook.com")
        if ck_str:
            log.info("Facebook: trying with cookies.txt")
            send_progress(-1, "جاري تسجيل الدخول بـ cookies...", "")
            fname, err = _try_download({"cookiefile": cookies_file})
            if fname:
                log.info(f"Facebook OK (cookies.txt): {fname}")
                return fname, None
            log.warning(f"Facebook cookies.txt failed: {(err or '')[:100]}")

    # Attempt 2: without cookies (public videos)
    log.info("Facebook: trying without cookies (public)")
    send_progress(-1, "جاري المحاولة بدون تسجيل دخول...", "")
    fname, err = _try_download({})
    if fname:
        log.info(f"Facebook OK (no cookies): {fname}")
        return fname, None

    log.error(f"Facebook failed: {err}")
    return None, err

# ══════════════════════════════════════════════════════════════════════════════
#  URL validator — reject CAPTCHAs, tracking pixels, images, scripts, ads
# ══════════════════════════════════════════════════════════════════════════════
_SKIP_URL_PATTERNS = [
    "captcha", "securimage", "recaptcha", "hcaptcha",
    "tracking", "analytics", "pixel", "beacon", "telemetry",
    "doubleclick", "googlesyndication", "adservice", "googleads",
    "/ads/", "/ad/", "adserver", "prebid", "adroll",
    "taboola", "outbrain", "disqus", "gravatar", "avatar",
    "facebook.com/plugins", "twitter.com/widgets",
    "connect.facebook.net", "platform.instagram.com",
    "gtag", "ga.js", "analytics.js", "hotjar", "clarity.ms",
]
_SKIP_URL_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot",
    ".pdf", ".zip", ".rar", ".txt",
)
_VIDEO_EXTENSIONS = (".mp4", ".m3u8", ".mpd", ".webm", ".mov", ".flv", ".mkv")
_VIDEO_PATH_HINTS = ("stream", "manifest", "playlist", "video", "media",
                     "hls", "dash", "chunk", "segment", "master", "play")

def is_valid_video_url(url):
    """
    Returns True only if a URL looks like a real video resource.
    Rejects CAPTCHAs, tracking pixels, images, scripts, ads, etc.
    """
    if not url or not isinstance(url, str) or not url.startswith("http"):
        return False
    url_lower = url.lower()
    if any(p in url_lower for p in _SKIP_URL_PATTERNS):
        log.debug(f"URL rejected (bad pattern): {url[:80]}")
        return False
    path = url_lower.split("?")[0]
    if any(path.endswith(ext) for ext in _SKIP_URL_EXTENSIONS):
        log.debug(f"URL rejected (bad extension): {url[:80]}")
        return False
    if any(path.endswith(ext) or (ext + "?") in url_lower for ext in _VIDEO_EXTENSIONS):
        return True
    path_filename = path.rsplit("/", 1)[-1]
    if any(hint in path_filename for hint in _VIDEO_PATH_HINTS):
        return True
    log.debug(f"URL rejected (no video signal): {url[:80]}")
    return False


# ══════════════════════════════════════════════════════════════════════════════
#  Generic yt-dlp download — used for Twitter, Instagram, TikTok, and unknowns
#  Strategy: try WITHOUT impersonate first, then retry with impersonate=chrome
#  as a Cloudflare-bypass fallback only when needed.
# ══════════════════════════════════════════════════════════════════════════════
def download_generic(url, height, is_audio, save_path, cookies_file, referer="",
                     use_impersonate=True):
    import yt_dlp

    fmt = build_format_string(height, is_audio)

    # تجهيز هيدرز احترافية ومطابقة لمتصفح كروم حقيقي 100% لتفادي كابتشا الحماية
    from urllib.parse import urlparse as _up2
    _rp2 = _up2(url)
    custom_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9,ar;q=0.8",
        "Referer": referer or f"{_rp2.scheme}://{_rp2.netloc}/",
        "Origin": f"{_rp2.scheme}://{_rp2.netloc}",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "cross-site",
        "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
    }

    opts = {
        "format":              fmt,
        "format_sort":         build_format_sort(height),
        "merge_output_format": "mp4" if not is_audio else None,
        "outtmpl":             os.path.join(save_path, "%(title)s.%(ext)s"),
        "progress_hooks":      [make_hook()],
        "quiet":               True,
        "no_warnings":         True,
        "nocheckcertificate":  True,
        "noprogress":          True,
        "overwrites":          True,
        "retries":             10,
        "fragment_retries":    10,
        "noplaylist":          True,
        "socket_timeout":      30,
        "http_chunk_size":     10485760,
        "http_headers":        custom_headers,
    }

    # Impersonation — بس للمواقع اللي محتاجاها (generic/Cloudflare)
    # dailymotion وغيره من المواقع المعروفة: لا تستخدم impersonate أصلاً
    _is_known_site = any(d in url for d in [
        "dailymotion.com", "vimeo.com", "twitch.tv", "streamable.com",
        "twitter.com", "x.com", "instagram.com", "tiktok.com",
    ])
    if use_impersonate and not _is_known_site:
        # impersonate=True: yt-dlp يختار تلقائياً اللي متاح (chrome أو غيره)
        opts["impersonate"] = True
        log.info(f"Generic Attempt (impersonate=auto): {url[:70]}")
    else:
        log.info(f"Generic Attempt (standard): {url[:70]}")

    if FFMPEG_PATH:
        opts["ffmpeg_location"] = FFMPEG_PATH
    else:
        opts["format"] = f"best[height<={height}]/best" if not is_audio else "bestaudio/best"

    if is_audio and FFMPEG_PATH:
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    if cookies_file and os.path.exists(cookies_file):
        opts["cookiefile"] = cookies_file

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            with _active_ydl_lock:
                global _active_ydl
                _active_ydl = ydl
            info  = ydl.extract_info(url, download=True)
            if info and info.get("_type") == "playlist":
                info = (info.get("entries") or [info])[0]
            fname = ydl.prepare_filename(info)
            if not is_audio:
                fname = os.path.splitext(fname)[0] + ".mp4"
        log.info(f"Generic OK: {fname}")
        return os.path.basename(fname), None
    except Exception as e:
        log.error(f"Generic failed ({url[:60]}): {e}")
        return None, str(e)
    finally:
        with _active_ydl_lock:
            _active_ydl = None


def download_generic_with_fallback(url, height, is_audio, save_path, cookies_file,
                                   referer=""):
    # المحاولة الأولى: باستخدام محاكاة المتصفح الافتراضية
    fname, err = download_generic(url, height, is_audio, save_path, cookies_file,
                                  referer=referer, use_impersonate=True)
    if fname:
        return fname, None

    err_low = (err or "").lower()
    
    # تحكم ذكي: لو الخطأ بسبب نقص حزم المحاكاة (مثل خطأ firefox المزعج) أو حظر الـ Cloudflare 403
    if "impersonate" in err_low or "available" in err_low or "403" in err_low or "cloudflare" in err_low:
        log.info("Switching instantly to heavy-headers clean download bypass...")
        # المحاولة الثانية: التحميل النظيف مع الهيدرز المعززة (تخطي آمن وبدون كراش)
        fname2, err2 = download_generic(url, height, is_audio, save_path, cookies_file,
                                        referer=referer, use_impersonate=False)
        if fname2:
            return fname2, None
            
        # المحاولة الثالثة والنهائية: إذا كان الرابط مباشر لملف فيديو، اسحبه بمكتبة الشبكة القياسية فوراً
        if any(x in url.lower() for x in [".mp4", ".m3u8", ".mov", ".webm", "stream"]):
            log.info("Target looks like a raw stream URL, invoking direct urllib pull...")
            try:
                urllib_download(url, save_path, referer=referer)
                return "DIRECT_DOWNLOAD_OK", None
            except Exception as ue:
                return None, f"Urllib fallback failed: {ue}"
                
        return None, err2
        
    return None, err
# ══════════════════════════════════════════════════════════════════════════════
#  Direct HTTP download fallback (for raw mp4/m3u8 URLs)
# ══════════════════════════════════════════════════════════════════════════════
def urllib_download(video_url, save_path, referer=""):
    import urllib.request, ssl
    path_part = video_url.split("?")[0]
    if "." in path_part:
        ext = path_part.rsplit(".", 1)[-1][:5]
    else:
        ext = "mp4"
    name = f"video_{int(time.time())}.{ext}"
    dest = os.path.join(save_path, name)
    ctx  = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode    = ssl.CERT_NONE
    hdrs = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124",
        "Referer":    referer or video_url,
    }
    try:
        req = urllib.request.Request(video_url, headers=hdrs)
        with urllib.request.urlopen(req, timeout=120, context=ctx) as r:
            total = int(r.headers.get("Content-Length", 0))
            done  = 0
            with open(dest, "wb") as f:
                while True:
                    if _download_event.is_set():
                        raise Exception("Download cancelled")
                    chunk = r.read(524288)
                    if not chunk: break
                    f.write(chunk)
                    done += len(chunk)
                    pct  = int(done/total*100) if total else -1
                    send_progress(pct, "", fmt_bytes(done))
        send_message({"type": "done", "filename": name})
    except Exception as e:
        log.error(f"urllib_download: {e}")
        send_message({"type": "error", "error": f"فشل التحميل المباشر: {str(e)[:150]}"})

# ══════════════════════════════════════════════════════════════════════════════
#  Main download dispatcher
# ══════════════════════════════════════════════════════════════════════════════
def do_download(msg):
    _download_event.clear()  # Reset cancellation flag

    url              = msg.get("url", "").strip()
    quality          = str(msg.get("quality", "1080"))
    dl_type          = msg.get("dlType", "video")
    save_path        = msg.get("savePath", "").strip() or os.path.expanduser("~/Downloads")
    intercepted_urls = msg.get("interceptedUrls", [])
    is_audio         = (dl_type == "audio" or quality == "0")
    height           = int(quality) if quality.isdigit() and int(quality) > 0 else 1080

    from urllib.parse import urlparse
    parsed = urlparse(url)
    host   = (parsed.hostname or "").lower().replace("www.", "").replace("m.", "")

    log.info(f"=== Download: host={host} q={quality} url={url[:80]} intercepted={len(intercepted_urls)}")

    try:
        os.makedirs(save_path, exist_ok=True)
    except Exception as e:
        send_message({"type": "error", "error": f"فشل إنشاء المجلد: {e}"}); return

    cookies_file = os.path.join(_dir, "cookies.txt")

    try:
        # ── YouTube ────────────────────────────────────────────────────────
        if host in ("youtube.com", "youtu.be"):
            fname, err = download_youtube(url, height, is_audio, save_path, cookies_file)
            if fname:
                send_message({"type": "done", "filename": fname})
            else:
                send_message({"type": "error",
                    "error": f"⚠ فشل التحميل\n{(err or '')[:150]}"})
            return

        # ── Facebook ───────────────────────────────────────────────────────
        if "facebook.com" in host or host == "fb.watch":
            fname, err = download_facebook(url, height, is_audio, save_path, cookies_file)
            if fname:
                send_message({"type": "done", "filename": fname})
            else:
                err_low = (err or "").lower()
                if "login" in err_low or "checkpoint" in err_low:
                    tip = "⚠ الفيديو خاص — تأكد إن cookies.txt محدّث"
                elif "unsupported url" in err_low:
                    tip = "⚠ الرابط مش مدعوم — تأكد إنه كامل"
                else:
                    tip = f"⚠ فشل تحميل فيسبوك\n{(err or '')[:150]}"
                send_message({"type": "error", "error": tip})
            return

        # ── Twitter/X, Instagram, TikTok, Vimeo, Dailymotion, Rumble ──────────────
        if host in ("twitter.com", "x.com", "instagram.com", "tiktok.com",
                    "vimeo.com", "dailymotion.com", "twitch.tv", "streamable.com",
                    "reddit.com", "bilibili.com", "rumble.com"):
            fname, err = download_generic_with_fallback(url, height, is_audio,
                                                         save_path, cookies_file)
            if fname:
                send_message({"type": "done", "filename": fname})
            else:
                _err_short = (err or "")[:150]
                send_message({"type": "error", "error": f"⚠ فشل التحميل\n{_err_short}"})
            return

        # ══════════════════════════════════════════════════════════════════
        #  Unknown site — multi-step extraction
        # ══════════════════════════════════════════════════════════════════
        log.info(f"Unknown site: {host}")
        send_progress(-1, "جاري المحاولة...", "")

        # Step 1: Try intercepted URLs from browser (m3u8/mp4 caught by content.js)
        # Filter out CAPTCHAs, tracking pixels, images, and other non-video URLs
        valid_intercepted = [u for u in intercepted_urls if is_valid_video_url(u)]
        log.info(f"Intercepted: {len(intercepted_urls)} total, {len(valid_intercepted)} valid")

        for iurl in valid_intercepted:
            log.info(f"Trying intercepted URL: {iurl[:80]}")
            send_progress(-1, "جاري تحميل الرابط المعترض...", "")

            # HLS/DASH streams — yt-dlp handles these natively
            if any(x in iurl.lower() for x in [".m3u8", ".mpd", "manifest"]):
                fname, err = download_generic(iurl, height, is_audio, save_path,
                                              cookies_file, referer=url)
                if fname:
                    send_message({"type": "done", "filename": fname})
                    return
            else:
                # Direct mp4/webm — try yt-dlp, fall back to urllib
                fname, err = download_generic(iurl, height, is_audio, save_path,
                                              cookies_file, referer=url)
                if fname:
                    send_message({"type": "done", "filename": fname})
                    return
                # urllib direct download as last resort for raw video files
                urllib_download(iurl, save_path, referer=url)
                return

        # Step 2: Try yt-dlp on the original URL (without impersonate first, then fallback)
        fname, last_err = download_generic_with_fallback(url, height, is_audio,
                                                          save_path, cookies_file)
        if fname:
            send_message({"type": "done", "filename": fname})
            return

        # Step 3: Try player page variants (play.php / embed.php / watch.php)
        from urllib.parse import parse_qs, urlunparse, urljoin as _uj
        _pp  = parsed
        _qs  = parse_qs(_pp.query)
        _vid = (_qs.get("vid") or _qs.get("id") or _qs.get("v") or [""])[0]

        player_pages = []
        if _vid:
            for php in ("play.php", "embed.php", "watch.php", "player.php"):
                candidate = urlunparse((_pp.scheme, _pp.netloc, f"/{php}", "", f"vid={_vid}", ""))
                if candidate != url:
                    player_pages.append(candidate)

        for candidate in player_pages:
            log.info(f"Trying player URL: {candidate}")
            fname2, err2 = download_generic_with_fallback(candidate, height, is_audio,
                                                           save_path, cookies_file)
            if fname2:
                send_message({"type": "done", "filename": fname2})
                return

        # Step 4: Fetch page HTML and extract video URLs
        pages_to_check = [url] + player_pages
        _origin = f"{_pp.scheme}://{_pp.netloc}"
        skip_patterns = ["ads", "banner", "doubleclick", "googlesyndication",
                         "analytics", "gtag", "facebook.com/plugins",
                         "twitter.com/widgets", "disqus"]

        # امتدادات مش فيديو — تجاهلها حتى لو اتبعتها كـ iframe
        skip_extensions = (".js", ".css", ".png", ".jpg", ".gif", ".svg",
                           ".woff", ".woff2", ".ttf", ".ico")

        for page in pages_to_check:
            send_progress(-1, "جاري قراءة صفحة الموقع...", "")
            _ck = load_cookies(cookies_file, _pp.hostname or "") if os.path.exists(cookies_file) else ""
            html, _ = http_get(page, cookies_str=_ck, referer=_origin + "/", timeout=20)
            if not html:
                continue

            # 4a: Direct video URLs in HTML
            direct = find_video_urls(html, page)
            if direct:
                log.info(f"Direct URL in HTML: {direct[0][:80]}")
                send_progress(-1, "تم العثور على الفيديو...", "")
                fname3, _ = download_generic_with_fallback(direct[0], height, is_audio, save_path, cookies_file, referer=page)
                if fname3:
                    send_message({"type": "done", "filename": fname3})
                    return
                urllib_download(direct[0], save_path, referer=page)
                return

            # 4b: iframes
            iframes_found = re.findall(
                r'<iframe[^>]+\ Guest\ src=["\']([^"\']{8,})["\']', html, re.IGNORECASE)
            iframes_found += re.findall(
                r'(?:iframe[^"\']*src|src)\s*[=:]\s*["\']'
                r'(https?://[^"\']{8,})["\']', html, re.IGNORECASE)

            for raw_src in iframes_found:
                raw_src = raw_src.strip()
                if raw_src.startswith("//"): raw_src = _pp.scheme + ":" + raw_src
                elif not raw_src.startswith("http"): raw_src = _uj(page, raw_src)
                if any(s in raw_src for s in skip_patterns): continue
                # تجاهل JS/CSS/صور — مش iframes فيديو
                _raw_path = raw_src.split("?")[0].lower()
                if any(_raw_path.endswith(ext) for ext in skip_extensions): continue

                log.info(f"iframe: {raw_src[:80]}")
                send_progress(-1, "جاري فحص الـ embed...", "")

                # Try yt-dlp on iframe src (with Cloudflare fallback)
                fname_if, _ = download_generic_with_fallback(raw_src, height, is_audio,
                                                              save_path, cookies_file, referer=page)
                if fname_if:
                    send_message({"type": "done", "filename": fname_if})
                    return

                # Fetch iframe HTML and search it
                _if_ck = load_cookies(cookies_file, urlparse(raw_src).hostname or "") \
                         if os.path.exists(cookies_file) else ""
                if_html, _ = http_get(raw_src, cookies_str=_if_ck, referer=page, timeout=15)
                if not if_html:
                    continue

                iframe_direct = find_video_urls(if_html, raw_src)
                if iframe_direct:
                    log.info(f"Direct URL in iframe HTML: {iframe_direct[0][:80]}")
                    send_progress(-1, "تم العثور على الفيديو...", "")
                    fname4, _ = download_generic(iframe_direct[0], height, is_audio, save_path, cookies_file, referer=raw_src)
                    if fname4:
                        send_message({"type": "done", "filename": fname4})
                        return
                    urllib_download(iframe_direct[0], save_path, referer=raw_src)
                    return

                # Nested iframes (level 2)
                nested = re.findall(
                    r'<iframe[^>]+\bsrc=["\']([^"\']{8,})["\']', if_html, re.IGNORECASE)
                for n_src in nested:
                    if n_src.startswith("//"): n_src = _pp.scheme + ":" + n_src
                    elif not n_src.startswith("http"): n_src = _uj(raw_src, n_src)
                    if any(s in n_src for s in skip_patterns): continue
                    log.info(f"nested iframe: {n_src[:80]}")
                    fname_n, _ = download_generic_with_fallback(n_src, height, is_audio, save_path, cookies_file)
                    if fname_n:
                        send_message({"type": "done", "filename": fname_n})
                        return

        # All attempts failed
        send_message({"type": "error",
            "error": f"⚠ الموقع ({host}) مش مدعوم أو الفيديو محمي\n"
                     f"الخطأ: {(last_err or '')[:120]}"})

    except Exception as e:
        log.critical(f"do_download crash: {traceback.format_exc()}")
        send_message({"type": "error", "error": f"خطأ غير متوقع: {str(e)[:150]}"})

# ══════════════════════════════════════════════════════════════════════════════
#  Extension ID auto-update — لما الـ extension تبعت ID-ها
# ══════════════════════════════════════════════════════════════════════════════
def _update_extension_id(ext_id: str):
    """
    يحدّث الـ manifest بالـ Extension ID الحقيقي ويعيد تسجيل الـ Registry.
    بيشتغل في الخلفية — آمن تماماً.
    """
    try:
        _json = os.path.join(_dir, "com.ytdl.pro.json")
        _bat  = os.path.join(_dir, "ytdl_host.bat")
        _exe  = os.path.join(_dir, "ytdl_host.exe")

        host_path = _exe if os.path.isfile(_exe) else _bat

        # افحص لو الـ ID هو نفسه الموجود أصلاً — مش محتاج تحديث
        if os.path.isfile(_json):
            try:
                with open(_json, encoding="utf-8") as f:
                    d = json.load(f)
                current_origins = d.get("allowed_origins", [])
                expected = f"chrome-extension://{ext_id}/"
                if expected in current_origins:
                    log.debug(f"Extension ID already up-to-date: {ext_id}")
                    return
            except Exception:
                pass

        # اكتب manifest محدّث
        manifest_data = {
            "name":        "com.ytdl.pro",
            "description": "YT Downloader Pro Native Host",
            "path":        host_path,
            "type":        "stdio",
            "allowed_origins": [f"chrome-extension://{ext_id}/"]
        }
        with open(_json, "w", encoding="utf-8") as f:
            json.dump(manifest_data, f, indent=2, ensure_ascii=False)

        # حدّث Registry
        try:
            import winreg
            _reg_key = r"Software\Google\Chrome\NativeMessagingHosts\com.ytdl.pro"
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, _reg_key) as k:
                winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _json)
        except ImportError:
            pass

        log.info(f"Extension ID updated: {ext_id}")
        send_message({"type": "registered", "extension_id": ext_id})

    except Exception as e:
        log.warning(f"_update_extension_id: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  Main loop
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log.info("ytdl_host v18 started")
    while True:
        msg = read_message()
        if msg is None:
            log.info("EOF — shutting down")
            break

        t = msg.get("type")
        log.debug(f"got: {t}")

        if t == "ping":
            send_message({"type": "pong"})

        elif t == "download":
            threading.Thread(
                target=do_download, args=(msg,), daemon=True, name="downloader"
            ).start()

        elif t == "cancel":
            log.info("Cancel requested")
            _download_event.set()
            with _active_ydl_lock:
                if _active_ydl:
                    try: _active_ydl._download_retcode = 1
                    except Exception: pass
            send_message({"type": "cancelled"})

        elif t == "browse_folder":
            def _browse():
                path = browse_folder()
                send_message({"type": "folder_selected", "path": path} if path
                             else {"type": "folder_cancelled"})
            threading.Thread(target=_browse, daemon=True, name="browse").start()

        elif t == "register_extension":
            # الـ extension بتبعت ID-ها — حدّث الـ manifest أوتوماتيك
            ext_id = msg.get("extension_id", "").strip()
            if ext_id and ext_id != "imfkhbibjnlddbkkmlokmpddkhkpdiid":
                _update_extension_id(ext_id)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        log.info("KeyboardInterrupt — exiting")
    except Exception:
        log.critical(traceback.format_exc())