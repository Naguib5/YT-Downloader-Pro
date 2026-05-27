chrome.runtime.sendMessage({ type: "PING_HOST" }, (r) => {
  const dot = document.getElementById("srv-dot");
  const txt = document.getElementById("srv-txt");
  if (r && r.ok) {
    dot.className = "on";
    txt.textContent = "✅ الهوست جاهز وشغال";
  } else {
    dot.className = "off";
    txt.textContent = "❌ شغّل install.bat أولاً";
  }
});
