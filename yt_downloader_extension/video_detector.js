// video_detector.js — Universal video detection + network interception
(function () {
  "use strict";

  // ═══════════════════════════════════════════════════════════════
  //  CONFIG
  // ═══════════════════════════════════════════════════════════════
  const MIN_WIDTH  = 200;
  const MIN_HEIGHT = 120;
  const SCAN_INTERVAL_MS    = 2000;   // periodic scan
  const DEBOUNCE_MS         = 600;    // mutation debounce
  const MAX_MEDIA_URLS      = 200;    // prevent memory leak
  const PURGE_AGE_MS        = 30 * 60 * 1000; // 30 min

  // Known player class/id substrings
  const PLAYER_HINTS = [
    "jwplayer", "jw-media", "jw-video",
    "videojs", "vjs", "vjs-video",
    "clappr", "clappr-player",
    "plyr", "plyr__video",
    "hls", "hlsjs",
    "dash", "dashjs", "dash-player",
    "flowplayer", "fp-player",
    "kaltura", "kWidget",
    "brightcove", "videojs",
    "shaka", "shaka-player",
    "bitmovin", "bitmovinplayer",
    "p2p-media-loader",
    "ogvjs",
    "dplayer", "dplayer-video",
    "xgplayer", "xg-video",
    "artplayer",
    "chimeara",
    "m3u8", "hls-stream",
  ];

  // Fake / ad / tracking hints
  const FAKE_HINTS = [
    "captcha", "securimage", "avatar", "icon", "logo", "banner",
    "sprite", "pixel", "beacon", "placeholder", "tracking",
    "analytics", "gtag", "doubleclick", "googlesyndication",
    "adsense", "adserver", "prebid", "adroll", "taboola",
    "outbrain", "disqus", "sharethis", "addthis",
  ];

  // Social widget iframe patterns
  const SOCIAL_IFRAME_PATTERNS = [
    /facebook\.com\/plugins/i,
    /twitter\.com\/widgets/i,
    /platform\.instagram\.com/i,
    /platform\.tiktok\.com/i,
    /apis\.google\.com\/se\/0\/js\/plusone/i,
    /connect\.facebook\.net/i,
    /assets\.pinterest\.com\/js\/pinit/i,
    /cdn\.syndication\.tw/i,
    /linkedin\.com\/widgets/i,
  ];

  // Video hosting iframe patterns
  const VIDEO_IFRAME_PATTERNS = [
    /youtube\.com\/embed/i,
    /youtu\.be/i,
    /vimeo\.com\/video/i,
    /dailymotion\.com\/embed/i,
    /rumble\.com\/embed/i,
    /streamtape\.com/i,
    /mp4upload\.com/i,
    /doodstream\./i,
    /ok\.ru\/videoembed/i,
    /filemoon\./i,
    /streamwish\./i,
    /vidhide\./i,
    /vidfast\./i,
    /embedsito\./i,
    /uqload\./i,
    /mixdrop\./i,
    /supervideo\./i,
    /voe\.sx/i,
    /streamlare\./i,
    /mega\.nz\/embed/i,
    /drive\.google\.com\/file\/d\/.*\/preview/i,
    /player\.(?:php|html)/i,
    /embed\.(?:php|html)/i,
    /watch\.(?:php|html)/i,
  ];

  // Media URL patterns
  const MEDIA_URL_RE = /\.(?:mp4|m3u8|mpd|webm|mov|flv|ts)(?:[?#][^"'\s]*)?$/i;
  const MEDIA_EXT_RE = /\.(?:mp4|m3u8|mpd|webm|mov|flv|ts)/i;

  // ═══════════════════════════════════════════════════════════════
  //  STATE
  // ═══════════════════════════════════════════════════════════════
  const _mediaUrls  = new Map();   // url → timestamp
  const _seenEls    = new WeakSet(); // elements already reported
  let _scanTimer    = null;
  let _debounceTimer = null;
  let _observer     = null;
  let _intercepted  = false;

  // ═══════════════════════════════════════════════════════════════
  //  URL UTILITIES
  // ═══════════════════════════════════════════════════════════════
  function normalizeUrl(u) {
    if (!u || typeof u !== "string") return "";
    u = u.trim();
    // Unescape common JS escapes
    u = u.replace(/\u0026/g, "&").replace(/\\//g, "/")
         .replace(/&amp;/g, "&");
    // Protocol-relative
    if (u.startsWith("//")) u = location.protocol + u;
    // Relative path
    if (u.startsWith("/") && !u.startsWith("//")) {
      u = location.origin + u;
    }
    // No protocol at all
    if (!u.startsWith("http") && !u.startsWith("blob:")) {
      try { u = new URL(u, location.href).href; } catch(e) { return ""; }
    }
    return u;
  }

  function isMediaUrl(u) {
    if (!u) return false;
    // Skip data URIs (tiny inline stuff)
    if (u.startsWith("data:")) return false;
    // Check extension
    try {
      const path = new URL(u, location.href).pathname;
      if (MEDIA_EXT_RE.test(path)) return true;
    } catch(e) {}
    // Check raw string
    return MEDIA_URL_RE.test(u.split("?")[0]);
  }

  function addMediaUrl(u) {
    if (!u) return;
    u = normalizeUrl(u);
    if (!u || !isMediaUrl(u)) return;
    // Skip tiny segments (.ts fragments are not useful as standalone)
    if (/\.ts($|[?#])/i.test(u) && !/master/i.test(u)) return;
    _mediaUrls.set(u, Date.now());
    // Purge old entries
    if (_mediaUrls.size > MAX_MEDIA_URLS) purgeOld();
    // Notify content.js
    window.postMessage({ type: "YTDL_MEDIA_FOUND", url: u }, "*");
  }

  function purgeOld() {
    const now = Date.now();
    for (const [k, v] of _mediaUrls) {
      if (now - v > PURGE_AGE_MS) _mediaUrls.delete(k);
    }
  }

  function getMediaUrls() {
    purgeOld();
    return Array.from(_mediaUrls.keys());
  }

  // ═══════════════════════════════════════════════════════════════
  //  ELEMENT VALIDATION
  // ═══════════════════════════════════════════════════════════════
  function isFakeElement(el) {
    const idClass = ((el.id || "") + " " + (el.className || "")).toLowerCase();
    return FAKE_HINTS.some(h => idClass.includes(h.toLowerCase()));
  }

  function isSmallElement(el) {
    const rect = el.getBoundingClientRect();
    return rect.width < MIN_WIDTH || rect.height < MIN_HEIGHT;
  }

  function isHiddenElement(el) {
    const style = window.getComputedStyle(el);
    return style.display === "none" || style.visibility === "hidden"
        || style.opacity === "0" || parseFloat(style.opacity) < 0.05;
  }

  function isSocialIframe(src) {
    return SOCIAL_IFRAME_PATTERNS.some(p => p.test(src));
  }

  function isVideoIframe(src) {
    return VIDEO_IFRAME_PATTERNS.some(p => p.test(src));
  }

  function isAdIframe(src) {
    return /doubleclick|googlesyndication|adservice|googleads|ads\.|adserver|prebid/i.test(src);
  }

  // ═══════════════════════════════════════════════════════════════
  //  VIDEO DETECTION — scan DOM
  // ═══════════════════════════════════════════════════════════════
  function scanForVideos() {
    const results = [];

    // 1. <video> elements
    const videos = document.querySelectorAll("video");
    for (const v of videos) {
      if (_seenEls.has(v)) continue;
      if (isFakeElement(v) || isSmallElement(v) || isHiddenElement(v)) continue;

      // Check sources
      const src = v.src || v.currentSrc || "";
      if (src) {
        _seenEls.add(v);
        results.push({ type: "video", el: v, src: normalizeUrl(src) });
        addMediaUrl(src);
        continue;
      }
      // <source> children
      const sources = v.querySelectorAll("source");
      for (const s of sources) {
        const sSrc = s.src || s.getAttribute("src") || "";
        if (sSrc) {
          _seenEls.add(v);
          results.push({ type: "video", el: v, src: normalizeUrl(sSrc) });
          addMediaUrl(sSrc);
          break;
        }
      }
    }

    // 2. <audio> elements (for audio-only pages)
    const audios = document.querySelectorAll("audio");
    for (const a of audios) {
      if (_seenEls.has(a)) continue;
      if (isFakeElement(a) || isHiddenElement(a)) continue;
      const src = a.src || a.currentSrc || "";
      if (src) {
        _seenEls.add(a);
        results.push({ type: "audio", el: a, src: normalizeUrl(src) });
        addMediaUrl(src);
      }
    }

    // 3. iframes — video hosting
    const iframes = document.querySelectorAll("iframe");
    for (const f of iframes) {
      if (_seenEls.has(f)) continue;
      if (isSmallElement(f) || isHiddenElement(f)) continue;
      const src = f.src || f.getAttribute("data-src") || "";
      if (!src || src === "about:blank") continue;
      if (isSocialIframe(src) || isAdIframe(src)) continue;
      if (/\.js(\?|$)/i.test(src)) continue;

      if (isVideoIframe(src)) {
        _seenEls.add(f);
        results.push({ type: "iframe", el: f, src: normalizeUrl(src) });
      } else if (f.offsetWidth >= MIN_WIDTH && f.offsetHeight >= MIN_HEIGHT) {
        // Large generic iframe — might be a player
        _seenEls.add(f);
        results.push({ type: "iframe-generic", el: f, src: normalizeUrl(src) });
      }
    }

    // 4. Known player containers (jwplayer, videojs, etc.)
    for (const hint of PLAYER_HINTS) {
      const els = document.querySelectorAll(
        '[class*="' + hint + '"], [id*="' + hint + '"]'
      );
      for (const el of els) {
        if (_seenEls.has(el)) continue;
        if (isFakeElement(el) || isSmallElement(el) || isHiddenElement(el)) continue;
        // Find video inside
        const v = el.querySelector("video");
        if (v) {
          _seenEls.add(el);
          const src = v.src || v.currentSrc || "";
          if (src) addMediaUrl(src);
          results.push({ type: "player", el: el, src: normalizeUrl(src) });
        }
      }
    }

    // 5. Elements with data-video-src or data-src pointing to media
    const dataEls = document.querySelectorAll(
      '[data-video-src], [data-hls-src], [data-dash-src], [data-src*=".mp4"], ' +
      '[data-src*=".m3u8"], [data-src*=".webm"]'
    );
    for (const el of dataEls) {
      if (_seenEls.has(el)) continue;
      if (isFakeElement(el) || isSmallElement(el) || isHiddenElement(el)) continue;
      const src = el.dataset.videoSrc || el.dataset.hlsSrc || el.dataset.dashSrc
               || el.dataset.src || "";
      if (src && isMediaUrl(src)) {
        _seenEls.add(el);
        addMediaUrl(src);
        results.push({ type: "data-src", el: el, src: normalizeUrl(src) });
      }
    }

    // 6. og:video meta tag
    const ogVideo = document.querySelector(
      'meta[property="og:video"], meta[property="og:video:url"], ' +
      'meta[property="og:video:secure_url"]'
    );
    if (ogVideo && ogVideo.content) {
      addMediaUrl(ogVideo.content);
    }

    // 7. Schema.org video
    try {
      const ldJson = document.querySelectorAll('script[type="application/ld+json"]');
      for (const s of ldJson) {
        try {
          const data = JSON.parse(s.textContent);
          const urls = extractSchemaVideoUrls(data);
          for (const u of urls) addMediaUrl(u);
        } catch(e) {}
      }
    } catch(e) {}

    return results;
  }

  function extractSchemaVideoUrls(obj) {
    const urls = [];
    if (!obj || typeof obj !== "object") return urls;
    if (obj.contentUrl) urls.push(obj.contentUrl);
    if (obj.embedUrl) urls.push(obj.embedUrl);
    if (Array.isArray(obj)) {
      for (const item of obj) urls.push(...extractSchemaVideoUrls(item));
    }
    return urls;
  }

  // ═══════════════════════════════════════════════════════════════
  //  MUTATION OBSERVER — detect dynamically loaded videos
  // ═══════════════════════════════════════════════════════════════
  function startObserver() {
    if (_observer) return;
    _observer = new MutationObserver((mutations) => {
      let relevant = false;
      for (const m of mutations) {
        for (const node of m.addedNodes) {
          if (node.nodeType !== Node.ELEMENT_NODE) continue;
          // Check if relevant element was added
          const tag = (node.tagName || "").toLowerCase();
          if (tag === "video" || tag === "audio" || tag === "iframe" ||
              tag === "source" || tag === "object" || tag === "embed") {
            relevant = true; break;
          }
          // Check children
          if (node.querySelector) {
            if (node.querySelector("video, audio, iframe, source, embed")) {
              relevant = true; break;
            }
          }
        }
        if (relevant) break;
      }
      if (relevant) scheduleScan();
    });
    _observer.observe(document.documentElement, {
      childList: true, subtree: true
    });
  }

  function scheduleScan() {
    if (_debounceTimer) return;
    _debounceTimer = setTimeout(() => {
      _debounceTimer = null;
      const results = scanForVideos();
      if (results.length > 0) {
        window.postMessage({
          type: "YTDL_VIDEOS_DETECTED",
          count: results.length,
          sources: results.map(r => r.src).filter(Boolean)
        }, "*");
      }
    }, DEBOUNCE_MS);
  }

  // ═══════════════════════════════════════════════════════════════
  //  NETWORK INTERCEPTION
  // ═══════════════════════════════════════════════════════════════
  function interceptNetwork() {
    if (_intercepted) return;
    _intercepted = true;

    // ── fetch interception ──
    const _origFetch = window.fetch;
    window.fetch = function (...args) {
      const url = typeof args[0] === "string" ? args[0]
                : args[0] instanceof Request ? args[0].url : "";
      if (url && isMediaUrl(url)) addMediaUrl(url);
      return _origFetch.apply(this, args);
    };

    // ── XMLHttpRequest interception ──
    const _origOpen = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      if (url && isMediaUrl(url)) addMediaUrl(url);
      return _origOpen.call(this, method, url, ...rest);
    };

    // ── MediaSource interception (blob: streams) ──
    try {
      const _origAddSourceBuffer = MediaSource.prototype.addSourceBuffer;
      MediaSource.prototype.addSourceBuffer = function (mimeType) {
        // We can't get the URL from blob, but we know it's a video
        if (/^video\/|^application\/(x-mpegURL|dash\+xml)/i.test(mimeType)) {
          window.postMessage({
            type: "YTDL_MEDIA_SOURCE",
            mimeType: mimeType
          }, "*");
        }
        return _origAddSourceBuffer.call(this, mimeType);
      };
    } catch(e) {}

    // ── Performance entries (catch URLs loaded before our script) ──
    try {
      const entries = performance.getEntriesByType("resource");
      for (const entry of entries) {
        if (entry.name && isMediaUrl(entry.name)) {
          addMediaUrl(entry.name);
        }
      }
      // Observe new entries
      if (typeof PerformanceObserver !== "undefined") {
        const perfObs = new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) {
            if (entry.name && isMediaUrl(entry.name)) {
              addMediaUrl(entry.name);
            }
          }
        });
        perfObs.observe({ type: "resource", buffered: false });
      }
    } catch(e) {}
  }

  // ═══════════════════════════════════════════════════════════════
  //  HTML REGEX EXTRACTION (for pages with inline video URLs)
  // ═══════════════════════════════════════════════════════════════
  function extractFromHTML() {
    const html = document.documentElement.innerHTML;
    const patterns = [
      /<(?:video|source)[^>]+\bsrc=["']([^"']+\.(?:mp4|m3u8|webm|mpd|mov)[^"']*)["']/gi,
      /\b(?:file|src|url)\s*:\s*["']([^"']+\.(?:mp4|m3u8|webm|mpd)[^"']*)["']/gi,
      /["']([^"']{15,}\.(?:mp4|m3u8|webm|mpd)[^"'?#]{0,100})["']/gi,
    ];
    for (const pat of patterns) {
      let m;
      while ((m = pat.exec(html)) !== null) {
        addMediaUrl(m[1]);
      }
    }
  }

  // ═══════════════════════════════════════════════════════════════
  //  PUBLIC API — exposed via window.__ytdl_detector
  // ═══════════════════════════════════════════════════════════════
  window.__ytdl_detector = {
    scan: scanForVideos,
    getMediaUrls: getMediaUrls,
    addMediaUrl: addMediaUrl,
    extractFromHTML: extractFromHTML,
  };

  // ═══════════════════════════════════════════════════════════════
  //  INIT
  // ═══════════════════════════════════════════════════════════════
  interceptNetwork();
  startObserver();
  scanForVideos();
  extractFromHTML();

  // Periodic scan for SPAs and lazy-loaded content
  _scanTimer = setInterval(() => {
    scanForVideos();
  }, SCAN_INTERVAL_MS);

  // Listen for URL changes in SPAs
  let _lastHref = location.href;
  setInterval(() => {
    if (location.href !== _lastHref) {
      _lastHref = location.href;
      // Reset seen elements for new page — WeakSet has no clear()
      // Just reassign (old references will be GC'd)
      setTimeout(() => {
        scanForVideos();
        extractFromHTML();
      }, 1000);
    }
  }, 1500);

})();
