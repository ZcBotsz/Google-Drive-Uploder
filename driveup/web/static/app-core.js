/* DriveUp Studio — boot, blockers, wizard chrome, steps 1–2.
   Classic script (no bundler); all app-*.js files share one global scope. */
// ── loading screen (real boot steps, min 600 ms) ────────────────────────────
const SPLASH_STEPS = [
  [0.15, 'Connecting to Telegram…'],
  [0.40, 'Verifying your session…'],
  [0.62, 'Linking Google Drive…'],
  [0.84, 'Loading your files…'],
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
    if (S.me.job) hydrate(S.me.job);
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
  S.files = (job.items || []).map((it) => ({
    id: it.id, name: it.name, size: it.size, mime: it.mime,
    msg_id: it.msg_id, caption: it.caption || '',
  }));
  if (job.destination?.folder_id) {
    S.folder = { id: job.destination.folder_id, path: job.destination.path || 'My Drive' };
    S.split = (job.destination && job.destination.split) || null;
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
    n.onclick = () => go('naming');
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
    const studio = el('button', 'btn', '🌐 Studio');
    studio.onclick = () => { toast('You are in the Studio'); };
    bar.appendChild(studio);
  } else if (S.step === 'done') {
    const more = el('button', 'btn primary', '📤 Upload more');
    more.onclick = () => toast('Send more files to the bot — a new job card appears');
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

function viewFiles(view) {
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
      list.innerHTML = `<p class="center dim">No files match. Send more files to the bot.</p>`;
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
      </select>
      <input type="text" id="splitRule" placeholder="Rule: match text → folder name"
             style="flex:1;display:none">
      <input type="text" id="splitElse" placeholder="Everything else → Other"
             style="max-width:230px;display:none">
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
  const setSplit = () => {
    $('#splitMode').value = saved.mode || 'none';
    const custom = saved.mode === 'custom';
    $('#splitRule').style.display = custom ? '' : 'none';
    $('#splitElse').style.display = custom ? '' : 'none';
    if (custom) {
      const r0 = (saved.rules || [])[0] || {};
      $('#splitRule').value = r0.match ? `${r0.match} → ${r0.folder}` : '';
      $('#splitElse').value = saved['else'] || '';
    }
    drawSplitPreview();
  };
  const drawSplitPreview = () => {
    const mode = $('#splitMode').value;
    const box = $('#splitPreview');
    if (mode === 'none') {
      box.textContent = `Everything goes into “${S.folder.path}”.`;
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

