// background.js v11 — auto register extension ID + fixed: bfcache errors, browse_folder, duplicate guard
const HOST = "com.ytdl.pro";
let nativePort    = null;
let isDownloading = false;

chrome.runtime.onInstalled.addListener(() => {
  console.log("YT Downloader Pro v11");
  _registerExtensionId();   // ← أول تثبيت
});

chrome.runtime.onStartup.addListener(() => {
  _registerExtensionId();   // ← كل مرة يشتغل Chrome
});

/**
 * يبعت الـ Extension ID للـ native host عشان يحدّث الـ manifest أوتوماتيك.
 * بيشتغل في الخلفية بدون أي تأثير على اليوزر.
 */
function _registerExtensionId() {
  try {
    const port = chrome.runtime.connectNative(HOST);
    port.postMessage({
      type:         "register_extension",
      extension_id: chrome.runtime.id
    });
    // سيب الـ port يتقفل لوحده بعد الرسالة
    setTimeout(() => { try { port.disconnect(); } catch(e) {} }, 3000);
  } catch (e) {
    // الـ host مش شغال دلوقتي — مش مشكلة، هيتسجل المرة الجاية
    console.log("register_extension: host not ready yet —", e.message);
  }
}

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "ytdl-keepalive") return;
  port.onDisconnect.addListener(() => void chrome.runtime.lastError);
});

function getNativePort() {
  if (nativePort) return nativePort;
  try {
    nativePort = chrome.runtime.connectNative(HOST);

    nativePort.onMessage.addListener((msg) => {
      console.log("Native →", msg.type);
      if (msg.type === "done" || msg.type === "error") isDownloading = false;
      // ✅ broadcast only to active tabs to avoid bfcache errors
      broadcastToActiveTabs(msg);
    });

    nativePort.onDisconnect.addListener(() => {
      const err = chrome.runtime.lastError?.message || "disconnected";
      console.log("Native disconnected:", err);
      isDownloading = false;
      nativePort = null;
      broadcastToActiveTabs({ type: "error", error: "⚠ انقطع الاتصال بالهوست — " + err });
    });

    return nativePort;
  } catch (e) {
    console.error("connectNative failed:", e);
    nativePort = null;
    return null;
  }
}

function broadcastToActiveTabs(payload) {
  // ✅ فقط التابات النشطة، تجاهل الأخطاء بهدوء
  chrome.tabs.query({ active: true }, (tabs) => {
    for (const tab of (tabs || [])) {
      if (!tab.id || tab.url?.startsWith("chrome://")) continue;
      chrome.tabs.sendMessage(tab.id, { type: "NATIVE_MSG", payload }, () => {
        void chrome.runtime.lastError;
      });
    }
  });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "PING_HOST") {
    try {
      const p = getNativePort();
      if (!p) { sendResponse({ ok: false, error: "Cannot connect to native host" }); return true; }
      p.postMessage({ type: "ping" });
      sendResponse({ ok: true });
    } catch (e) {
      sendResponse({ ok: false, error: e.message });
    }
    return true;
  }

  if (msg.type === "NATIVE_SEND") {
    const payload  = msg.payload || {};
    const isBrowse = payload.type === "browse_folder";

    if (!isBrowse && isDownloading) {
      sendResponse({ ok: true }); // ignore duplicate download
      return true;
    }
    try {
      const p = getNativePort();
      if (!p) { sendResponse({ ok: false, error: "Cannot connect to native host" }); return true; }
      if (!isBrowse) isDownloading = true;
      p.postMessage(payload);
      sendResponse({ ok: true });
    } catch (e) {
      if (!isBrowse) isDownloading = false;
      sendResponse({ ok: false, error: e.message });
    }
    return true;
  }

  return true;
});