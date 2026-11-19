/* DriveUp Studio — entry point.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
function renderDashboard() {
  const view = $('#view');
  view.className = 'screen dashboard';
  view.innerHTML = '';
  $('#steps').innerHTML = '';
  $('#stepLabel').textContent = 'YOUR WORKSPACE';
  $('#bottom').innerHTML = '';
  const top = el('section', 'card', '<p class="dim">ZC DRIVE UPLOAD · PRIVATE WORKSPACE</p><h1>Your Studio</h1><p class="dim">Manage your transfers here. Start an upload when you are ready, or review your recent jobs.</p>');
  const actions = el('div', 'row dashboard-actions');
  const upload = el('button', 'btn primary', '＋ Start an upload');
  upload.onclick = () => { S.dashboard = false; S.jobId = ''; S.files = []; S.step = 'files'; history.replaceState(null, '', '/studio'); render(); };
  const historyBtn = el('button', 'btn ghost', 'Past uploads');
  historyBtn.onclick = () => showUploadHistory();
  actions.append(upload, historyBtn);
  top.appendChild(actions);
  view.appendChild(top);
  const section = el('section', 'card');
  section.innerHTML = '<h2>Recent transfers</h2><p class="dim">Only your account’s jobs appear here.</p><div class="dashboard-jobs dim">Loading your jobs…</div>';
  view.appendChild(section);
  api('/api/jobs?limit=5').then((result) => {
    const box = $('.dashboard-jobs', section);
    const jobs = result.jobs || [];
    if (!jobs.length) { box.textContent = 'No transfers yet. Start an upload to create your first job.'; return; }
    box.innerHTML = '';
    jobs.forEach((job) => {
      const row = el('div', 'dashboard-job');
      const totals = job.totals || {};
      const title = el('strong', '', `${esc(job.status || 'Transfer')} · ${Number(totals.total || totals.items || 0)} file(s)`);
      const open = el('a', 'btn ghost', 'Open');
      open.href = `/studio?job=${encodeURIComponent(job.id)}`;
      row.append(title, open); box.appendChild(row);
    });
  }).catch((error) => { const box = $('.dashboard-jobs', section); if (box) box.textContent = error.message; });
  tintButtons(view);
}

// ── exports for tests (the page works without them) ────────────────────────
window.DU = { S, api, go, render, renderDashboard, toast, cmpNatural, humanBytes, refreshPreview,
              startUpload, attachSSE, fireConfetti, setRing, naturalKey };

document.addEventListener('DOMContentLoaded', () => {
  wireGlobal();
  const historyButton = $('#historyBtn');
  if (historyButton) historyButton.addEventListener('click', showUploadHistory);
  boot();
});
