/* DriveUp Studio — boot, blockers, wizard chrome, steps 1–2.
   Classic script (no bundler); all app-*.js files share one global scope. */
// ── loading screen (real boot steps, min 600 ms) ────────────────────────────
const SPLASH_STEPS = [
  [0.15, 'Opening your Studio…'],
  [0.40, 'Verifying your sign-in…'],
  [0.62, 'Checking Google Drive access…'],
  [0.84, 'Loading your workspace…'],
  [1.00, 'Ready'],
];

function splashProgress(pct, label) {
  const fill = $('#splash .fill');
  if (fill) fill.style.width = `${Math.round(pct * 100)}%`;
  const st = $('#splash .splash-status');
  if (st && label && st.textContent !== label) {
    st.classList.add('fade');
    setTimeout(() => { st.textContent = label; st.classList.remove('fade'); }, 120);
  }
}

async function boot() {
  const t0 = performance.now();
  S.reduced = !motionOK();
  splashProgress(0.05, SPLASH_STEPS[0][1]);
  let step = 0;
  const bump = () => {
    const [p, label] = SPLASH_STEPS[Math.min(step, SPLASH_STEPS.length - 1)];
    splashProgress(p, label);
    step++;
  };
  bump();
  try {
    const tg = window.Telegram?.WebApp;
    if (tg) { try { tg.ready(); tg.expand(); } catch (_) {} }
    const jobId = new URLSearchParams(location.search).get('job') || '';
    S.jobId = jobId;
    bump();
    S.me = await api('/api/me' + (jobId ? `?job=${encodeURIComponent(jobId)}` : ''));
    bump();
    if (S.me.blocked) { showBlocker(S.me.blocked); return; }
    if (S.me.job) {
      S.jobId = S.me.job.id || S.jobId;
      hydrate(S.me.job);
    }
    bump();
    $('#who').textContent = S.me.user?.username ? '@' + S.me.user.username
      : (S.me.user?.name || `id ${S.me.userId}`);
    const brand = S.me.brand || 'ZC Drive Uploader';
    $('#brandName').textContent = brand.toUpperCase();
    const topBrand = $('#brandNameTop');
    if (topBrand) topBrand.textContent = brand.toUpperCase();
    document.title = `${brand} · DriveUp Studio`;
    if (S.me.progress_style) document.documentElement.dataset.bar = S.me.progress_style;
    await loadTree(S.folder.id);
    bump();
    render();
    const wait = Math.max(0, 600 - (performance.now() - t0));
    await new Promise((res) => setTimeout(res, wait));
    splashProgress(1, 'Ready');
    $('#splash').classList.add('gone');
    document.body.dataset.ready = '1';
  } catch (e) {
    $('#splash').classList.remove('gone');
    splashProgress(1, 'Something went wrong');
    const box = $('#splash .splash-inner');
    box.appendChild(el('p', 'hint bad', esc(e.message)));
    const btn = el('button', 'btn primary', '↻ Retry');
    btn.onclick = () => location.reload();
    box.appendChild(btn);
    toast(e.message, 'err');
  }
}

function hydrate(job) {
  S.source = job.source || {};
  S.files = (job.items || []).map((it) => ({
    id: it.id, name: it.name, size: it.size, mime: it.mime,
    msg_id: it.msg_id, caption: it.caption || '',
  }));
  if (job.destination?.folder_id) {
    S.folder = { id: job.destination.folder_id, path: job.destination.path || 'My Drive' };
    S.split = (job.destination && job.destination.split) || null;
    S.folderGroups = (S.split && S.split.groups) || [];
    S.selected = job.destination.folder_id;
  }
  if (job.naming) Object.assign(S.naming, job.naming);
  if (job.sequence_mode) S.sequence.mode = job.sequence_mode;
  if (typeof job.strict_order === 'boolean') S.sequence.strict = job.strict_order;
  if (job.conflict_policy) S.conflict = job.conflict_policy;
  if (job.share_mode) S.share = job.share_mode;
  if (job.status && ['running', 'paused', 'queued', 'done'].includes(job.status)) {
    S.step = job.status === 'done' ? 'done' : 'live';
  }
}

