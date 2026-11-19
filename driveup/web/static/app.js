/* ZC Drive Uploader — private Studio workspace and entry point. */
function startNewTransfer(files) {
  S.dashboard = false;
  S.jobId = '';
  S.source = {};
  S.files = [];
  S.folder = { id: 'root', path: 'My Drive' };
  S.selected = 'root';
  S.split = null;
  S.folderGroups = [];
  S.fileRoutes = {};
  S.manualFolders = [];
  S.manualRoutes = {};
  S.step = 'files';
  history.replaceState(null, '', '/studio');
  render();
  if (files?.length) {
    uploadBrowserFiles(files, $('#browserIntakeStatus'), $('#browserPick'), $('#browserIntakeProgress'));
  }
}

function renderDashboard() {
  const view = $('#view');
  view.className = 'screen dashboard';
  view.innerHTML = '';
  $('#steps').innerHTML = '';
  $('#stepLabel').textContent = 'YOUR WORKSPACE';
  $('#bottom').innerHTML = '';
  $('#bottom').classList.add('hide');
  const user = S.me?.user || {};
  const plan = S.me?.plan || {};
  const usage = S.me?.usage_today || {};
  const stats = S.me?.upload_stats || {};
  const connected = !!S.me?.drive_connected;
  const name = esc(user.name || user.username || 'there');
  const hero = el('section', 'dashboard-welcome');
  hero.innerHTML = `
    <div class="dashboard-welcome-copy">
      <div class="dashboard-eyebrow"><span class="dashboard-spark">✦</span> YOUR PRIVATE WORKSPACE</div>
      <h1>All your transfers.<br><span>One little space.</span></h1>
      <p>Hey ${name}, welcome to your Studio. Collect files, choose exactly where they go, and follow every transfer.</p>
      <div class="dashboard-hero-actions"></div>
    </div>
    <div class="dashboard-orbit" aria-hidden="true"><span class="orbit orbit-one"></span><span class="orbit orbit-two"></span><span class="orbit-core">↗</span><span class="orbit-dot dot-one"></span><span class="orbit-dot dot-two"></span><span class="orbit-file">▤</span></div>`;
  const actions = $('.dashboard-hero-actions', hero);
  const upload = el('button', 'btn primary', '＋ New transfer');
  upload.type = 'button'; upload.onclick = () => startNewTransfer();
  const historyBtn = el('button', 'btn ghost', 'View history ↗');
  historyBtn.type = 'button'; historyBtn.onclick = () => showUploadHistory();
  actions.append(upload, historyBtn);
  view.appendChild(hero);

  if (new URLSearchParams(location.search).get('connected') === '1') {
    const notice = el('div', 'dashboard-connected', `✓ Google Drive connected${S.me?.drive_email ? ` as ${esc(S.me.drive_email)}` : ''}. Your workspace is ready.`);
    notice.setAttribute('role', 'status'); view.appendChild(notice);
    history.replaceState(null, '', '/studio');
  }

  const overview = el('section', 'dashboard-overview');
  overview.setAttribute('aria-label', 'Workspace overview');
  const stat = (icon, label, value, note, extra = '') => {
    const node = el('div', 'dashboard-stat');
    node.innerHTML = `<div class="dashboard-stat-top"><span class="dashboard-stat-icon">${icon}</span><span class="dashboard-stat-label">${label}</span></div><strong>${value}</strong><small>${note}</small>${extra}`;
    return node;
  };
  overview.append(
    stat('↗', 'FILES UPLOADED', Number(stats.files || 0).toLocaleString(), 'Completed transfers'),
    stat('◈', 'TOTAL UPLOADED', humanBytes(stats.bytes), 'Across completed files'),
    stat('◷', 'TODAY’S USAGE', humanBytes(usage.bytes), `${Number(usage.files || 0).toLocaleString()} files today`),
    stat('◎', 'DRIVE STORAGE', connected ? 'Checking…' : 'Not connected', connected ? 'Loading available storage' : 'Connect to see storage', '',)
  );
  const storage = overview.lastElementChild;
  view.appendChild(overview);
  if (connected) api('/api/drive/about').then((about) => {
    const val = $('strong', storage), note = $('small', storage);
    if (!val || !storage.isConnected) return;
    if (about.usage_reported) {
      val.textContent = humanBytes(about.usage);
      note.textContent = about.limit > 0 ? `of ${humanBytes(about.limit)} Google storage` : 'Google storage used · limit unavailable';
    } else { val.textContent = 'Unavailable'; note.textContent = 'Storage usage not reported by Google'; }
  }).catch(() => { if (storage.isConnected) { $('strong', storage).textContent = 'Unavailable'; $('small', storage).textContent = 'Could not load Google storage'; } });

  const columns = el('div', 'dashboard-columns');
  const quick = el('section', 'dashboard-card dashboard-quick');
  quick.innerHTML = `<div class="dashboard-card-head"><div><div class="dashboard-eyebrow">LET’S GET STARTED</div><h2>Quick upload</h2></div><span class="dashboard-card-icon">↗</span></div><p>Choose files from your device. You’ll review the order and destination before uploading to Drive.</p>`;
  const zone = el('div', 'dashboard-dropzone');
  zone.innerHTML = `<div class="dashboard-cloud">☁</div><strong>Drop files here</strong><span>or choose them from your device</span>`;
  const input = document.createElement('input'); input.type = 'file'; input.multiple = true; input.className = 'sr-only'; input.setAttribute('aria-label', 'Choose files for a new transfer');
  const choose = el('button', 'btn primary', 'Browse files'); choose.type = 'button';
  choose.onclick = () => { if (!connected) return toast('Connect Google Drive to stage files.', 'err'); input.click(); };
  input.onchange = () => { const files = Array.from(input.files || []); if (files.length) startNewTransfer(files); };
  zone.addEventListener('dragover', (event) => { event.preventDefault(); zone.classList.add('dragging'); });
  zone.addEventListener('dragleave', () => zone.classList.remove('dragging'));
  zone.addEventListener('drop', (event) => { event.preventDefault(); zone.classList.remove('dragging'); if (!connected) return toast('Connect Google Drive to stage files.', 'err'); const files = Array.from(event.dataTransfer?.files || []); if (files.length) startNewTransfer(files); });
  zone.append(choose, input); quick.appendChild(zone);
  quick.appendChild(el('p', 'dashboard-footnote', 'Files are staged in batches. Plan, storage and service limits apply.'));

  const account = el('section', 'dashboard-card dashboard-account');
  const email = connected ? esc(S.me?.drive_email || 'Your Google account') : 'Not connected';
  account.innerHTML = `<div class="dashboard-card-head"><div><div class="dashboard-eyebrow">YOUR CONNECTION</div><h2>Google Drive</h2></div><span class="dashboard-card-icon">◈</span></div>
    <div class="dashboard-account-line"><span class="dashboard-service-icon">▲</span><div><strong>${email}</strong><small>${connected ? 'Ready for your transfers' : 'Connect your account to get started'}</small></div><span class="dashboard-status ${connected ? 'online' : ''}">${connected ? 'Connected' : 'Offline'}</span></div>
    <div class="dashboard-plan"><span>YOUR PLAN</span><strong>${esc(plan.name || 'Plan')}</strong><small>${plan.max_files_job ? `${Number(plan.max_files_job).toLocaleString()} files / job` : 'See account limits'}${plan.max_file_mb ? ` · ${Number(plan.max_file_mb).toLocaleString()} MB / file` : ''}</small></div>`;
  const acctAction = el('a', 'dashboard-inline-action', connected ? 'Open Google Drive ↗' : 'Connect Google Drive ↗');
  acctAction.href = connected ? 'https://drive.google.com/' : '/auth/google/start';
  if (connected) { acctAction.target = '_blank'; acctAction.rel = 'noopener noreferrer'; }
  account.appendChild(acctAction);
  columns.append(quick, account); view.appendChild(columns);

  const section = el('section', 'dashboard-card dashboard-activity');
  section.innerHTML = '<div class="dashboard-card-head"><div><div class="dashboard-eyebrow">PICK UP WHERE YOU LEFT OFF</div><h2>Recent transfers</h2><p>Your latest jobs, all in one place.</p></div><button class="dashboard-view-all" type="button">View all ↗</button></div><div class="dashboard-jobs" aria-live="polite"><p>Loading your transfers…</p></div>';
  $('.dashboard-view-all', section).onclick = () => showUploadHistory();
  view.appendChild(section);
  api('/api/jobs?limit=5').then((result) => {
    const box = $('.dashboard-jobs', section);
    if (!box || !section.isConnected) return;
    const jobs = result.jobs || [];
    if (!jobs.length) { box.innerHTML = '<div class="dashboard-empty"><span>↗</span><strong>Nothing here yet</strong><p>Your transfers will show up here when you create your first job.</p></div>'; return; }
    box.innerHTML = '';
    jobs.forEach((job) => {
      const row = el('a', 'dashboard-job');
      row.href = `/studio?job=${encodeURIComponent(job.id)}`;
      const totals = job.totals || {};
      const files = Number(totals.files || 0);
      const status = String(job.status || 'unknown').replaceAll('_', ' ');
      const dt = job.created_at ? new Date(Number(job.created_at) * 1000) : null;
      const date = dt && !Number.isNaN(dt.valueOf()) ? dt.toLocaleDateString() : 'Date unavailable';
      row.innerHTML = `<span class="dashboard-job-icon">▤</span><span class="dashboard-job-info"><strong>${esc(job.destination?.path || 'Google Drive transfer')}</strong><small>${files.toLocaleString()} file${files === 1 ? '' : 's'} · ${esc(date)}</small></span><span class="dashboard-job-status">${esc(status)}</span><span class="dashboard-job-arrow">↗</span>`;
      box.appendChild(row);
    });
  }).catch((error) => { const box = $('.dashboard-jobs', section); if (box) box.textContent = error.message || 'Could not load recent transfers.'; });
}

window.DU = { S, api, go, render, renderDashboard, toast, cmpNatural, humanBytes, refreshPreview,
              startUpload, attachSSE, fireConfetti, setRing, naturalKey };

document.addEventListener('DOMContentLoaded', () => {
  wireGlobal();
  const historyButton = $('#historyBtn');
  if (historyButton) historyButton.addEventListener('click', showUploadHistory);
  boot();
});
