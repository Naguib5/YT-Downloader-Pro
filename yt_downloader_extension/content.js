// content.js v10 — Universal Video Downloader
// Supports: YouTube, Facebook, Twitter/X, Instagram, TikTok, Vimeo, Dailymotion,
//           JWPlayer, VideoJS, Clappr, Plyr, HLS.js, DASH.js, and any site with <video>
(function () {
  'use strict';

  // ── Keepalive ──────────────────────────────────────────────────────────────
  function connectKeepalive() {
    try {
      const p = chrome.runtime.connect({ name: "ytdl-keepalive" });
      p.onDisconnect.addListener(() => setTimeout(connectKeepalive, 3000));
    } catch(e) {}
  }
  connectKeepalive();

  // ── State ──────────────────────────────────────────────────────────────────
  let fabEl         = null;
  let overlayEl     = null;
  let lastUrl       = "";
  let pageUrl       = "";
  let videoTitle    = "";
  let videoThumb    = "";
  let savePath      = "";
  let isDownloading = false;
  // Intercepted network URLs (m3u8, mp4, mpd) caught by the injected script
  let interceptedUrls = [];
  // Track detected video elements to avoid duplicate buttons
  const detectedElements = new WeakSet();

  chrome.storage.local.get(["ytdl_save_path"], (r) => {
    if (r && r.ytdl_save_path) savePath = r.ytdl_save_path;
  });

  // ── Listen for intercepted network URLs from injected script ──────────────
  window.addEventListener("__ytdl_intercepted__", (e) => {
    const url = e.detail && e.detail.url;
    if (!url) return;
    // Deduplicate
    if (!interceptedUrls.includes(url)) {
      interceptedUrls.push(url);
      // Keep only last 30 to avoid memory growth
      if (interceptedUrls.length > 30) interceptedUrls.shift();
    }
    // If FAB not yet shown, trigger detection
    if (!fabEl) tryShowFab();
  });

  // ── Native messages ────────────────────────────────────────────────────────
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type !== "NATIVE_MSG") return;
    const d = msg.payload;

    if (d.type === "folder_selected" && d.path) {
      savePath = d.path;
      chrome.storage.local.set({ ytdl_save_path: savePath });
      const el = document.getElementById("ytdl-folder-path");
      if (el) el.textContent = savePath;
      setStatus("✅ تم حفظ المجلد: " + savePath, "ok");
      return;
    }
    if (d.type === "folder_cancelled") { setStatus("", ""); return; }
    if (!overlayEl) return;

    if (d.type === "progress") {
      showProgressUI();
      setProgress(d.pct ?? -1, d.speed || "", d.size || "");
      setStatus("جاري التحميل" + (d.pct >= 0 ? " " + d.pct + "%" : "..."), "ok");
    } else if (d.type === "merging") {
      setProgress(99, "", "");
      setStatus("🔀 جاري دمج الفيديو والصوت...", "ok");
    } else if (d.type === "done") {
      isDownloading = false;
      setProgress(100, "", "");
      setStatus("✅ تم الحفظ: " + (d.filename || ""), "ok");
      const cb = document.getElementById("ytdl-cancel-btn");
      if (cb) { cb.textContent = "✓ إغلاق"; cb.onclick = closeOverlay; }
      showCloseBtn();
    } else if (d.type === "error") {
      isDownloading = false;
      setStatus("❌ " + (d.error || "خطأ").replace(/\n/g, " | "), "err");
      showCloseBtn(); showQualityUI();
    } else if (d.type === "pong") {
      setStatus("✅ جاهز — اختار الجودة", "ok");
    }
  });

  // ══════════════════════════════════════════════════════════════════════════
  //  NETWORK INTERCEPTION — injected into page context via script tag
  //  Catches fetch/XHR/MediaSource requests for m3u8, mp4, mpd
  // ══════════════════════════════════════════════════════════════════════════
  function injectNetworkInterceptor() {
    // Avoid double injection
    if (document.getElementById("__ytdl_interceptor__")) return;
    const script = document.createElement("script");
    script.id = "__ytdl_interceptor__";
    script.textContent = `(function(){
      if(window.__ytdl_intercepted) return;
      window.__ytdl_intercepted = true;
      const VIDEO_RE = /\\.(?:m3u8|mp4|webm|mpd)(\\?|$|#)/i;
      const STREAM_RE = /\\.(?:m3u8|mpd)/i;

      function emit(url){
        try{
          if(!url || typeof url!=='string') return;
          if(!VIDEO_RE.test(url) && !STREAM_RE.test(url)) return;
          window.dispatchEvent(new CustomEvent('__ytdl_intercepted__',{detail:{url}}));
        }catch(e){}
      }

      // Patch fetch
      const _fetch = window.fetch;
      window.fetch = function(input,...args){
        try{ emit(typeof input==='string'?input:input.url); }catch(e){}
        return _fetch.apply(this,[input,...args]);
      };

      // Patch XHR
      const _open = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(m, url, ...rest){
        try{ emit(url); }catch(e){}
        return _open.apply(this,[m,url,...rest]);
      };

      // Patch MediaSource / SourceBuffer
      if(window.MediaSource){
        const _addSB = MediaSource.prototype.addSourceBuffer;
        MediaSource.prototype.addSourceBuffer = function(mime,...args){
          // mime like 'video/mp4; codecs=...' or 'application/x-mpegURL'
          try{
            window.dispatchEvent(new CustomEvent('__ytdl_intercepted__',{detail:{url:window.location.href, mime}}));
          }catch(e){}
          return _addSB.apply(this,[mime,...args]);
        };
      }

      // Watch PerformanceObserver for resource entries
      try{
        const po = new PerformanceObserver((list)=>{
          for(const e of list.getEntries()){
            emit(e.name);
          }
        });
        po.observe({type:'resource',buffered:true});
      }catch(e){}
    })();`;
    (document.head || document.documentElement).appendChild(script);
  }

  // Inject immediately
  injectNetworkInterceptor();
  // Re-inject after navigation for SPAs
  document.addEventListener("DOMContentLoaded", injectNetworkInterceptor);

  // ══════════════════════════════════════════════════════════════════════════
  //  SPA navigation observer — detects URL changes in React/Vue/Angular apps
  // ══════════════════════════════════════════════════════════════════════════
  const pageObserver = new MutationObserver(() => {
    const cur = window.location.href;
    if (cur !== lastUrl) {
      lastUrl = cur;
      interceptedUrls = []; // clear on page change
      onPageChange();
    }
  });
  pageObserver.observe(document.documentElement, { childList: true, subtree: true });

  // Also catch history API navigation (pushState/replaceState)
  ["pushState", "replaceState"].forEach((fn) => {
    const orig = history[fn];
    history[fn] = function (...args) {
      const result = orig.apply(this, args);
      setTimeout(() => {
        const cur = window.location.href;
        if (cur !== lastUrl) { lastUrl = cur; interceptedUrls = []; onPageChange(); }
      }, 100);
      return result;
    };
  });
  window.addEventListener("popstate", () => {
    setTimeout(() => {
      const cur = window.location.href;
      if (cur !== lastUrl) { lastUrl = cur; interceptedUrls = []; onPageChange(); }
    }, 100);
  });

  // Initial run
  lastUrl = window.location.href;
  onPageChange();

  function onPageChange() {
    removeFab();
    if (overlayEl && !isDownloading) closeOverlay();
    // Wait for dynamic content to load
    setTimeout(tryShowFab, 800);
    setTimeout(tryShowFab, 2000);
    setTimeout(tryShowFab, 4000);
  }

  function tryShowFab() {
    if (fabEl) return; // already shown
    const info = detectVideo();
    if (info) {
      pageUrl    = info.url;
      videoTitle = info.title;
      videoThumb = info.thumb;
      // Pass intercepted URLs to native host via a separate field
      info.intercepted = interceptedUrls.slice();
      showFab();
    }
  }

  // Also watch for dynamic video elements being added to the DOM
  const videoObserver = new MutationObserver((mutations) => {
    if (fabEl) return;
    for (const m of mutations) {
      for (const node of m.addedNodes) {
        if (node.nodeType !== 1) continue;
        const hasVideo = node.tagName === "VIDEO" ||
          node.querySelector && node.querySelector("video, iframe");
        if (hasVideo) {
          setTimeout(tryShowFab, 500);
          return;
        }
      }
    }
  });
  videoObserver.observe(document.documentElement, { childList: true, subtree: true });

  // ══════════════════════════════════════════════════════════════════════════
  //  detectVideo — comprehensive detection for all sites
  // ══════════════════════════════════════════════════════════════════════════
  function detectVideo() {
    const u    = window.location.href;
    const host = window.location.hostname.replace(/^(www\.|m\.|mobile\.)+/, "");

    // ── YouTube ──────────────────────────────────────────────────────────────
    if (host === "youtube.com" || host === "youtu.be") {
      const shorts = u.match(/\/shorts\/([A-Za-z0-9_-]{8,})/);
      if (shorts) return mkResult(u, getYTTitle(),
          `https://img.youtube.com/vi/${shorts[1]}/mqdefault.jpg`);
      const live = u.match(/\/live\/([A-Za-z0-9_-]{8,})/);
      if (live) return mkResult(u, getYTTitle(),
          `https://img.youtube.com/vi/${live[1]}/mqdefault.jpg`);
      const watch = u.match(/[?&]v=([A-Za-z0-9_-]{8,})/);
      if (watch) return mkResult(u, getYTTitle(),
          `https://img.youtube.com/vi/${watch[1]}/mqdefault.jpg`);
      if (host === "youtu.be") {
        const id = location.pathname.slice(1).split("?")[0];
        if (id.length >= 8) return mkResult(u,
          document.title.replace(/ - YouTube$/,"").trim(),
          `https://img.youtube.com/vi/${id}/mqdefault.jpg`);
      }
      return null;
    }

    // ── Facebook / fb.watch ───────────────────────────────────────────────
    if (host.includes("facebook.com") || host === "fb.watch") {
      if (u.includes("/login") || u === "https://www.facebook.com/") return null;
      const fbOk = u.match(/\/(?:videos?|reel|watch|share\/r)\//);
      if (!fbOk && host !== "fb.watch") return null;
      let realUrl = u;
      const canonical = document.querySelector('link[rel="canonical"]');
      if (canonical?.href?.includes("facebook.com")) realUrl = canonical.href;
      else {
        const ogUrl = document.querySelector('meta[property="og:url"]');
        if (ogUrl?.content?.includes("facebook.com")) realUrl = ogUrl.content;
      }
      if (!realUrl.match(/\/\d{5,}/) && !realUrl.match(/\/reel\/\d+/) && host !== "fb.watch") return null;
      return mkResult(realUrl, document.title.replace(/[|–\-]\s*(Facebook|فيسبوك).*/i,"").trim(), "");
    }

    // ── Twitter/X ────────────────────────────────────────────────────────
    if (host === "twitter.com" || host === "x.com") {
      if (!u.match(/\/status\/\d+/)) return null;
      return mkResult(u, document.title.replace(/\s*[\/|]\s*(X|Twitter)$/, "").trim(), "");
    }

    // ── Instagram ────────────────────────────────────────────────────────
    if (host === "instagram.com") {
      if (!u.match(/\/(reel|p|tv)\/[A-Za-z0-9_-]+/)) return null;
      return mkResult(u, document.title.replace(/[•|]\s*Instagram.*/,"").trim(), "");
    }

    // ── TikTok ───────────────────────────────────────────────────────────
    if (host.includes("tiktok.com")) {
      if (!u.match(/\/@[\w.]+\/video\/\d+/)) return null;
      return mkResult(u, document.title.replace(/\s*[|]\s*TikTok$/, "").trim(), "");
    }

    // ── Vimeo ────────────────────────────────────────────────────────────
    if (host === "vimeo.com") {
      if (!u.match(/\/\d{5,}/)) return null;
      return mkResult(u, document.title.replace(/ on Vimeo$/, "").trim(), "");
    }

    // ── Dailymotion ──────────────────────────────────────────────────────
    if (host === "dailymotion.com") {
      if (!u.match(/\/video\/[a-z0-9]+/i)) return null;
      return mkResult(u, document.title.replace(/ - Dailymotion$/, "").trim(), "");
    }

    // ── Rumble ───────────────────────────────────────────────────────────
    if (host === "rumble.com") {
      if (!u.match(/\/(embed|v)\/[a-z0-9]+/i)) return null;
      return mkResult(u, document.title.trim(), "");
    }

    // ── Twitch ───────────────────────────────────────────────────────────
    if (host === "twitch.tv") {
      if (!u.match(/\/(videos\/\d+|[a-z0-9_]+\/clip\/|[a-z0-9_]+$)/i)) return null;
      return mkResult(u, document.title.replace(/ - Twitch$/, "").trim(), "");
    }

    // ── Streamable ───────────────────────────────────────────────────────
    if (host === "streamable.com") {
      if (!u.match(/\/[a-z0-9]{4,}/i)) return null;
      return mkResult(u, document.title.trim(), getOgThumb());
    }

    // ── Intercepted network URLs (m3u8/mp4) from any site ────────────────
    // Filter out known fake URLs (CAPTCHAs, tracking pixels, etc.)
    const FAKE_INTERCEPT = /captcha|securimage|recaptcha|pixel|beacon|tracking|analytics|doubleclick|googlesyndication|adservice|prebid|disqus|facebook\.com\/plugins/i;
    const validIntercepted = interceptedUrls.filter(u => !FAKE_INTERCEPT.test(u));
    if (validIntercepted.length > 0) {
      return mkResult(u, document.title.trim() || host, "");
    }

    // ══════════════════════════════════════════════════════════════════════
    //  Generic detection — works on any site
    // ══════════════════════════════════════════════════════════════════════

    const fakeHint = /captcha|securimage|avatar|icon|logo|banner|sprite|pixel|beacon|placeholder|preroll|promo/i;
    const MIN_W = 200, MIN_H = 120;

    // 1. <video> elements — must be visible and large enough
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      const rect = v.getBoundingClientRect();
      const w = rect.width  || v.offsetWidth;
      const h = rect.height || v.offsetHeight;
      if (w < MIN_W || h < MIN_H) continue;
      const style = window.getComputedStyle(v);
      if (style.display === "none" || style.visibility === "hidden" || style.opacity === "0") continue;
      if (fakeHint.test((v.id || "") + " " + (v.className || ""))) continue;
      const src = v.src || v.currentSrc || "";
      if (src && (src.startsWith("http") || src.startsWith("blob:") || src.startsWith("//"))) {
        return mkResult(u, document.title.trim() || host, getOgThumb());
      }
      // Check <source> children
      const sources = v.querySelectorAll("source");
      for (const s of sources) {
        if (s.src && s.src.startsWith("http")) return mkResult(u, document.title.trim() || host, getOgThumb());
      }
      // Video element exists but src not set yet — still worth showing button
      // if it has poster or data attributes suggesting a real player
      if (v.poster || v.getAttribute("data-src") || v.getAttribute("data-video")) {
        return mkResult(u, document.title.trim() || host, v.poster || getOgThumb());
      }
    }

    // 2. Known video player containers (JWPlayer, VideoJS, Clappr, Plyr, etc.)
    const playerSelectors = [
      ".jwplayer", ".jw-player",                  // JWPlayer
      ".video-js", ".vjs-tech",                   // VideoJS
      ".clappr-player", ".clappr-wrapper",        // Clappr
      ".plyr", ".plyr__video-wrapper",            // Plyr
      "[data-player]", "[data-vjs-player]",       // generic player attrs
      ".html5-video-player",                      // YouTube-style
      "#player", "#video-player", "#mediaplayer", // common IDs
      ".video-player", ".media-player",           // common classes
      "[class*='player'][class*='video']",
      "[id*='player'][class*='video']",
    ];
    for (const sel of playerSelectors) {
      try {
        const el = document.querySelector(sel);
        if (!el) continue;
        const w = el.offsetWidth, h = el.offsetHeight;
        if (w < MIN_W || h < MIN_H) continue;
        return mkResult(u, document.title.trim() || host, getOgThumb());
      } catch(e) {}
    }

    // 3. HLS.js / Dash.js instances on window
    if (window.Hls || window.dashjs || window.jwplayer || window.videojs ||
        window.Clappr || window.Plyr || window.fluidPlayer) {
      return mkResult(u, document.title.trim() || host, getOgThumb());
    }

    // 4. <iframe> from known video hosts — must be large enough
    const VIDEO_IFRAME_HOSTS = [
      // Major platforms
      "youtube.com/embed", "youtu.be",
      "vimeo.com/video", "player.vimeo.com",
      "dailymotion.com/embed",
      "rumble.com/embed",
      "twitch.tv/embed", "player.twitch.tv",
      "streamable.com/e/",
      "ok.ru/videoembed",
      // File hosts / stream hosts
      "streamtape.com", "streamtape.co",
      "mp4upload.com",
      "doodstream.com", "dood.", "ds2play.",
      "filemoon.", "moon.", "fmoonembed.",
      "streamwish.", "swdyu.", "wishembed.",
      "vidhide.", "vidhidevip.", "vidhidepro.",
      "vidfast.", "vidplay.", "vidsrc.", "vidcloud.",
      "vtube.", "vvtube.",
      "embedsito.", "uqload.", "mixdrop.",
      "upstream.", "highload.", "vidmoly.",
      "streamlare.", "slmaxed.", "slwatch.",
      "supervideo.", "sv.","govid.",
      "voe.sx", "voe.",
      "mega.nz/embed", "drive.google.com/file",
      "1fichier.com", "abyss.to",
      // Arabic streaming sites
      "shahid.net/", "starzplay.", "cimaclub.", "cimakufilm.",
      "myvid.", "arabseed.", "mycima.", "akwam.",
      "fushaar.", "series-ar.", "animeiat.", "animeblkom.",
      "4anime.", "animesaturn.", "gogoanime.", "animepahe.",
      "wecima.", "cinemalek.", "mazikashare.",
      "fasel4k.", "faselk.", "faseL4k.",
      "2mbd.", "stream4arab.", "arabicflix.",
      // Generic patterns
      "embed-watch", "embed.php", "player.php", "stream.php",
      "watch.php", "play.php", "iframe.php",
    ];
    const iframes = document.querySelectorAll("iframe");
    for (const f of iframes) {
      const w = f.offsetWidth, h = f.offsetHeight;
      if (w < MIN_W || h < MIN_H) continue;
      const isrc = f.src || f.getAttribute("data-src") || f.getAttribute("data-lazy-src") || "";
      if (!isrc || isrc === "about:blank" || isrc.startsWith("javascript:")) continue;
      if (/\.js(\?|$)|analytics|gtag|disqus|facebook\.com\/plugins|twitter\.com\/widgets/i.test(isrc)) continue;
      // Check against known video hosts
      if (VIDEO_IFRAME_HOSTS.some(h => isrc.includes(h))) {
        return mkResult(u, document.title.trim() || host, getOgThumb());
      }
      // Check for video-related path patterns in the src
      if (/\/(embed|player|video|watch|stream)\b/i.test(isrc) && !/ads\.|ad\.|banner/i.test(isrc)) {
        if (w > 300 && h > 180) { // Must be reasonably large
          return mkResult(u, document.title.trim() || host, getOgThumb());
        }
      }
    }

    // 5. og:video meta — strong signal
    const ogVideo = document.querySelector('meta[property="og:video"], meta[property="og:video:url"], meta[property="og:video:secure_url"]');
    if (ogVideo?.content?.startsWith("http")) {
      return mkResult(u, document.title.trim() || host, getOgThumb());
    }

    // 6. URL pattern matching — strong patterns for video pages
    if (/[?&](?:vid|video_id|videoId|id)=[a-zA-Z0-9_\-]{3,}/i.test(u) ||
        /video\.php\?/i.test(u) ||
        /\/(?:episode|watch|series|movie|film|anime|مسلسل|فيلم|حلقة)\/[a-zA-Z0-9_\-]{3,}/i.test(u) ||
        /\/(?:embed|player|stream|play)\/[a-zA-Z0-9_\-]{3,}/i.test(u) ||
        /\/(?:v|e|f)\/[a-zA-Z0-9_\-]{6,}/i.test(u) ||
        /(?:shahid|cima|wecimaplus|mycima|cimaclub|arabseed|akwam|fushaar|animeiat|wecima|fasel4k|faselk|2mbd)\./.test(host)) {
      return mkResult(u, document.title.trim() || host, getOgThumb());
    }

    return null;
  }

  function mkResult(url, title, thumb) {
    return { url, title: title || url, thumb: thumb || "" };
  }

  function getYTTitle() {
    const selectors = [
      'h1.ytd-watch-metadata yt-formatted-string',
      '#title h1 yt-formatted-string',
      'h1.ytd-shorts yt-formatted-string',
      'ytd-video-primary-info-renderer h1',
      'h1[class*="title"]',
    ];
    for (const s of selectors) {
      const el = document.querySelector(s);
      if (el?.textContent?.trim()) return el.textContent.trim();
    }
    return document.title.replace(/ - YouTube$/, "").trim();
  }

  function getOgThumb() {
    const og = document.querySelector('meta[property="og:image"]');
    return og?.content || "";
  }

  // ── FAB ────────────────────────────────────────────────────────────────────
  function showFab() {
    if (fabEl) return;
    fabEl = document.createElement("button");
    fabEl.id = "ytdl-fab";
    fabEl.innerHTML = `<span>⬇</span><span>تحميل</span>`;
    fabEl.setAttribute("title", "YT Downloader Pro — تحميل الفيديو");

    // Make it draggable
    makeDraggable(fabEl);

    fabEl.addEventListener("click", openDialog);
    document.body.appendChild(fabEl);

    // Handle fullscreen: re-append to fullscreen element
    document.addEventListener("fullscreenchange", onFullscreenChange);
    document.addEventListener("webkitfullscreenchange", onFullscreenChange);
  }

  function onFullscreenChange() {
    const fsEl = document.fullscreenElement || document.webkitFullscreenElement;
    if (fsEl && fabEl) {
      fsEl.appendChild(fabEl);
    } else if (fabEl && fabEl.parentElement !== document.body) {
      document.body.appendChild(fabEl);
    }
  }

  function removeFab() {
    if (fabEl) {
      fabEl.remove();
      fabEl = null;
      document.removeEventListener("fullscreenchange", onFullscreenChange);
      document.removeEventListener("webkitfullscreenchange", onFullscreenChange);
    }
  }

  // Draggable FAB
  function makeDraggable(el) {
    let startX, startY, startLeft, startBottom, dragged = false;
    el.addEventListener("mousedown", (e) => {
      if (e.button !== 0) return;
      startX = e.clientX; startY = e.clientY;
      const rect = el.getBoundingClientRect();
      startLeft   = rect.left;
      startBottom = window.innerHeight - rect.bottom;
      el.style.transition = "none";
      dragged = false;

      function onMove(e2) {
        const dx = e2.clientX - startX, dy = e2.clientY - startY;
        if (Math.abs(dx) > 4 || Math.abs(dy) > 4) dragged = true;
        if (!dragged) return;
        el.style.left   = Math.max(0, Math.min(window.innerWidth  - el.offsetWidth,  startLeft   + dx)) + "px";
        el.style.bottom = Math.max(0, Math.min(window.innerHeight - el.offsetHeight, startBottom - dy)) + "px";
        el.style.right  = "auto";
      }
      function onUp() {
        el.style.transition = "";
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup",   onUp);
      }
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup",   onUp);
    });

    el.addEventListener("click", (e) => {
      if (dragged) { dragged = false; e.stopImmediatePropagation(); e.preventDefault(); }
    }, true);
  }

  // ── Dialog ─────────────────────────────────────────────────────────────────
  function openDialog() {
    if (overlayEl) return;
    // Re-detect on open to get latest info
    const info = detectVideo();
    if (info) {
      pageUrl = info.url;
      videoTitle = info.title;
      videoThumb = info.thumb;
    }

    // Merge intercepted URLs into payload for native host
    const interceptedSnapshot = interceptedUrls.slice();

    const displayPath = savePath || "لم يتم الاختيار بعد";
    const thumbHtml   = videoThumb
      ? `<img id="ytdl-thumb" src="${esc(videoThumb)}" alt="" onerror="this.style.display='none'">`
      : "";

    overlayEl = document.createElement("div");
    overlayEl.id = "ytdl-overlay";
    overlayEl.innerHTML = `
      <div id="ytdl-box">
        <h2>⬇ YT Downloader Pro</h2>
        <p id="ytdl-title" title="${esc(videoTitle)}">${esc((videoTitle || window.location.hostname).substring(0,70))}</p>
        ${thumbHtml}
        <div id="ytdl-folder-row">
          <span id="ytdl-folder-icon">📁</span>
          <span id="ytdl-folder-path" title="${esc(displayPath)}">${esc(displayPath)}</span>
          <button id="ytdl-folder-btn">📂 تصفح</button>
        </div>
        <div id="ytdl-status"></div>
        <div id="ytdl-progress-wrap">
          <div id="ytdl-progress-bar-bg"><div id="ytdl-progress-bar"></div></div>
          <div id="ytdl-progress-info">
            <span id="ytdl-progress-pct">0%</span>
            <span id="ytdl-progress-speed"></span>
            <span id="ytdl-progress-size"></span>
          </div>
          <button id="ytdl-cancel-btn">✕ إلغاء</button>
        </div>
        <div id="ytdl-qualities">
          <button class="ytdl-q-btn best" data-q="2160">4K<small>2160p</small></button>
          <button class="ytdl-q-btn best" data-q="1080">1080p<small>FHD</small></button>
          <button class="ytdl-q-btn"      data-q="720" >720p<small>HD</small></button>
          <button class="ytdl-q-btn"      data-q="480" >480p</button>
          <button class="ytdl-q-btn"      data-q="360" >360p</button>
          <button class="ytdl-q-btn"      data-q="240" >240p</button>
        </div>
        <button id="ytdl-audio-btn">🎵 صوت فقط (MP3)</button>
        <button id="ytdl-close" style="display:none">✕ إغلاق</button>
      </div>`;
    document.body.appendChild(overlayEl);

    chrome.runtime.sendMessage({ type: "PING_HOST" }, (r) => {
      if (chrome.runtime.lastError) return;
      setStatus(r?.ok ? "✅ جاهز — اختار الجودة" : "⚠ تأكد من تشغيل الهوست", r?.ok ? "ok" : "err");
    });

    document.getElementById("ytdl-folder-btn").onclick = () => {
      setStatus("⏳ جاري فتح نافذة الاختيار...", "");
      chrome.runtime.sendMessage(
        { type: "NATIVE_SEND", payload: { type: "browse_folder" } },
        () => { void chrome.runtime.lastError; }
      );
    };

    overlayEl.querySelectorAll(".ytdl-q-btn").forEach(b =>
      b.addEventListener("click", () => startDownload(b.dataset.q, "video", interceptedSnapshot)));
    document.getElementById("ytdl-audio-btn").onclick  = () => startDownload("0", "audio", interceptedSnapshot);
    document.getElementById("ytdl-close").onclick      = closeOverlay;
    document.getElementById("ytdl-cancel-btn").onclick = cancelDownload;
    overlayEl.addEventListener("click", e => { if (e.target === overlayEl) closeOverlay(); });
    document.addEventListener("keydown", onEsc);
  }

  function onEsc(e) { if (e.key === "Escape" && !isDownloading) closeOverlay(); }

  function closeOverlay() {
    if (overlayEl) { overlayEl.remove(); overlayEl = null; }
    document.removeEventListener("keydown", onEsc);
  }

  function cancelDownload() {
    isDownloading = false;
    chrome.runtime.sendMessage({ type: "NATIVE_SEND", payload: { type: "cancel" } }, () => {
      void chrome.runtime.lastError;
    });
    showQualityUI();
    setStatus("⛔ تم الإلغاء", "warn");
    const cb = document.getElementById("ytdl-cancel-btn");
    if (cb) { cb.textContent = "✕ إغلاق"; cb.onclick = closeOverlay; }
  }

  function showProgressUI() {
    ["ytdl-qualities","ytdl-audio-btn","ytdl-close"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.style.display = "none";
    });
    const pw = document.getElementById("ytdl-progress-wrap");
    if (pw) pw.style.display = "block";
  }

  function showQualityUI() {
    const q = document.getElementById("ytdl-qualities");
    const a = document.getElementById("ytdl-audio-btn");
    if (q) q.style.display = "grid";
    if (a) a.style.display = "block";
    const pw = document.getElementById("ytdl-progress-wrap");
    if (pw) pw.style.display = "none";
  }

  function showCloseBtn() {
    const cl = document.getElementById("ytdl-close");
    if (cl) cl.style.display = "block";
  }

  function startDownload(quality, dlType, intercepted) {
    if (!savePath) {
      setStatus("⚠ اضغط 📂 تصفح واختار مجلد الحفظ أولاً!", "err");
      return;
    }
    isDownloading = true;
    showProgressUI();
    setStatus("⏳ جاري إرسال الطلب...", "");
    setProgress(0, "", "");

    chrome.runtime.sendMessage({
      type: "NATIVE_SEND",
      payload: {
        type: "download",
        url: pageUrl,
        title: videoTitle,
        quality,
        dlType,
        savePath,
        interceptedUrls: intercepted || [],
      }
    }, (r) => {
      if (chrome.runtime.lastError || !r || !r.ok) {
        isDownloading = false;
        setStatus("❌ فشل الإرسال — " + (r?.error || chrome.runtime.lastError?.message || ""), "err");
        showQualityUI(); showCloseBtn();
      }
    });
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function setStatus(msg, cls) {
    const el = document.getElementById("ytdl-status");
    if (!el) return;
    el.textContent = msg;
    el.className   = msg ? "visible" + (cls ? " " + cls : "") : "";
  }

  function setProgress(pct, speed, size) {
    const bar  = document.getElementById("ytdl-progress-bar");
    const pEl  = document.getElementById("ytdl-progress-pct");
    const sEl  = document.getElementById("ytdl-progress-speed");
    const szEl = document.getElementById("ytdl-progress-size");
    if (!bar) return;
    if (pct < 0) {
      bar.style.width     = "100%";
      bar.style.animation = "ytdl-indeterminate 1.4s infinite";
      if (pEl) pEl.textContent = "جاري...";
    } else {
      bar.style.animation = "";
      bar.style.width     = pct + "%";
      if (pEl) pEl.textContent = pct + "%";
    }
    if (sEl)  sEl.textContent = speed;
    if (szEl) szEl.textContent = size;
  }

  function esc(s) {
    return String(s)
      .replace(/&/g,"&amp;").replace(/</g,"&lt;")
      .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
  }
})();