// ── blockers (lock / maintenance) ───────────────────────────────────────────
function showBlocker(b) {
  $('#splash').classList.add('gone');
  const wrap = el('div', 'blocker');
  const inner = el('div', 'inner');
  inner.appendChild(el('div', 'icon', b.kind === 'locked' ? '🔒' : '🛠'));
  inner.appendChild(el('h1', '', esc(b.title || 'Blocked')));
  inner.appendChild(el('p', '', esc(b.message || '')));
  if (b.until) {
    const cd = el('div', 'countdown', '');
    inner.appendChild(cd);
    const tick = () => {
      const left = Math.max(0, Math.floor(b.until - Date.now() / 1000));
      cd.textContent = left ? `unlocks in ${humanSecs(left)}` : 'unlocked — reload';
      if (!left) { clearInterval(iv); const btn = el('button', 'btn primary', '↻ Reload');
        btn.onclick = () => location.reload(); inner.appendChild(btn); }
    };
    tick();
    const iv = setInterval(tick, 1000);
  }
  if (b.support) inner.appendChild(el('p', 'hint', 'Contact: ' + esc(b.support)));
  wrap.appendChild(inner);
  document.body.appendChild(wrap);
  document.body.dataset.blocked = b.kind || '1';
}

function showAuthWall(msg) {
  if ($('#authwall')) return;
  const wrap = el('div', 'blocker');
  wrap.id = 'authwall';
  const inner = el('div', 'inner');
  inner.appendChild(el('div', 'icon', '🔐'));
  inner.appendChild(el('h1', '', 'Your Studio session ended'));
  inner.appendChild(el('p', '', esc(msg || 'Sign in again to continue.')));
  const login = el('a', 'btn primary', 'Continue with Google Drive');
  login.href = '/auth/google/login';
  inner.appendChild(login);
  inner.appendChild(el('p', 'hint', 'You can use the Studio directly on this website; Telegram is optional.'));
  wrap.appendChild(inner);
  document.body.appendChild(wrap);
}

// ── wizard chrome ───────────────────────────────────────────────────────────
const STEP_LABELS = {
  files: 'Files', folder: 'Folder', naming: 'Naming', sequence: 'Sequence',
  review: 'Review', live: 'Progress', done: 'Done',
};

function renderSteps() {
  const bar = $('#steps');
  bar.innerHTML = '';
  const idx = S.steps.indexOf(S.step);
  S.steps.forEach((s, i) => {
    const d = el('div', 'step-dot' + (i < idx ? ' done' : i === idx ? ' active' : ''));
    d.title = STEP_LABELS[s];
    d.dataset.step = s;
    bar.appendChild(d);
  });
  $('#stepLabel').textContent = `${idx + 1}/${S.steps.length} · ${STEP_LABELS[S.step]}`;
}

function go(step, back = false) {
  if (!S.steps.includes(step)) return;
  S.step = step;
  render(back);
  window.scrollTo({ top: 0, behavior: motionOK() ? 'smooth' : 'auto' });
}

// ── render root ─────────────────────────────────────────────────────────────
function tintButtons(root = document) {
  const terms = (node) => `${node.textContent || ''} ${node.title || ''} ${node.id || ''}`.toLowerCase();
  const candidates = root.matches?.('button.btn, a.btn')
    ? [root, ...root.querySelectorAll('button.btn, a.btn')]
    : (root.querySelectorAll?.('button.btn, a.btn') || []);
  candidates.forEach((node) => {
    node.classList.remove('primary', 'success', 'danger');
    const label = terms(node);
    if (/cancel|delete|remove|disconnect|clear|stop|danger/.test(label)) node.classList.add('danger');
    else if (/start|upload|connect|confirm|save|retry|resume|share|next|apply|open folder/.test(label)) node.classList.add('success');
    else node.classList.add('primary');
  });
}

// App views are rendered dynamically; tint new buttons as they enter the DOM.
const tintObserver = new MutationObserver((records) => {
  for (const record of records) for (const node of record.addedNodes) {
    if (node.nodeType === 1) tintButtons(node);
  }
});
if (document.body) tintObserver.observe(document.body, { childList: true, subtree: true });

function render(back = false) {
  renderSteps();
  const view = $('#view');
  view.className = 'screen' + (back ? ' back' : '');
  view.innerHTML = '';
  const fn = {
    files: viewFiles, folder: viewFolder, naming: viewNaming, sequence: viewSequence,
    review: viewReview, live: viewLive, done: viewDone,
  }[S.step];
  fn(view);
  if (S.step === 'live' && S.jobId) attachSSE();
  renderBottom();
}

