# YT Downloader Pro 🎬

> Download YouTube videos and playlists with ease — no Python, no command line, just one click.

---

## 🇬🇧 English

### Requirements
- Windows 10 or 11
- Google Chrome


---

### Installation (2 steps only)

#### Step 1 — Run the app
1. Go to [Releases](../../releases) and download the latest `YT-Downloader-Pro.zip`
2. Extract the ZIP anywhere on your PC
3. Double-click **`yt_downloader_pro.exe`**
   - The app will automatically set up everything in the background ✅

#### Step 2 — Add the Chrome Extension
When the app opens, you'll see a banner at the top:

> 🔌 **One last step: Add the extension to Chrome**

1. Click **"Open chrome://extensions"**
2. Enable **Developer Mode** (toggle in the top-right corner)
3. Click **"Load unpacked"**
4. Select the **`yt_downloader_extension`** folder (inside the extracted ZIP)
5. Click **"✓ Done"** in the banner

**That's it! 🎉**

---

### How to use
- Open any YouTube video or playlist
- Click the **⬇ Download** button that appears on the page
- Or paste the URL directly in the app and click **Fetch**

---

### Troubleshooting

| Problem | Solution |
|---|---|
| "Host not found" error | Make sure the app is open before using the extension |
| Extension doesn't appear | Make sure you selected the correct `yt_downloader_extension` folder |
| Download stuck | Click Stop and try again |
| No 1080p+ option | Place `ffmpeg.exe` in the same folder as the app |


## 📁 Project Structure

```
YT-Downloader-Pro/
├── yt_downloader_pro.exe        ← Main app (run this)
├── ffmpeg.exe                   ← Optional (for 1080p+)
├── native_host/
│   ├── ytdl_host.exe            ← Chrome ↔ App bridge (auto-configured)
│   └── ytdl_host.py
└── yt_downloader_extension/     ← Load this in Chrome
    ├── manifest.json
    ├── background.js
    └── ...
```

---

## ⭐ Support
If you find this useful, give it a star on GitHub!