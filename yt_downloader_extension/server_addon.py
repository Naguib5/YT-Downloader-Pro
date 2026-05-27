"""
server_addon.py v4 — YT Downloader Pro
────────────────────────────────────────
دمج هذا الملف مع yt_downloader_pro.py:
  1. انسخ الـ imports للأعلى
  2. انسخ باقي الكود قبل سطر  root = tk.Tk()
  3. اضف سطر واحد بعد root = tk.Tk():
       threading.Thread(target=_start_server, daemon=True).start()
"""

# ═══ IMPORTS — أضفها مع بقية الـ imports ══════════════════════
from http.server   import HTTPServer, BaseHTTPRequestHandler
from urllib.parse  import urlparse, parse_qs
import json        as _json
import uuid        as _uuid
import threading   as _threading


# ═══ JOB STORE — أضفه قبل root = tk.Tk() ═════════════════════
# قاموس يخزن كل job تحميل نشط
# { job_id: { status, pct, speed, size, filename, error } }
_jobs: dict = {}
_jobs_lock  = _threading.Lock()

def _new_job() -> str:
    jid = str(_uuid.uuid4())[:8]
    with _jobs_lock:
        _jobs[jid] = {"status": "starting", "pct": 0,
                       "speed": "", "size": "", "filename": "", "error": ""}
    return jid

def _update_job(jid, **kw):
    with _jobs_lock:
        if jid in _jobs:
            _jobs[jid].update(kw)

def _get_job(jid):
    with _jobs_lock:
        return dict(_jobs.get(jid, {"status": "not_found"}))


# ═══ DOWNLOAD WORKER ══════════════════════════════════════════
def _download_worker(jid: str, url: str, quality: str,
                     type_: str, save_path: str):
    """
    ينفذ التحميل في thread مستقل ويحدّث حالة الـ job.
    quality: "1080","720","480","360","240","2160" أو "0" للصوت
    type_: "video" | "audio"
    """
    import yt_dlp, os

    quality = quality.strip()
    is_audio = (type_ == "audio" or quality == "0")

    if is_audio:
        fmt = "bestaudio[acodec^=mp4a]/bestaudio"
    else:
        h = int(quality) if quality.isdigit() else 1080
        fmt = (
            f"bestvideo[height<={h}][vcodec^=avc1]+bestaudio[acodec^=mp4a]"
            f"/bestvideo[height<={h}]+bestaudio"
            f"/best[height<={h}]"
            f"/bestvideo+bestaudio/best"
        )

    actual_filename = [""]

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done  = d.get("downloaded_bytes", 0)
            speed = d.get("speed") or 0
            pct   = int(done / total * 100) if total else -1

            speed_str = (f"{speed/1024/1024:.1f} MB/s" if speed >= 1024*1024
                         else f"{speed/1024:.0f} KB/s"  if speed else "—")
            size_str  = (f"{fmt_bytes(done)} / {fmt_bytes(total)}" if total
                         else fmt_bytes(done))

            _update_job(jid, status="downloading", pct=pct,
                        speed=speed_str, size=size_str)

        elif d["status"] == "finished":
            _update_job(jid, status="merging", pct=99)
            fname = d.get("filename") or d.get("info_dict", {}).get("_filename", "")
            if fname:
                actual_filename[0] = os.path.basename(fname)

    ydl_opts = {
        "format":              fmt,
        "outtmpl":             os.path.join(save_path, "%(title)s.%(ext)s"),
        "merge_output_format": "mp4" if not is_audio else None,
        "progress_hooks":      [hook],
        "quiet":               True,
        "no_warnings":         True,
        "nocheckcertificate":  True,
        "extractor_args": {
            "youtube": {"player_client": ["tv_embedded", "web"]}
        },
        **({"postprocessors": [{"key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": "192"}]}
           if is_audio else {}),
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
        fname = actual_filename[0] or "تم التحميل"
        _update_job(jid, status="done", pct=100, filename=fname)
    except Exception as e:
        err = str(e)[:200]
        _update_job(jid, status="error", error=err)


def fmt_bytes(b):
    if b >= 1024**3: return f"{b/1024**3:.1f} GB"
    if b >= 1024**2: return f"{b/1024**2:.1f} MB"
    return f"{b/1024:.0f} KB"


# ═══ HTTP HANDLER ═════════════════════════════════════════════
class _Handler(BaseHTTPRequestHandler):

    def log_message(self, *_): pass   # أخفِ السجلات

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json_response(self, code, obj):
        body = _json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    # ── GET /ping   ── فحص إن الخادم شغال
    # ── GET /progress?job=ID  ── حالة التحميل
    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/ping":
            self.send_response(200)
            self._cors()
            self.end_headers()
            self.wfile.write(b"pong")
            return

        if parsed.path == "/progress":
            jid = parse_qs(parsed.query).get("job", [""])[0]
            self._json_response(200, _get_job(jid))
            return

        self.send_response(404)
        self.end_headers()

    # ── POST /download  ── ابدأ تحميل جديد
    # ── POST /cancel    ── ألغِ آخر تحميل (best-effort)
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        if self.path == "/download":
            try:
                data = _json.loads(body)
                url     = data.get("url", "").strip()
                quality = str(data.get("quality", "1080"))
                type_   = data.get("type", "video")

                if not url:
                    self._json_response(400, {"error": "no url"})
                    return

                # احضر مسار الحفظ من الـ GUI
                save_path = folder_path.get() if folder_path.get() else "."

                jid = _new_job()

                # شغّل التحميل في thread مستقل
                _threading.Thread(
                    target=_download_worker,
                    args=(jid, url, quality, type_, save_path),
                    daemon=True
                ).start()

                self._json_response(200, {"job_id": jid})

            except Exception as e:
                self._json_response(500, {"error": str(e)})
            return

        if self.path == "/cancel":
            # best-effort: مش ممكن نوقف yt-dlp من بره بسهولة
            # بس نحدّث حالة كل الجوبز النشطة
            with _jobs_lock:
                for jid, job in _jobs.items():
                    if job["status"] in ("downloading", "merging", "starting"):
                        _jobs[jid]["status"] = "cancelled"
            self._json_response(200, {"ok": True})
            return

        self.send_response(404)
        self.end_headers()


# ═══ SERVER STARTUP ═══════════════════════════════════════════
def _start_server():
    try:
        srv = HTTPServer(("127.0.0.1", 9999), _Handler)
        srv.serve_forever()
    except OSError:
        pass   # المنفذ مشغول — تجاهل


# ─── أضف هذا السطر بعد  root = tk.Tk()  مباشرة: ──────────────
# threading.Thread(target=_start_server, daemon=True).start()
# ──────────────────────────────────────────────────────────────