function renderBottom() {
  const bar = $('#bottom');
  bar.innerHTML = '';
  const backBtn = () => {
    const i = S.steps.indexOf(S.step);
    if (i > 0 && S.step !== 'live') {
      const b = el('button', 'btn ghost', '‹ Back');
      b.onclick = () => go(S.steps[i - 1], true);
      return b;
    }
    return null;
  };
  const b = backBtn();
  if (b) bar.appendChild(b);
  const grow = el('div', 'grow');
  bar.appendChild(grow);

  if (S.step === 'files') {
    const n = el('button', 'btn primary', `Next: folder ›`);
    n.onclick = () => go('folder');
    n.disabled = S.files.length === 0;
    bar.appendChild(n);
  } else if (S.step === 'folder') {
    const n = el('button', 'btn primary', 'Next: naming ›');
    n.onclick = () => {
      if ($('#splitMode')?.value === 'batches') {
        const split = currentSplit();
        if (!split) return toast(`Use unique folder names, positive counts, and assign exactly ${S.files.length} files.`, 'err');
        S.split = split;
      } else S.split = currentSplit();
      go('naming');
    };
    bar.appendChild(n);
  } else if (S.step === 'naming') {
    const n = el('button', 'btn primary', 'Next: sequence ›');
    n.onclick = async () => { await refreshPreview(); go('sequence'); };
    bar.appendChild(n);
  } else if (S.step === 'sequence') {
    const n = el('button', 'btn primary', 'Next: review ›');
    n.onclick = async () => { await refreshPreview(); go('review'); };
    bar.appendChild(n);
  } else if (S.step === 'review') {
    const start = el('button', 'btn primary', '🚀 Start upload');
    start.id = 'startBtn';
    start.onclick = startUpload;
    bar.appendChild(start);
  } else if (S.step === 'live') {
    const stop = el('button', 'btn danger', 'Cancel');
    stop.onclick = cancelJob;
    bar.appendChild(stop);
  } else if (S.step === 'done') {
    const more = el('button', 'btn primary', '📤 Upload more');
    more.onclick = () => {
      S.jobId = ''; S.files = []; S.folder = { id: 'root', path: 'My Drive' };
      history.replaceState(null, '', '/studio');
      go('files');
    };
    bar.appendChild(more);
  }
  bar.classList.toggle('hide', S.step === 'done' ? false : false);
}

// ── step 1: files ───────────────────────────────────────────────────────────
function typeIcon(mime, name) {
  const m = (mime || '').toLowerCase();
  const ext = (name.split('.').pop() || '').toLowerCase();
  if (m.startsWith('video/')) return ['🎬', 'video'];
  if (m.startsWith('audio/')) return ['🎵', 'audio'];
  if (m.startsWith('image/')) return ['🖼', 'image'];
  if (m === 'application/pdf' || ext === 'pdf') return ['📕', 'pdf'];
  if (['zip', 'rar', '7z', 'gz'].includes(ext)) return ['🗜', 'archive'];
  return ['📄', 'doc'];
}

function postMultipartWithProgress(url, form, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('POST', url);
    xhr.withCredentials = true;
    const initData = window.Telegram?.WebApp?.initData;
    if (initData) xhr.setRequestHeader('X-Telegram-Init-Data', initData);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) onProgress(event.loaded / Math.max(1, event.total));
    };
    xhr.onerror = () => reject(new Error('Network interrupted while sending this batch.'));
    xhr.onload = () => {
      let data = {};
      try { data = JSON.parse(xhr.responseText || '{}'); } catch (_) {}
      if (xhr.status >= 200 && xhr.status < 300 && data.ok) return resolve(data);
      const message = data.message || `Upload request failed (${xhr.status}).`;
      if (xhr.status === 401) showAuthWall(message);
      reject(new Error(message));
    };
    xhr.send(form);
  });
}

