/* DriveUp Studio — entry point.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
function renderDashboard() {
  const view = $('#view');
  view.className = 'screen dashboard';
  view.innerHTML = '';
  $('#steps').innerHTML = '';
  $('#stepLabel').textContent = 'PRIVATE WORKSPACE';
  $('#bottom').innerHTML = '';
  const user = S.me?.user || {};
  const plan = S.me?.plan || {};
  const usage = S.me?.usage_today || {};
  const hero = el('section', 'card dashboard-hero');
  hero.innerHTML = `<p class="dim">ZC DRIVE UPLOAD · YOUR PRIVATE WORKSPACE</p><h1>Welcome to Studio</h1><p class="dim">Your workspace for managing transfers, account access, and recent activity. Uploading is one action—not the whole website.</p>`;
  const hello = el('p', 'dashboard-greeting', `Signed in as ${user.name || user.username || 'your account'}`);
  hero.appendChild(hello);
  if (new URLSearchParams(location.search).get('connected') === '1') {
    const connected = el('p', 'dashboard-connected', `✓ Google Drive connected${S.me?.drive_email ? ` as ${esc(S.me.drive_email)}` : ''}. Your workspace is ready.`);
    connected.setAttribute('role', 'status');
    hero.appendChild(connected);
    history.replaceState(null, '', '/studio');
  }
  const actions = el('div', 'row dashboard-actions');
  const upload = el('button', 'btn primary', '＋ Start an upload');
  upload.onclick = () => { S.dashboard = false; S.jobId = ''; S.files = []; S.step = 'files'; history.replaceState(null, '', '/studio'); render(); };
  const historyBtn = el('button', 'btn ghost', 'Transfer history');
  historyBtn.onclick = () => showUploadHistory();
  actions.append(upload, historyBtn);
  hero.appendChild(actions);
  view.appendChild(hero);

  const grid = el('div', 'dashboard-grid');
  const drive = el('section', 'card dashboard-panel');
  drive.innerHTML = `<p class="dim">CONNECTED DRIVE</p><h2>${S.me?.drive_connected ? esc(S.me.drive_email || 'Google Drive connected') : 'Drive not connected'}</h2><p class="dim">${S.me?.drive_connected ? 'Your transfers use the Google account you authorized.' : 'Connect your Google account to use Drive features.'}</p>`;
  if (!S.me?.drive_connected) { const connect = el('a', 'btn primary', 'Connect Google Drive'); connect.href = '/auth/google/start'; drive.appendChild(connect); }
  const planCard = el('section', 'card dashboard-panel');
  planCard.innerHTML = `<p class="dim">CURRENT PLAN</p><h2>${esc(plan.name || 'Plan')}</h2><p class="dim">Limits apply to uploads: ${plan.max_files_job ? `${Number(plan.max_files_job).toLocaleString()} files per job` : 'see account limits'}${plan.max_file_mb ? ` · ${Number(plan.max_file_mb).toLocaleString()} MB per file` : ''}.</p>`;
  const usageCard = el('section', 'card dashboard-panel');
  usageCard.innerHTML = `<p class="dim">TODAY’S USAGE</p><h2>${humanBytes(Number(usage.bytes || 0))}</h2><p class="dim">${Number(usage.files || 0).toLocaleString()} file(s) recorded today. Plan and Drive quotas still apply.</p>`;
  grid.append(drive, planCard, usageCard);
  view.appendChild(grid);

  const section = el('section', 'card dashboard-panel');
  section.innerHTML = '<div class="dashboard-section-head"><div><p class="dim">YOUR ACTIVITY</p><h2>Recent transfers</h2></div><button class="btn ghost dashboard-history" type="button">View all</button></div><div class="dashboard-jobs dim">Loading your jobs…</div>';
  $('.dashboard-history', section).onclick = () => showUploadHistory();
  view.appendChild(section);
  api('/api/jobs?limit=5').then((result) => {
    const box = $('.dashboard-jobs', section);
    const jobs = result.jobs || [];
    if (!jobs.length) { box.innerHTML = '<p>No transfers yet. Your private workspace is ready when you are.</p>'; return; }
    box.innerHTML = '';
    jobs.forEach((job) => {
      const row = el('div', 'dashboard-job');
      const totals = job.totals || {};
      const details = el('div', 'dashboard-job-info');
      details.appendChild(el('strong', '', `${esc(job.status || 'Transfer')} · ${Number(totals.total || totals.items || 0).toLocaleString()} file(s)`));
      details.appendChild(el('span', 'dim', job.destination?.path ? esc(job.destination.path) : 'Google Drive transfer'));
      const open = el('a', 'btn ghost', 'View job');
      open.href = `/studio?job=${encodeURIComponent(job.id)}`;
      row.append(details, open); box.appendChild(row);
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
