/* DriveUp Studio — entry point.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
// ── exports for tests (the page works without them) ────────────────────────
window.DU = { S, api, go, render, toast, cmpNatural, humanBytes, refreshPreview,
              startUpload, attachSSE, fireConfetti, setRing, naturalKey };

document.addEventListener('DOMContentLoaded', () => {
  wireGlobal();
  const historyButton = $('#historyBtn');
  if (historyButton) historyButton.addEventListener('click', showUploadHistory);
  boot();
});