async function uploadBrowserFiles(fileList, statusNode, button, progressBar) {
  const chosen = Array.from(fileList || []);
  if (!chosen.length) return;
  const totalBytes = chosen.reduce((sum, file) => sum + file.size, 0);
  const plan = S.me?.plan || {};
  const maxFiles = Number(plan.max_files_job || 5000);
  if (S.files.length + chosen.length > maxFiles) {
    return toast(`This job would exceed your ${maxFiles}-file plan limit.`, 'err');
  }
  const maxFileBytes = Number(plan.max_file_mb || 0) * 1024 ** 2;
  const tooLarge = chosen.find((file) => maxFileBytes && file.size > maxFileBytes);
  if (tooLarge) return toast(`${tooLarge.name} exceeds your ${plan.max_file_mb} MB per-file limit.`, 'err');
  const dailyBytes = Number(plan.daily_gb || 0) * 1024 ** 3;
  const alreadyUsed = Number(S.me?.usage_today?.bytes || 0) + S.files.reduce((sum, file) => sum + file.size, 0);
  if (dailyBytes && alreadyUsed + totalBytes > dailyBytes) {
    return toast('This selection exceeds the remaining daily upload allowance.', 'err');
  }
  const chunkSize = 20;
  let completedFiles = 0, completedBytes = 0;
  button.disabled = true;
  progressBar.hidden = false;
  progressBar.setAttribute('aria-valuenow', '0');
  progressBar.querySelector('.browser-progress-fill').style.width = '0%';
  try {
    for (let offset = 0; offset < chosen.length; offset += chunkSize) {
      const batch = chosen.slice(offset, offset + chunkSize);
      const batchBytes = batch.reduce((sum, file) => sum + file.size, 0);
      const form = new FormData();
      for (const file of batch) form.append('files', file, file.name);
      if (S.jobId) form.append('job_id', S.jobId);
      statusNode.textContent = `Sending files ${offset + 1}–${offset + batch.length} of ${chosen.length} to your private Studio…`;
      const result = await postMultipartWithProgress('/api/uploads', form, (fraction) => {
        const pct = totalBytes ? Math.round((completedBytes + fraction * batchBytes) / totalBytes * 100)
          : Math.round((completedFiles + fraction * batch.length) / chosen.length * 100);
        progressBar.setAttribute('aria-valuenow', String(pct));
        progressBar.querySelector('.browser-progress-fill').style.width = `${pct}%`;
        statusNode.textContent = `Sending ${Math.min(chosen.length, completedFiles + batch.length)} of ${chosen.length} files · ${pct}%`;
      });
      S.jobId = result.job.id;
      hydrate(result.job);
      completedFiles += batch.length;
      completedBytes += batchBytes;
    }
    history.replaceState(null, '', `/studio?job=${encodeURIComponent(S.jobId)}`);
    statusNode.textContent = `${completedFiles} file(s) are ready. Review folder assignments, naming and order, then start the upload.`;
    progressBar.setAttribute('aria-valuenow', '100');
    progressBar.querySelector('.browser-progress-fill').style.width = '100%';
    render();
    toast('Files added to your Studio job');
  } catch (error) {
    const message = `${completedFiles} of ${chosen.length} files were added. This draft is saved; select only the remaining files to continue. ${error.message || ''}`;
    statusNode.textContent = message;
    statusNode.classList.add('bad');
    render();
    toast(message, 'err');
  } finally {
    button.disabled = false;
  }
}

