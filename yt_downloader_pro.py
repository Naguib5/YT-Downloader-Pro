import yt_dlp
import tkinter as tk
from tkinter import filedialog, ttk
import threading
import os
import shutil
import sys
import json
import subprocess

# ══════════════════════════════════════════════════════════════
#  AUTO SETUP — Native Host + Registry (بيشتغل أول مرة بس)
# ══════════════════════════════════════════════════════════════
def _get_base_dir():
    """مسار الـ exe الحقيقي — مش الـ temp folder بتاع PyInstaller"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))

def setup_native_host():
    """
    يسجّل الـ native host في Registry أوتوماتيك.
    بيشتغل في الخلفية — اليوزر مش هيحس بيه.
    """
    try:
        import winreg
    except ImportError:
        return  # مش Windows

    try:
        base_dir   = _get_base_dir()
        host_dir   = os.path.join(base_dir, "native_host")
        host_bat   = os.path.join(host_dir, "ytdl_host.bat")
        host_exe   = os.path.join(host_dir, "ytdl_host.exe")
        manifest   = os.path.join(host_dir, "com.ytdl.pro.json")

        # ── اختار الـ host المناسب (exe أو py) ──────────────
        if os.path.isfile(host_exe):
            host_path = host_exe
        elif os.path.isfile(host_bat):
            host_path = host_bat
        else:
            # اعمل bat يشير لـ ytdl_host.py بجانب الـ exe
            host_py = os.path.join(host_dir, "ytdl_host.py")
            if not os.path.isfile(host_py):
                return
            os.makedirs(host_dir, exist_ok=True)
            with open(host_bat, "w", encoding="utf-8") as f:
                f.write(f'@echo off\n"{sys.executable}" "{host_py}"\n')
            host_path = host_bat

        # ── افحص لو الـ manifest محتاج تحديث ────────────────
        ext_id = "imfkhbibjnlddbkkmlokmpddkhkpdiid"
        needs_update = True

        if os.path.isfile(manifest):
            try:
                with open(manifest, encoding="utf-8") as f:
                    d = json.load(f)
                if d.get("path", "").lower() == host_path.lower():
                    needs_update = False
                # احتفظ بالـ ID لو موجود
                for origin in d.get("allowed_origins", []):
                    if "chrome-extension://" in origin:
                        saved_id = origin.replace("chrome-extension://", "").strip("/")
                        if saved_id and saved_id != "imfkhbibjnlddbkkmlokmpddkhkpdiid":
                            ext_id = saved_id
            except Exception:
                pass

        if needs_update:
            os.makedirs(host_dir, exist_ok=True)
            manifest_data = {
                "name": "com.ytdl.pro",
                "description": "YT Downloader Pro Native Host",
                "path": host_path,
                "type": "stdio",
                "allowed_origins": [f"chrome-extension://{ext_id}/"]
            }
            with open(manifest, "w", encoding="utf-8") as f:
                json.dump(manifest_data, f, indent=2, ensure_ascii=False)

        # ── سجّل في Registry دايماً بالمسار الصح ────────────
        reg_key = r"Software\Google\Chrome\NativeMessagingHosts\com.ytdl.pro"
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, reg_key) as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, manifest)

    except Exception:
        pass  # فشل التسجيل مش هيوقف البرنامج


def open_chrome_extensions():
    """
    يفتح Chrome على chrome://extensions لو الـ extension لسه متضافتش.
    بيشتغل مرة واحدة بس — بعدين بيسيب Chrome للمستخدم.
    """
    try:
        base_dir   = _get_base_dir()
        marker     = os.path.join(base_dir, "native_host", ".ext_installed")

        # لو اتثبتت قبل كده، مش محتاج
        if os.path.isfile(marker):
            return

        chrome_paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        chrome = next((p for p in chrome_paths if os.path.isfile(p)), None)
        if not chrome:
            return

        ext_folder = os.path.join(base_dir, "yt_downloader_extension")
        if not os.path.isdir(ext_folder):
            return

        # افتح chrome://extensions
        subprocess.Popen([chrome, "chrome://extensions/"],
                         creationflags=0x00000008)  # DETACHED_PROCESS

    except Exception:
        pass


# ── شغّل الـ setup فوراً وبشكل synchronous قبل الـ UI ────────
setup_native_host()
threading.Thread(target=open_chrome_extensions, daemon=True).start()

# ══════════════════════════════════════════════════════════════
#  STATE
# ══════════════════════════════════════════════════════════════
format_map    = {}
playlist_info = []
total_videos  = 0
stop_flag     = False
check_vars    = []

# ══════════════════════════════════════════════════════════════
#  FFMPEG
# ══════════════════════════════════════════════════════════════
def get_ffmpeg():
    local = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ffmpeg.exe")
    if os.path.isfile(local):
        return os.path.dirname(local)
    if shutil.which("ffmpeg"):
        return None
    return "NOT_FOUND"

FFMPEG     = get_ffmpeg()
HAS_FFMPEG = FFMPEG != "NOT_FOUND"

# ══════════════════════════════════════════════════════════════
#  YT-DLP OPTIONS
# ══════════════════════════════════════════════════════════════
def base_opts():
    o = {"quiet": True, "no_warnings": True, "nocheckcertificate": True}
    if HAS_FFMPEG and FFMPEG:
        o["ffmpeg_location"] = FFMPEG
    return o

def download_opts():
    return {**base_opts(), "retries": 15, "fragment_retries": 15,
            "concurrent_fragment_downloads": 4}

# ══════════════════════════════════════════════════════════════
#  COLORS & FONTS
# ══════════════════════════════════════════════════════════════
C_BG      = "#0a0a0f"
C_CARD    = "#111118"
C_CARD2   = "#1a1a24"
C_CARD3   = "#22222e"
C_ACCENT  = "#00d4ff"
C_ACCENT2 = "#0099cc"
C_GREEN   = "#00e676"
C_RED     = "#ff4444"
C_ORANGE  = "#ff9500"
C_MUTED   = "#4a4a5a"
C_WHITE   = "#f0f0f8"
C_BORDER  = "#2a2a3a"
C_CHECK_BG = "#1e1e2e"

F_TITLE = ("Segoe UI", 15, "bold")
F_HEAD  = ("Segoe UI", 9, "bold")
F_BODY  = ("Segoe UI", 9)
F_SMALL = ("Segoe UI", 8)
F_MONO  = ("Consolas", 9)

# ══════════════════════════════════════════════════════════════
#  CUSTOM DIALOG
# ══════════════════════════════════════════════════════════════
def show_dialog(title, message, kind="info"):
    result = [None]
    dlg = tk.Toplevel(root)
    dlg.title("")
    dlg.configure(bg=C_CARD)
    dlg.resizable(False, False)
    dlg.grab_set()

    dlg.update_idletasks()
    w, h = 420, 230
    x = root.winfo_x() + (root.winfo_width()  - w) // 2
    y = root.winfo_y() + (root.winfo_height() - h) // 2
    dlg.geometry(f"{w}x{h}+{x}+{y}")

    icon_map  = {"info": ("ℹ", C_ACCENT), "warn": ("⚠", C_ORANGE),
                 "error": ("✕", C_RED),   "confirm": ("？", C_ACCENT)}
    icon, col = icon_map.get(kind, ("ℹ", C_ACCENT))

    tk.Frame(dlg, bg=col, height=3).pack(fill="x")
    body = tk.Frame(dlg, bg=C_CARD, padx=24, pady=18)
    body.pack(fill="both", expand=True)
    tk.Label(body, text=icon, font=("Segoe UI", 28), fg=col, bg=C_CARD).pack(side="left", padx=(0, 18))
    txt_frame = tk.Frame(body, bg=C_CARD)
    txt_frame.pack(side="left", fill="both", expand=True)
    tk.Label(txt_frame, text=title, font=F_HEAD, fg=C_WHITE, bg=C_CARD, anchor="w").pack(fill="x")
    tk.Label(txt_frame, text=message, font=F_BODY, fg="#9090a8", bg=C_CARD,
             anchor="w", wraplength=270, justify="left").pack(fill="x", pady=(6, 0))

    btn_bar = tk.Frame(dlg, bg=C_CARD2, pady=12)
    btn_bar.pack(fill="x", side="bottom")

    def _ok():
        result[0] = True
        dlg.destroy()

    def _cancel():
        result[0] = False
        dlg.destroy()

    if kind == "confirm":
        tk.Button(btn_bar, text="نعم، أكمل", command=_ok,
                  bg=C_GREEN, fg="#000", font=F_HEAD, relief="flat",
                  padx=18, pady=5, cursor="hand2").pack(side="right", padx=(4, 16))
        tk.Button(btn_bar, text="إلغاء", command=_cancel,
                  bg=C_CARD3, fg="#9090a8", font=F_HEAD, relief="flat",
                  padx=18, pady=5, cursor="hand2").pack(side="right", padx=4)
    else:
        tk.Button(btn_bar, text="حسناً", command=_ok,
                  bg=col, fg="#000", font=F_HEAD, relief="flat",
                  padx=24, pady=5, cursor="hand2").pack(side="right", padx=16)

    dlg.wait_window()
    return result[0]


def show_reset_confirm():
    result = [None]
    dlg = tk.Toplevel(root)
    dlg.title("")
    dlg.configure(bg=C_CARD)
    dlg.resizable(False, False)
    dlg.grab_set()

    dlg.update_idletasks()
    w, h = 440, 250
    x = root.winfo_x() + (root.winfo_width()  - w) // 2
    y = root.winfo_y() + (root.winfo_height() - h) // 2
    dlg.geometry(f"{w}x{h}+{x}+{y}")

    tk.Frame(dlg, bg=C_ACCENT, height=3).pack(fill="x")
    body = tk.Frame(dlg, bg=C_CARD, padx=22, pady=16)
    body.pack(fill="both", expand=True)
    tk.Label(body, text="🔄", font=("Segoe UI", 26), bg=C_CARD).pack(side="left", padx=(0, 16))
    txt = tk.Frame(body, bg=C_CARD)
    txt.pack(side="left", fill="both", expand=True)
    tk.Label(txt, text="بدء جلسة جديدة", font=F_HEAD, fg=C_WHITE, bg=C_CARD, anchor="w").pack(fill="x")
    tk.Label(txt,
             text="سيتم مسح بيانات القائمة الحالية من الشاشة فقط.\nالفيديوهات المحملة على جهازك لن تُمس.",
             font=F_BODY, fg="#9090a8", bg=C_CARD, anchor="w", justify="left", wraplength=270).pack(fill="x", pady=(8, 0))

    btn_bar = tk.Frame(dlg, bg=C_CARD2, pady=12)
    btn_bar.pack(fill="x", side="bottom")

    def _yes():
        result[0] = True
        dlg.destroy()

    def _no():
        result[0] = False
        dlg.destroy()

    tk.Button(btn_bar, text="نعم، ابدأ من جديد", command=_yes,
              bg=C_ACCENT, fg="#000", font=F_HEAD, relief="flat",
              padx=16, pady=5, cursor="hand2").pack(side="right", padx=(4, 16))
    tk.Button(btn_bar, text="إلغاء", command=_no,
              bg=C_CARD3, fg="#9090a8", font=F_HEAD, relief="flat",
              padx=16, pady=5, cursor="hand2").pack(side="right", padx=4)

    dlg.wait_window()
    return result[0]


# ══════════════════════════════════════════════════════════════
#  VIDEO LIST  — checkboxes with white tick
# ══════════════════════════════════════════════════════════════
def populate_video_list():
    for w in video_list_inner.winfo_children():
        w.destroy()
    check_vars.clear()

    for vid in playlist_info:
        var = tk.BooleanVar(value=True)
        check_vars.append(var)

        row = tk.Frame(video_list_inner, bg=C_CARD)
        row.pack(fill="x", padx=4, pady=1)

        tk.Label(row, text=f"{vid['index']:>3}.", font=F_MONO,
                 fg=C_MUTED, bg=C_CARD, width=4).pack(side="left")

        # ── Checkbox with WHITE checkmark ──────────────────
        cb = tk.Checkbutton(
            row,
            variable=var,
            bg=C_CARD,
            activebackground=C_CARD,
            selectcolor=C_CARD2,          # box fill when checked
            fg=C_WHITE,                   # checkmark colour (Windows uses fg for mark)
            activeforeground=C_WHITE,
            relief="flat",
            cursor="hand2",
            bd=0,
        )
        cb.pack(side="left", padx=(2, 4))

        short = vid["title"][:66] + "…" if len(vid["title"]) > 66 else vid["title"]
        tk.Label(row, text=short, font=F_BODY, fg=C_WHITE,
                 bg=C_CARD, anchor="w").pack(side="left", fill="x", expand=True)

    video_list_inner.update_idletasks()
    video_canvas.configure(scrollregion=video_canvas.bbox("all"))


def select_all():    [v.set(True)        for v in check_vars]
def deselect_all():  [v.set(False)       for v in check_vars]
def invert_sel():    [v.set(not v.get()) for v in check_vars]
def get_selected():  return [v for v, cb in zip(playlist_info, check_vars) if cb.get()]

# ══════════════════════════════════════════════════════════════
#  RESET
# ══════════════════════════════════════════════════════════════
def reset_ui():
    if not show_reset_confirm():
        return
    global playlist_info, format_map, check_vars
    playlist_info.clear(); format_map.clear(); check_vars.clear()
    for w in video_list_inner.winfo_children():
        w.destroy()
    video_list_inner.update_idletasks()
    video_canvas.configure(scrollregion=video_canvas.bbox("all"))
    playlist_label.config(text="لم يتم الجلب بعد", fg=C_MUTED)
    total_label.config(text="")
    current_video_label.config(text="—")
    remaining_label.config(text="المتبقي: —")
    quality_label.config(text="")
    speed_label.config(text="السرعة: —   |   الوقت المتبقي: —")
    set_status("جاهز", C_GREEN)
    playlist_bar["value"] = 0;  playlist_pct_label.config(text="0 / 0")
    video_bar["value"]    = 0;  video_pct_label.config(text="0%")
    quality_menu["values"] = []; quality_var.set("")
    url_entry.delete(0, tk.END)

# ══════════════════════════════════════════════════════════════
#  FETCH
# ══════════════════════════════════════════════════════════════
def fetch_info():
    threading.Thread(target=_fetch_thread, daemon=True).start()

def _fetch_thread():
    fetch_btn.config(state="disabled", text="⏳ جاري الجلب...")
    set_status("جاري جلب بيانات الفيديو...", C_ACCENT)
    try:
        url = url_entry.get().strip()
        if not url:
            show_dialog("الرابط فارغ", "من فضلك أدخل رابط الفيديو أو قائمة التشغيل", "error")
            return

        opts = {**base_opts(), "extract_flat": "in_playlist", "playlistend": 9999}
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)

        global playlist_info, total_videos, format_map
        playlist_info.clear(); format_map.clear()

        if "entries" in info:
            entries = [e for e in info["entries"] if e]
            total_videos = len(entries)
            for i, e in enumerate(entries):
                vid_id  = e.get("id") or ""
                vid_url = (e.get("webpage_url")
                           or (f"https://www.youtube.com/watch?v={vid_id}"
                               if len(vid_id) == 11 else vid_id))
                playlist_info.append({
                    "index": i + 1,
                    "title": e.get("title", f"فيديو {i+1}"),
                    "url":   vid_url
                })
            pl_name = info.get("title", "قائمة تشغيل")
            playlist_label.config(text=f"🎵  {pl_name}", fg=C_WHITE)
            total_label.config(text=f"عدد الفيديوهات: {total_videos}")
            first_url = playlist_info[0]["url"]
        else:
            total_videos = 1
            playlist_info = [{"index": 1, "title": info.get("title", "فيديو"), "url": url}]
            playlist_label.config(text=f"🎬  {info.get('title','فيديو')}", fg=C_WHITE)
            total_label.config(text="فيديو واحد")
            first_url = url

        # ── جلب الجودات ──────────────────────────
        with yt_dlp.YoutubeDL(base_opts()) as ydl:
            vinfo = ydl.extract_info(first_url, download=False)

        heights = set()
        for f in vinfo.get("formats", []):
            h  = f.get("height")
            vc = (f.get("vcodec") or "none").lower()
            ac = (f.get("acodec") or "none").lower()
            if not h or vc == "none":
                continue
            if HAS_FFMPEG or ac != "none":
                heights.add(h)

        for h in sorted(heights, reverse=True):
            format_map[f"{h}p"] = h

        all_keys = ["أفضل جودة"] + list(format_map.keys())
        quality_menu["values"] = all_keys
        quality_var.set(all_keys[0])
        # Update combobox colors after setting values
        _style_combobox()

        populate_video_list()

        if HAS_FFMPEG:
            set_status(f"✓  جاهز — {len(format_map)} جودة متاحة (بما فيها 4K)", C_GREEN)
        else:
            set_status("⚠  ffmpeg غير موجود — الجودات المتاحة محدودة (720p كحد أقصى)", C_ORANGE)

    except Exception as e:
        show_dialog("خطأ في جلب البيانات", str(e), "error")
        set_status("فشل الجلب", C_RED)
    finally:
        fetch_btn.config(state="normal", text="🔍  جلب المعلومات")

# ══════════════════════════════════════════════════════════════
#  PROGRESS HOOK
# ══════════════════════════════════════════════════════════════
def make_hook():
    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            done  = d.get("downloaded_bytes", 0)
            spd   = d.get("speed") or 0
            eta   = d.get("eta") or 0
            if total:
                pct = done / total * 100
                video_bar["value"] = pct
                video_pct_label.config(text=f"{pct:.0f}%")
            spd_s = (f"{spd/1024/1024:.1f} MB/s" if spd >= 1024*1024
                     else f"{spd/1024:.0f} KB/s"  if spd else "—")
            eta_s = f"{eta//60}:{eta%60:02d}" if eta else "—"
            speed_label.config(text=f"السرعة: {spd_s}   |   الوقت المتبقي: {eta_s}")
        elif d["status"] == "finished":
            inf = d.get("info_dict", {})
            h   = inf.get("height")
            if h:
                quality_label.config(text=f"✓  الجودة الفعلية: {h}p", fg=C_GREEN)
            video_bar["value"] = 100
            video_pct_label.config(text="100%")
    return hook

# ══════════════════════════════════════════════════════════════
#  DOWNLOAD
# ══════════════════════════════════════════════════════════════
def start_download():
    global stop_flag
    stop_flag = False
    threading.Thread(target=_download_all, daemon=True).start()

def stop_download():
    global stop_flag
    stop_flag = True
    set_status("تم إيقاف التحميل", C_ORANGE)

def _download_all():
    selected = get_selected()
    if not selected:
        show_dialog("لم تختر شيئاً", "اختر فيديو واحداً على الأقل من القائمة", "warn")
        return

    save_path = folder_path.get()
    if not save_path:
        show_dialog("مجلد الحفظ فارغ", "اختر المجلد الذي تريد حفظ الفيديوهات فيه", "warn")
        return

    chosen = quality_var.get()
    h      = format_map.get(chosen)

    if HAS_FFMPEG:
        fmt = (f"bv*[height<={h}]+ba/best[height<={h}]/best" if h else "bv*+ba/best")
    else:
        fmt = (f"best[height<={h}]/best" if h else "best")

    n      = len(selected)
    failed = []

    dl_btn.config(state="disabled")
    stop_btn.config(state="normal")

    for i, vid in enumerate(selected):
        if stop_flag:
            break

        short = vid["title"][:55] + "…" if len(vid["title"]) > 55 else vid["title"]
        current_video_label.config(text=f"[{vid['index']}]  {short}")
        remaining_label.config(text=f"المتبقي: {n - i - 1} فيديو")
        playlist_bar["value"] = i / n * 100
        playlist_pct_label.config(text=f"{i} / {n}")
        video_bar["value"] = 0
        video_pct_label.config(text="0%")
        quality_label.config(text="جاري التحميل...", fg=C_MUTED)
        set_status(f"تحميل الفيديو {i+1} من {n}  —  رقم {vid['index']} في القائمة", C_ACCENT)

        opts = {
            **download_opts(),
            "format":              fmt,
            "outtmpl":             os.path.join(save_path, f"{vid['index']:04d} - %(title)s.%(ext)s"),
            "merge_output_format": "mp4" if HAS_FFMPEG else None,
            "progress_hooks":      [make_hook()],
            "quiet":               False,
        }
        if not HAS_FFMPEG:
            opts.pop("merge_output_format", None)

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([vid["url"]])
        except Exception as e:
            failed.append(f"[{vid['index']}] {vid['title']}")
            print(f"ERROR: {e}")

    playlist_bar["value"] = 100
    playlist_pct_label.config(text=f"{n} / {n}")
    dl_btn.config(state="normal")
    stop_btn.config(state="disabled")

    if stop_flag:
        return

    if failed:
        set_status(f"انتهى مع {len(failed)} خطأ", C_ORANGE)
        show_dialog("اكتمل التحميل مع أخطاء",
                    f"فشل تحميل {len(failed)} فيديو:\n" + "\n".join(failed[:8]),
                    "warn")
    else:
        set_status(f"✓  اكتمل تحميل {n} فيديو بنجاح", C_GREEN)
        show_dialog("اكتمل التحميل 🎉",
                    f"تم تحميل {n} فيديو بنجاح في المجلد المختار.\n"
                    f"يمكنك الضغط على «بدء جديد» للبحث عن قائمة أخرى.",
                    "info")

# ══════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════
def choose_folder():
    f = filedialog.askdirectory()
    if f:
        folder_path.set(f)

def set_status(msg, color=C_MUTED):
    status_label.config(text=msg, fg=color)

def section(parent, title):
    outer = tk.Frame(parent, bg=C_CARD, bd=0)
    outer.pack(fill="x", padx=14, pady=4)
    # Accent top border
    tk.Frame(outer, bg=C_ACCENT, height=2).pack(fill="x")
    # Section header
    hdr_f = tk.Frame(outer, bg=C_CARD2)
    hdr_f.pack(fill="x")
    tk.Label(hdr_f, text=title, font=F_HEAD,
             fg=C_ACCENT, bg=C_CARD2, anchor="w",
             padx=12, pady=6).pack(fill="x")
    # Content area
    inner = tk.Frame(outer, bg=C_CARD, padx=10, pady=8)
    inner.pack(fill="x")
    return inner

def _style_combobox():
    """Force combobox dropdown list colors."""
    try:
        root.tk.eval(f"""
            [{quality_menu._w} configure -background {C_CARD2}]
        """)
    except Exception:
        pass

# ══════════════════════════════════════════════════════════════
#  ROOT WINDOW
# ══════════════════════════════════════════════════════════════
root = tk.Tk()
root.title("YT Downloader Pro")
root.geometry("780x870")
root.configure(bg=C_BG)
root.resizable(False, False)

# ── Extension setup banner (أول مرة بس) ──────────────────────
def _check_ext_banner():
    """لو الـ extension لسه متثبتش، اعرض بنر"""
    base_dir = _get_base_dir()
    marker   = os.path.join(base_dir, "native_host", ".ext_installed")
    if os.path.isfile(marker):
        return

    banner = tk.Frame(root, bg="#1a1200", pady=0)
    try:
        children = root.winfo_children()
        if children:
            banner.pack(fill="x", before=children[0])
        else:
            banner.pack(fill="x")
    except Exception:
        banner.pack(fill="x")

    inner = tk.Frame(banner, bg="#1a1200")
    inner.pack(fill="x", padx=16, pady=6)

    tk.Label(inner,
             text="🔌  خطوة أخيرة: أضف الإضافة لـ Chrome",
             font=("Segoe UI", 9, "bold"),
             fg=C_ORANGE, bg="#1a1200").pack(side="left")

    def _open_guide():
        base = _get_base_dir()
        ext  = os.path.join(base, "yt_downloader_extension")
        chrome_paths = [
            os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
            os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
        ]
        chrome = next((p for p in chrome_paths if os.path.isfile(p)), None)
        if chrome:
            subprocess.Popen([chrome, "chrome://extensions/"])

    def _mark_done():
        marker = os.path.join(_get_base_dir(), "native_host", ".ext_installed")
        os.makedirs(os.path.dirname(marker), exist_ok=True)
        open(marker, "w").close()
        root.after(0, banner.destroy)

    tk.Button(inner, text="افتح chrome://extensions",
              command=_open_guide,
              bg=C_ORANGE, fg="#000",
              font=("Segoe UI", 8, "bold"),
              relief="flat", padx=10, pady=2,
              cursor="hand2").pack(side="left", padx=(12, 6))

    tk.Label(inner,
             text='← اضغط "Load unpacked" واختر مجلد yt_downloader_extension',
             font=("Segoe UI", 8),
             fg="#ccaa55", bg="#1a1200").pack(side="left")

    tk.Button(inner, text="✓ تم التثبيت",
              command=_mark_done,
              bg=C_CARD3, fg=C_MUTED,
              font=("Segoe UI", 8),
              relief="flat", padx=8, pady=2,
              cursor="hand2").pack(side="right")

root.after(500, _check_ext_banner)  # بعد ما الـ UI يتبني

# Set icon from file
try:
    icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app_icon.ico")
    if os.path.isfile(icon_path):
        root.iconbitmap(icon_path)
except Exception:
    pass

# ── Styles ────────────────────────────────────────────────────
sty = ttk.Style()
sty.theme_use("clam")

sty.configure("G.Horizontal.TProgressbar",
               troughcolor=C_CARD3, background=C_GREEN,
               borderwidth=0, thickness=14, lightcolor=C_GREEN, darkcolor=C_GREEN)
sty.configure("C.Horizontal.TProgressbar",
               troughcolor=C_CARD3, background=C_ACCENT,
               borderwidth=0, thickness=10, lightcolor=C_ACCENT, darkcolor=C_ACCENT)

# Combobox — fully visible text, high contrast dropdown
sty.configure("TCombobox",
               fieldbackground=C_CARD2,
               background=C_CARD3,
               foreground=C_WHITE,
               selectbackground=C_ACCENT,
               selectforeground="#000000",
               arrowcolor=C_ACCENT,
               borderwidth=1,
               relief="flat")
sty.map("TCombobox",
        fieldbackground=[("readonly", C_CARD2), ("active", C_CARD3)],
        foreground=[("readonly", C_WHITE), ("active", C_WHITE)],
        background=[("active", C_CARD3)],
        selectforeground=[("readonly", C_WHITE)])

# Style the dropdown listbox via option_add
root.option_add("*TCombobox*Listbox.background",    C_CARD2)
root.option_add("*TCombobox*Listbox.foreground",    C_WHITE)
root.option_add("*TCombobox*Listbox.selectBackground", C_ACCENT)
root.option_add("*TCombobox*Listbox.selectForeground", "#000000")
root.option_add("*TCombobox*Listbox.font",          F_BODY)

# ══════════════════════════════════════════════════════════════
#  HEADER
# ══════════════════════════════════════════════════════════════
hdr = tk.Frame(root, bg=C_BG)
hdr.pack(fill="x", padx=16, pady=(14, 2))

tk.Label(hdr, text="▶  YT Downloader Pro",
         font=F_TITLE, fg=C_ACCENT, bg=C_BG).pack(side="left")

badge_col  = C_GREEN if HAS_FFMPEG else C_ORANGE
badge_text = "  ffmpeg ✓  " if HAS_FFMPEG else "  ffmpeg ✗  "
tk.Label(hdr, text=badge_text, font=F_SMALL,
         fg="#000", bg=badge_col, padx=4, pady=4,
         relief="flat").pack(side="right", padx=(0,2))

tk.Frame(root, bg=C_BORDER, height=1).pack(fill="x", padx=14, pady=(2, 4))

if not HAS_FFMPEG:
    warn_bar = tk.Frame(root, bg="#1a1200")
    warn_bar.pack(fill="x", padx=14, pady=(0, 4))
    tk.Label(warn_bar,
             text="⚠  لتحميل 1080p أو أعلى: ضع ffmpeg.exe في نفس مجلد البرنامج",
             font=F_SMALL, fg=C_ORANGE, bg="#1a1200",
             anchor="w", padx=10, pady=5).pack(fill="x")

# ══════════════════════════════════════════════════════════════
#  S1 — الرابط
# ══════════════════════════════════════════════════════════════
s1 = section(root, "①  رابط الفيديو أو قائمة التشغيل")

url_row = tk.Frame(s1, bg=C_CARD)
url_row.pack(fill="x")

# URL entry with visible border
url_frame = tk.Frame(url_row, bg=C_ACCENT, padx=1, pady=1)
url_frame.pack(side="left", fill="x", expand=True, padx=(0, 8))
url_entry = tk.Entry(url_frame, width=48, font=F_BODY,
                     bg=C_CARD2, fg=C_WHITE,
                     insertbackground=C_ACCENT,
                     relief="flat", bd=4)
url_entry.pack(fill="x", expand=True)

fetch_btn = tk.Button(url_row, text="🔍  جلب المعلومات",
                      command=fetch_info,
                      bg=C_ACCENT, fg="#000", font=F_HEAD,
                      relief="flat", padx=14, pady=7,
                      cursor="hand2", activebackground=C_ACCENT2,
                      activeforeground="#000")
fetch_btn.pack(side="left")

info_strip = tk.Frame(s1, bg=C_CARD)
info_strip.pack(fill="x", pady=(8, 0))

playlist_label = tk.Label(info_strip, text="لم يتم الجلب بعد",
                           font=F_BODY, fg=C_MUTED, bg=C_CARD, anchor="w")
playlist_label.pack(side="left")

total_label = tk.Label(info_strip, text="",
                       font=F_BODY, fg=C_MUTED, bg=C_CARD, anchor="e")
total_label.pack(side="right")

# ══════════════════════════════════════════════════════════════
#  S2 — قائمة الفيديوهات
# ══════════════════════════════════════════════════════════════
s2 = section(root, "②  اختر الفيديوهات التي تريد تحميلها")

sel_row = tk.Frame(s2, bg=C_CARD)
sel_row.pack(fill="x", pady=(0, 8))

for txt, cmd in [("☑  تحديد الكل", select_all),
                 ("☐  إلغاء الكل", deselect_all),
                 ("⇄  عكس التحديد", invert_sel)]:
    tk.Button(sel_row, text=txt, command=cmd,
              bg=C_CARD3, fg=C_WHITE, font=F_SMALL,
              relief="flat", padx=12, pady=4, cursor="hand2",
              activebackground=C_BORDER, activeforeground=C_WHITE).pack(side="left", padx=(0, 4))

# Scrollable list with border
list_border = tk.Frame(s2, bg=C_BORDER, padx=1, pady=1)
list_border.pack(fill="x")

list_bg = tk.Frame(list_border, bg=C_CARD)
list_bg.pack(fill="both", expand=True)

video_canvas = tk.Canvas(list_bg, bg=C_CARD, height=160, highlightthickness=0, bd=0)
vsb = ttk.Scrollbar(list_bg, orient="vertical", command=video_canvas.yview)
video_canvas.configure(yscrollcommand=vsb.set)
vsb.pack(side="right", fill="y")
video_canvas.pack(side="left", fill="both", expand=True)

video_list_inner = tk.Frame(video_canvas, bg=C_CARD)
video_canvas.create_window((0, 0), window=video_list_inner, anchor="nw")
video_canvas.bind("<MouseWheel>",
                  lambda e: video_canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

# ══════════════════════════════════════════════════════════════
#  S3 — الجودة والمجلد
# ══════════════════════════════════════════════════════════════
s3 = section(root, "③  الجودة ومجلد الحفظ")

opts_row = tk.Frame(s3, bg=C_CARD)
opts_row.pack(fill="x")

# Quality label + combobox
tk.Label(opts_row, text="الجودة:", font=F_HEAD, fg=C_WHITE, bg=C_CARD).pack(side="left")
quality_var  = tk.StringVar()
quality_menu = ttk.Combobox(opts_row, textvariable=quality_var,
                             state="readonly", width=16, font=F_BODY,
                             style="TCombobox")
quality_menu.pack(side="left", padx=(6, 24), ipady=4)

# Folder label + entry + button
tk.Label(opts_row, text="مجلد الحفظ:", font=F_HEAD, fg=C_WHITE, bg=C_CARD).pack(side="left")
folder_path = tk.StringVar()

folder_frame = tk.Frame(opts_row, bg=C_ACCENT, padx=1, pady=1)
folder_frame.pack(side="left", padx=(6, 6))
tk.Entry(folder_frame, textvariable=folder_path, width=24, font=F_BODY,
         bg=C_CARD2, fg=C_WHITE, relief="flat", bd=4,
         insertbackground=C_ACCENT).pack()

tk.Button(opts_row, text="📂", command=choose_folder,
          bg=C_CARD3, fg=C_WHITE, font=("Segoe UI", 11),
          relief="flat", padx=8, pady=3, cursor="hand2",
          activebackground=C_BORDER).pack(side="left")

# ══════════════════════════════════════════════════════════════
#  S4 — التقدم
# ══════════════════════════════════════════════════════════════
s4 = section(root, "④  تقدم التحميل")

# — Playlist bar —
pl_lbl_row = tk.Frame(s4, bg=C_CARD)
pl_lbl_row.pack(fill="x", pady=(0, 2))
tk.Label(pl_lbl_row, text="القائمة الكاملة:", font=F_HEAD,
         fg=C_WHITE, bg=C_CARD).pack(side="left")
remaining_label = tk.Label(pl_lbl_row, text="المتبقي: —",
                            font=F_BODY, fg=C_MUTED, bg=C_CARD)
remaining_label.pack(side="right")

pl_bar_row = tk.Frame(s4, bg=C_CARD)
pl_bar_row.pack(fill="x", pady=(0, 8))
playlist_bar = ttk.Progressbar(pl_bar_row, length=660, mode="determinate",
                                style="G.Horizontal.TProgressbar")
playlist_bar.pack(side="left")
playlist_pct_label = tk.Label(pl_bar_row, text="0 / 0",
                               font=F_MONO, fg=C_GREEN, bg=C_CARD, width=8)
playlist_pct_label.pack(side="left", padx=(6, 0))

# — Current video —
tk.Label(s4, text="الفيديو الحالي:", font=F_HEAD,
         fg=C_WHITE, bg=C_CARD, anchor="w").pack(fill="x")
current_video_label = tk.Label(s4, text="—", font=F_BODY,
                                fg=C_ACCENT, bg=C_CARD, anchor="w")
current_video_label.pack(fill="x", pady=(2, 4))

# — Video progress bar —
vid_bar_row = tk.Frame(s4, bg=C_CARD)
vid_bar_row.pack(fill="x", pady=(0, 4))
video_bar = ttk.Progressbar(vid_bar_row, length=660, mode="determinate",
                             style="C.Horizontal.TProgressbar")
video_bar.pack(side="left")
video_pct_label = tk.Label(vid_bar_row, text="0%",
                            font=F_MONO, fg=C_ACCENT, bg=C_CARD, width=6)
video_pct_label.pack(side="left", padx=(6, 0))

extra_row = tk.Frame(s4, bg=C_CARD)
extra_row.pack(fill="x", pady=(2, 0))
quality_label = tk.Label(extra_row, text="", font=F_MONO,
                          fg=C_MUTED, bg=C_CARD, anchor="w")
quality_label.pack(side="left")
speed_label = tk.Label(extra_row, text="السرعة: —   |   الوقت المتبقي: —",
                       font=F_MONO, fg=C_MUTED, bg=C_CARD, anchor="e")
speed_label.pack(side="right")

# ══════════════════════════════════════════════════════════════
#  ACTION BUTTONS
# ══════════════════════════════════════════════════════════════
btn_frame = tk.Frame(root, bg=C_BG)
btn_frame.pack(pady=12)

dl_btn = tk.Button(btn_frame, text="⬇  ابدأ التحميل",
                   command=start_download,
                   bg=C_GREEN, fg="#000",
                   font=("Segoe UI", 11, "bold"),
                   relief="flat", padx=24, pady=9,
                   cursor="hand2",
                   activebackground="#00c060", activeforeground="#000")
dl_btn.pack(side="left", padx=6)

stop_btn = tk.Button(btn_frame, text="⏹  إيقاف",
                     command=stop_download,
                     bg=C_RED, fg=C_WHITE,
                     font=("Segoe UI", 11, "bold"),
                     relief="flat", padx=24, pady=9,
                     cursor="hand2", state="disabled",
                     activebackground="#cc2222", activeforeground=C_WHITE)
stop_btn.pack(side="left", padx=6)

tk.Button(btn_frame, text="🔄  بدء جديد",
          command=reset_ui,
          bg=C_CARD3, fg=C_WHITE,
          font=("Segoe UI", 11, "bold"),
          relief="flat", padx=24, pady=9,
          cursor="hand2",
          activebackground=C_BORDER, activeforeground=C_WHITE).pack(side="left", padx=6)

# ══════════════════════════════════════════════════════════════
#  STATUS BAR
# ══════════════════════════════════════════════════════════════
status_bar = tk.Frame(root, bg="#0d0d12")
status_bar.pack(fill="x", side="bottom")
tk.Frame(status_bar, bg=C_ACCENT, height=2).pack(fill="x")
status_label = tk.Label(status_bar, text="جاهز",
                        font=F_MONO, fg=C_GREEN, bg="#0d0d12",
                        anchor="w", padx=16)
status_label.pack(fill="x", ipady=5)

# ══════════════════════════════════════════════════════════════
root.mainloop()