function viewFiles(view) {
  const intake = el('section', 'card browser-intake');
  const intakeTitle = el('h2', '', 'Upload from this device');
  const intakeHelp = el('p', 'dim', 'Choose files here, then review the destination and options before starting the transfer.');
  const fileInput = document.createElement('input');
  fileInput.type = 'file';
  fileInput.multiple = true;
  fileInput.id = 'browserFileInput';
  fileInput.className = 'sr-only';
  fileInput.setAttribute('aria-label', 'Choose files to upload');
  const pick = el('button', 'btn primary', 'Choose files');
  pick.type = 'button';
  pick.onclick = () => fileInput.click();
  const intakeStatus = el('p', 'hint', 'Selected files stay private to your signed-in workspace and are sent to your connected Drive. Large selections are transferred in small batches.');
  intakeStatus.setAttribute('role', 'status');
  const progressBar = el('div', 'browser-progress');
  progressBar.hidden = true;
  progressBar.setAttribute('role', 'progressbar');
  progressBar.setAttribute('aria-label', 'File selection transfer to Studio');
  progressBar.setAttribute('aria-valuemin', '0'); progressBar.setAttribute('aria-valuemax', '100');
  progressBar.setAttribute('aria-valuenow', '0');
  progressBar.innerHTML = '<span class="browser-progress-fill"></span>';
  fileInput.onchange = () => uploadBrowserFiles(fileInput.files, intakeStatus, pick, progressBar);
  intake.append(intakeTitle, intakeHelp, pick, fileInput, progressBar, intakeStatus);
  view.append(intake);
  const total = S.files.reduce((a, f) => a + f.size, 0);
  const head = el('div', 'card');
  head.innerHTML = `
    <div class="row">
      <div class="grow">
        <h2>Files in this job</h2>
        <p class="dim">${S.files.length} file(s) · ${humanBytes(total)}</p>
      </div>
      <span class="pill info" id="fileCountPill">${S.files.length} files</span>
    </div>`;
  view.appendChild(head);

  const tools = el('div', 'card tight');
  tools.innerHTML = `
    <div class="row tight">
      <input type="search" id="fsearch" placeholder="Search by name…" style="flex:1">
      <select id="ftype" style="width:auto">
        <option value="">All types</option>
        <option value="video">Videos</option>
        <option value="image">Images</option>
        <option value="audio">Audio</option>
        <option value="application/pdf">PDFs</option>
      </select>
    </div>`;
  view.appendChild(tools);

  const list = el('div', 'card');
  list.id = 'fileList';
  view.appendChild(list);

  const draw = () => {
    const q = ($('#fsearch')?.value || '').toLowerCase();
    const ty = $('#ftype')?.value || '';
    list.innerHTML = '';
    const rows = S.files.filter((f) => {
      const okName = !q || f.name.toLowerCase().includes(q);
      const okType = !ty || (f.mime || '').toLowerCase().startsWith(ty);
      return okName && okType;
    });
    if (!rows.length) {
      list.innerHTML = `<p class="center dim">${S.files.length ? 'No files match your search or type filter.' : 'No files yet. Choose files above to start a Studio upload.'}</p>`;
      return;
    }
    rows.forEach((f) => {
      const [icon, kind] = typeIcon(f.mime, f.name);   // ← must be declared
      const r = el('div', 'file-row');
      r.dataset.id = f.id;
      r.innerHTML = `
        <div class="type-icon ${kind}">${icon}</div>
        <div class="grow"><div class="fname">${esc(f.name)}</div>
          <div class="fmeta">${humanBytes(f.size)} · ${esc(f.mime || 'file')}</div></div>
        <span class="badge-num">${S.files.indexOf(f) + 1}</span>
        <button class="btn icon sm del" title="Remove">✕</button>`;
      r.querySelector('.del').onclick = async () => {
        r.classList.add('removing');
        setTimeout(() => r.remove(), 200);
        S.files = S.files.filter((x) => x.id !== f.id);
        try {
          await api(`/api/jobs/${S.jobId}/items/${f.id}`, { method: 'DELETE' });
        } catch (e) { toast(e.message, 'err'); }
        const pill = $('#fileCountPill');
        if (pill) pill.textContent = `${S.files.length} files`;
        renderBottom();
        toast('Removed — undo not available', '');
      };
      list.appendChild(r);
    });
  };
  draw();
  $('#fsearch').oninput = debounce(draw, 180);
  $('#ftype').onchange = draw;
}

// ── step 2: folder picker ───────────────────────────────────────────────────
async function loadTree(folderId = 'root') {
  const box = $('#tree');
  if (box) box.innerHTML = '<div class="skel-row skeleton"></div><div class="skel-row skeleton"></div>';
  try {
    const data = await api(`/api/drive/children?parent=${encodeURIComponent(folderId)}`);
    S.tree[folderId] = data.folders;
    S.driveAbout = data.about || S.driveAbout;
    return data.folders;
  } catch (e) {
    if (e.message.includes('not connected')) showBlocker({
      kind: 'locked', title: 'Google Drive not connected',
      message: 'Your Google Drive connection is missing. Sign in again to connect your Drive directly on this website.',
    });
    return [];
  }
}

function viewFolder(view) {
  const card = el('div', 'card');
  card.innerHTML = `
    <div class="row" style="margin-bottom:10px">
      <div class="grow"><h2>Destination folder</h2>
        <p class="dim" id="quotaLine">${S.driveAbout && S.driveAbout.free > 0
          ? humanBytes(S.driveAbout.free) + ' free' : ''}</p></div>
      <button class="btn sm" id="favBtn">★ Favourite</button>
    </div>
    <div class="row tight" style="margin-bottom:10px">
      <input type="search" id="dsearch" placeholder="Search folders…" style="flex:1">
      <button class="btn sm" id="newFolderBtn">＋ New folder</button>
    </div>
    <div class="picker">
      <div class="tree" id="tree"><div class="skel-row skeleton"></div><div class="skel-row skeleton"></div></div>
      <div class="col">
        <div class="crumbs-path" id="crumbs">My Drive</div>
        <div class="row tight">
          <button class="btn sm" id="upBtn">↑ Parent</button>
          <button class="btn sm" id="rootBtn">⌂ My Drive</button>
          <button class="btn sm" id="copyId">⧉ Copy folder ID</button>
        </div>
        <div class="hint" id="folderHint">Tap a folder to select it. Tap ▸ to open its children.</div>
      </div>
    </div>
    <div class="divider"></div>
    <h3>Split into sub-folders <span class="dim mini">(optional)</span></h3>
    <p class="hint">Keep one folder for everything, or let Drive create sub-folders
       inside it and route each file automatically.</p>
    <div class="row tight">
      <select id="splitMode" style="max-width:210px">
        <option value="none">One folder for all</option>
        <option value="type">By file type — Videos / Documents …</option>
        <option value="season">By season — from S01E02 names</option>
        <option value="date">By month — 2026-11</option>
        <option value="custom">By my own rule</option>
        <option value="batches">By folder counts (manual)</option>
      </select>
      <input type="text" id="splitRule" placeholder="Rule: match text → folder name"
             style="flex:1;display:none">
      <input type="text" id="splitElse" placeholder="Everything else → Other"
             style="max-width:230px;display:none">
    </div>
    <div id="batchRoutePanel" class="batch-route-panel" style="display:none">
      <p class="hint">Create or reuse child folders under the selected Drive folder. Counts assign consecutive files in the Files list; all counts must add up to the job. Later filename/order edits do not change a file’s folder assignment.</p>
      <div id="batchRouteRows" class="batch-route-rows"></div>
      <button type="button" class="btn sm" id="addBatchRoute">＋ Add folder group</button>
      <p class="hint" id="batchRouteSummary" role="status"></p>
    </div>
    <p class="hint" id="splitPreview"></p>`;
  view.appendChild(card);

  const renderTree = () => {
    const tree = $('#tree');
    tree.innerHTML = '';
    const root = el('div', 'tree-node' + (S.selected === 'root' ? ' selected' : '') + ' open');
    root.dataset.id = 'root';
    root.innerHTML = `<div class="nrow"><span class="caret">▸</span>⌂ <b>My Drive</b></div>
      <div class="tree-children"><div id="rootKids"></div></div>`;
    tree.appendChild(root);
    root.querySelector('.nrow').onclick = (ev) => {
      if (ev.target.classList.contains('caret')) { root.classList.toggle('open'); return; }
      selectFolder('root', 'My Drive');
    };
    const kids = $('#rootKids', root);
    (S.tree.root || []).forEach((f) => kids.appendChild(node(f)));
  };

  const node = (f, depth = 0) => {
    const n = el('div', 'tree-node' + (S.selected === f.id ? ' selected' : ''));
    n.dataset.id = f.id;
    n.dataset.name = f.name;
    n.innerHTML = `<div class="nrow"><span class="caret">▸</span>📁 ${esc(f.name)}</div>
      <div class="tree-children"><div></div></div>`;
    let loaded = false;
    const row = n.querySelector('.nrow');
    const caret = n.querySelector('.caret');
    caret.onclick = async (ev) => {
      ev.stopPropagation();
      const open = n.classList.toggle('open');
      if (open && !loaded) {
        loaded = true;
        const holder = n.querySelector('.tree-children > div');
        holder.innerHTML = '<div class="skel-row skeleton" style="height:26px"></div>';
        const kids = await loadTree(f.id);
        holder.innerHTML = '';
        if (!kids.length) holder.innerHTML = '<div class="hint" style="padding:6px 8px">empty</div>';
        kids.forEach((k) => holder.appendChild(node(k, depth + 1)));
      }
    };
    row.onclick = () => selectFolder(f.id, f.name);
    return n;
  };

  const selectFolder = (id, name) => {
    S.selected = id;
    S.folder = { id, path: name };
    if (S.split && S.split.mode && S.split.mode !== 'none') S.split = currentSplit();
    $$('.tree-node').forEach((n) => n.classList.toggle('selected', n.dataset.id === id));
    const crumb = $('#crumbs');
    if (crumb) crumb.innerHTML = `<b>My Drive</b> <span class="sep">›</span> <b>${esc(name)}</b>`;
    const hint = $('#folderHint');
    if (hint) hint.textContent = `Selected: ${name}`;
    const chip = $('.badge-num.pop');
    if (chip) chip.classList.remove('pop');
  };

  renderTree();

  $('#dsearch').oninput = debounce(async (ev) => {
    const q = ev.target.value.trim();
    const tree = $('#tree');
    if (!q) { renderTree(); return; }
    tree.innerHTML = '<div class="skel-row skeleton"></div>';
    try {
      const res = await api(`/api/drive/search?q=${encodeURIComponent(q)}`);
      tree.innerHTML = '';
      if (!res.folders.length) {
        tree.innerHTML = '<p class="hint" style="padding:10px">No folder matches.</p>';
      }
      res.folders.forEach((f) => tree.appendChild(node(f)));
    } catch (e) { toast(e.message, 'err'); }
  }, 320);

  $('#newFolderBtn').onclick = async () => {
    const name = prompt('New folder name');
    if (!name) return;
    try {
      const res = await api('/api/drive/folders', { method: 'POST',
        body: { name, parent: S.selected } });
      toast(`Created “${res.name}”`, 'ok');
      S.selected = res.id;
      S.folder = { id: res.id, path: res.name };
      S.tree[S.folder.id] = null;
      S.tree.root = await loadTree('root');
      renderTree();
      selectFolder(res.id, res.name);
    } catch (e) { toast(e.message, 'err'); }
  };

  $('#upBtn').onclick = async () => {
    try {
      const res = await api(`/api/drive/parent?child=${encodeURIComponent(S.selected)}`);
      S.selected = res.id; S.folder = { id: res.id, path: res.name };
      S.tree[res.id] = S.tree[res.id] || await loadTree(res.id);
      renderTree();
    } catch (e) { toast(e.message, 'err'); }
  };
  $('#rootBtn').onclick = () => { S.selected = 'root'; S.folder = { id: 'root', path: 'My Drive' }; renderTree(); };
  $('#copyId').onclick = async () => {
    try { await navigator.clipboard.writeText(S.selected); toast('Folder id copied', 'ok'); }
    catch (_) { toast(S.selected); }
  };
  $('#favBtn').onclick = async () => {
    try {
      await api('/api/favourites', { method: 'POST',
        body: { folder_id: S.selected, label: S.folder.path } });
      toast('Added to favourites ★', 'ok');
    } catch (e) { toast(e.message, 'err'); }
  };

  // ── per-group destinations ─────────────────────────────────────────────
  const SPLIT_KEY = 'du.split';
  const saved = S.split || JSON.parse(localStorage.getItem(SPLIT_KEY) || '{"mode":"none"}');
  const drawBatchRows = () => {
    const box = $('#batchRouteRows');
    if (!box) return;
    box.innerHTML = '';
    S.folderGroups.forEach((group, index) => {
      const row = el('div', 'batch-route-row');
      const name = document.createElement('input');
      name.type = 'text'; name.maxLength = 80; name.value = group.name || `Folder ${index + 1}`;
      name.setAttribute('aria-label', `Folder name for group ${index + 1}`);
      name.placeholder = `Folder ${index + 1}`;
      const count = document.createElement('input');
      count.type = 'number'; count.min = '1'; count.max = String(Math.max(1, S.files.length));
      count.step = '1'; count.value = group.count ? String(group.count) : '';
      count.setAttribute('aria-label', `Number of files for group ${index + 1}`);
      count.placeholder = 'Files';
      const remove = el('button', 'btn ghost sm', 'Remove');
      remove.type = 'button'; remove.setAttribute('aria-label', `Remove folder group ${index + 1}`);
      name.oninput = () => { group.name = name.value; drawSplitPreview(); };
      count.oninput = () => { group.count = Math.max(0, Number.parseInt(count.value || '0', 10) || 0); drawSplitPreview(); };
      remove.onclick = () => { S.folderGroups.splice(index, 1); drawBatchRows(); drawSplitPreview(); };
      row.append(name, count, remove); box.append(row);
    });
  };
  const setSplit = () => {
    $('#splitMode').value = saved.mode || 'none';
    const custom = saved.mode === 'custom';
    const batches = saved.mode === 'batches';
    $('#splitRule').style.display = custom ? '' : 'none';
    $('#splitElse').style.display = custom ? '' : 'none';
    $('#batchRoutePanel').style.display = batches ? '' : 'none';
    if (custom) {
      const r0 = (saved.rules || [])[0] || {};
      $('#splitRule').value = r0.match ? `${r0.match} → ${r0.folder}` : '';
      $('#splitElse').value = saved['else'] || '';
    }
    if (batches && !S.folderGroups.length) {
      S.folderGroups = saved.groups?.length ? saved.groups.map((g) => ({ ...g }))
        : Array.from({ length: Math.min(4, Math.max(1, S.files.length)) }, (_, i) => ({ name: `Folder ${i + 1}`, count: 0 }));
    }
    drawBatchRows();
    drawSplitPreview();
  };
  const drawSplitPreview = () => {
    const mode = $('#splitMode').value;
    const box = $('#splitPreview');
    if (mode === 'none') {
      box.textContent = `Everything goes into “${S.folder.path}”.`;
      return;
    }
    if (mode === 'batches') {
      let cursor = 0;
      const total = S.folderGroups.reduce((sum, g) => sum + (Number(g.count) || 0), 0);
      const ranges = S.folderGroups.map((g) => {
        const n = Number(g.count) || 0;
        const first = cursor + 1; cursor += n;
        return n ? `${esc(g.name || 'Untitled folder')}: files ${first}–${cursor} (${n})` : `${esc(g.name || 'Untitled folder')}: set file count`;
      });
      const valid = total === S.files.length && S.files.length > 0 && S.folderGroups.every((g) => g.name.trim() && Number(g.count) > 0);
      $('#batchRouteSummary').textContent = `${total} of ${S.files.length} files assigned${valid ? ' ✓' : ` · assign exactly ${S.files.length}`}`;
      box.innerHTML = ranges.map((line) => `<span>${line}</span>`).join(' · ');
      $('#batchRouteSummary').classList.toggle('bad', !valid);
      return;
    }
    const sample = (S.files || []).map((f) => ({ name: f.name || '', id: f.id }));
    if (!sample.length) { box.textContent = 'Add files to see where they land.'; return; }
    const bucket = (name) => {
      if (mode === 'type') {
        const ext = (name.split('.').pop() || '').toLowerCase();
        const map = { mp4: 'Videos', mkv: 'Videos', mov: 'Videos', webm: 'Videos',
                      jpg: 'Images', png: 'Images', gif: 'Images', webp: 'Images',
                      pdf: 'Documents', docx: 'Documents', txt: 'Documents',
                      mp3: 'Audio', m4a: 'Audio', zip: 'Archives', rar: 'Archives' };
        return map[ext] || 'Other';
      }
      if (mode === 'season') {
        const m = name.match(/S(\d{1,2})\s*E\d{1,3}/i) || name.match(/Season[\s._-]*(\d{1,2})/i);
        return m ? `Season ${Number(m[1])}` : 'Season 1';
      }
      if (mode === 'date') return new Date().toISOString().slice(0, 7);
      const rule = $('#splitRule').value.split('→').map((x) => x.trim());
      if (rule.length === 2 && rule[0] && name.toLowerCase().includes(rule[0].toLowerCase())) {
        return rule[1];
      }
      return $('#splitElse').value.trim() || 'Other';
    };
    const groups = {};
    sample.forEach((f) => { (groups[bucket(f.name)] = groups[bucket(f.name)] || []).push(f.name); });
    box.innerHTML = Object.keys(groups).sort().map((g) =>
      `<b>${esc(g)}/</b> ← ${groups[g].length} file${groups[g].length === 1 ? '' : 's'}`).join(' · ');
  };
  $('#splitMode').onchange = () => { saved.mode = $('#splitMode').value; setSplit(); };
  $('#addBatchRoute').onclick = () => {
    S.folderGroups.push({ name: `Folder ${S.folderGroups.length + 1}`, count: 0 });
    drawBatchRows(); drawSplitPreview();
  };
  ['#splitRule', '#splitElse'].forEach((sel) => {
    $(sel).oninput = debounce(() => {
      const parts = $('#splitRule').value.split('→').map((x) => x.trim());
      saved.rules = parts.length === 2 && parts[0]
        ? [{ match: parts[0], folder: parts[1] }] : [];
      saved['else'] = $('#splitElse').value.trim() || 'Other';
      drawSplitPreview();
    }, 250);
  });
  setSplit();
}

