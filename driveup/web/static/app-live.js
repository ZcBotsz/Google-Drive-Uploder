/* DriveUp Studio — progress, done, confetti, global wiring.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
// ── step 6: live progress (SSE) ────────────────────────────────────────────
const LIVE = { items: {}, total: 0, bytesDone: 0, bytesTotal: 0, logs: [] };

function viewLive(view) {
  const c = el('div', 'card');
  c.innerHTML = `
    <div class="row">
      <div class="grow"><h2>Uploading</h2>
        <p class="dim" id="liveSub">waiting for the first chunk…</p></div>
      <span class="pill info" id="livePill">running</span>
    </div>
    <div class="row" style="gap:18px;margin:8px 0 14px">
      <div class="ring-wrap" style="position:relative">
        <svg class="ring-progress" width="112" height="112" viewBox="0 0 112 112">
          <circle class="bg" cx="56" cy="56" r="48"></circle>
          <circle class="fg" id="ringFg" cx="56" cy="56" r="48"
                  stroke-dasharray="301.6" stroke-dashoffset="301.6"></circle>
        </svg>
        <div class="ring-label" id="ringPct">0%</div>
      </div>
      <div class="grow">
        <div class="track"><div class="fill" id="overallFill"></div></div>
        <div class="row" style="margin-top:10px">
          <span class="pill" id="speedPill">⚡ —</span>
          <span class="pill" id="etaPill">⏳ —</span>
          <span class="pill" id="countPill">0/0</span>
        </div>
        <div class="hint" style="margin-top:8px" id="liveName"></div>
      </div>
    </div>
    <div class="row tight" style="margin-bottom:10px">
      <button class="btn sm" id="pauseBtn">⏸ Pause</button>
      <button class="btn sm" id="resumeBtn">▶️ Resume</button>
      <button class="btn sm" id="retryBtn">↻ Retry failed</button>
    </div>
    <div id="prows"></div>`;
  view.appendChild(c);

  const con = el('div', 'card');
  con.innerHTML = `<h3>Log</h3><div class="console" id="consoleLog"
      role="log" aria-live="polite"></div>`;
  view.appendChild(con);

  $('#pauseBtn').onclick = () => control('pause');
  $('#resumeBtn').onclick = () => control('resume');
  $('#retryBtn').onclick = () => control('retry-failed');
  drawLive();
}

function logLine(text, kind = '') {
  LIVE.logs.push({ text, kind });
  if (LIVE.logs.length > 200) LIVE.logs.shift();
  const box = $('#consoleLog');
  if (!box) return;
  const n = el('div', `ln ${kind}`, `${new Date().toLocaleTimeString()} · ${esc(text)}`);
  box.appendChild(n);
  box.scrollTop = box.scrollHeight;
}

function setRing(pct) {
  const c = 2 * Math.PI * 48;
  const fg = $('#ringFg');
  if (fg) fg.style.strokeDashoffset = String(c * (1 - Math.min(100, pct) / 100));
  const lab = $('#ringPct');
  if (lab) lab.textContent = `${Math.round(pct)}%`;
  const fill = $('#overallFill');
  if (fill) fill.style.width = `${Math.min(100, pct)}%`;
}

function stateIcon(state) {
  return ({ done: '✔', uploading: '↑', downloading: '↓', verifying: '⟳',
            failed: '✕', skipped: '⊘', pending: '·' })[state] || '·';
}

function drawLive() {
  const holder = $('#prows');
  if (!holder) return;
  holder.innerHTML = '';
  const items = Object.values(LIVE.items);
  if (!items.length) return;
  items.slice(0, 200).forEach((it) => {
    const r = el('div', 'prow');
    r.dataset.item = it.item_id;
    r.innerHTML = `<div class="state ${it.stage || 'pending'}">${stateIcon(it.stage)}</div>
      <div class="pname">${esc(it.name)}</div>
      <div class="track thin"><div class="fill" style="width:${it.drive_pct || 0}%"></div></div>
      <div class="right dim">${it.total ? humanBytes(it.total) : ''}</div>`;
    holder.appendChild(r);
  });
}

async function control(action) {
  try {
    await api(`/api/jobs/${S.jobId}/${action}`, { method: 'POST' });
    if (action === 'pause') logLine('paused by user', 'warn');
    if (action === 'resume') logLine('resumed by user', 'ok');
    if (action === 'retry-failed') logLine('retrying failed items', 'ok');
    toast(action.replace('-', ' '), 'ok');
  } catch (e) { toast(e.message, 'err'); }
}

function attachSSE() {
  if (S.sse) { try { S.sse.close(); } catch (_) {} }
  if (window.EventSource) {
    const es = new EventSource(`/api/jobs/${S.jobId}/events`);
    S.sse = es;
    es.onmessage = (ev) => {
      let d;
      try { d = JSON.parse(ev.data); } catch (_) { return; }
      if (d.stage === 'job-finished') { es.close(); refreshJobThenDone(); return; }
      LIVE.items[d.item_id] = { ...LIVE.items[d.item_id], ...d };
      LIVE.total = d.count || LIVE.total;
      applyProgress(d);
    };
    es.onerror = () => logLine('event stream reconnecting…', 'warn');
  }
  pollJob();
}

let pollTimer = null;
async function pollJob() {
  clearTimeout(pollTimer);
  try {
    const job = await api(`/api/jobs/${S.jobId}`);
    job.items.forEach((it) => {
      LIVE.items[it.id] = {
        item_id: it.id, name: it.final_name || it.name, stage: it.state,
        total: it.size, drive_pct: it.size ? (it.bytes_done / it.size) * 100 : 0,
      };
    });
    S.files = job.items.map((it) => ({ id: it.id, name: it.name, size: it.size,
      mime: it.mime, msg_id: it.msg_id }));
    if (job.totals) {
      LIVE.bytesTotal = job.totals.bytes;
      LIVE.bytesDone = job.totals.bytes_done;
    }
    drawLive();
    applyProgress(job);
    if (job.status === 'done') { refreshJobThenDone(); return; }
    if (['failed', 'cancelled'].includes(job.status)) {
      logLine(`job ${job.status}`, 'err');
      return;
    }
  } catch (_) { /* keep polling */ }
  pollTimer = setTimeout(pollJob, 2500);
}

function applyProgress(d) {
  const pct = d.overall_pct ?? (LIVE.bytesTotal
    ? (LIVE.bytesDone / LIVE.bytesTotal) * 100 : 0);
  setRing(pct);
  const sp = $('#speedPill');
  if (sp) sp.textContent = d.speed ? `⚡ ${humanBytes(d.speed)}/s` : '⚡ —';
  if (d.speed) S.measuredSpeed = d.speed;
  const et = $('#etaPill');
  if (et) et.textContent = d.eta ? `⏳ ${humanSecs(d.eta)}` : '⏳ —';
  const cp = $('#countPill');
  if (cp) cp.textContent = `${d.index || 0}/${d.count || LIVE.total || 0}`;
  const nm = $('#liveName');
  if (nm && d.name) nm.textContent = d.name;
  const sub = $('#liveSub');
  if (sub && d.stage) sub.textContent = `${d.stage} · ${d.name || ''}`;
  const pill = $('#livePill');
  if (pill) { pill.textContent = d.paused ? 'paused' : 'running';
    pill.className = 'pill ' + (d.paused ? 'warn' : 'info'); }
  if (d.drive_pct && d.item_id) {
    const row = $(`.prow[data-item="${d.item_id}"] .fill`);
    if (row) row.style.width = `${d.drive_pct}%`;
  }
}

async function refreshJobThenDone() {
  try {
    const job = await api(`/api/jobs/${S.jobId}`);
    S.finalJob = job;
    if (job.status === 'done') { go('done'); return; }
  } catch (_) {}
  pollJob();
}

// ── step 7: done (+ confetti) ──────────────────────────────────────────────
function viewDone(view) {
  const job = S.finalJob || {};
  const items = job.items || [];
  const done = items.filter((i) => i.state === 'done');
  const failed = items.filter((i) => i.state === 'failed');
  const skipped = items.filter((i) => i.state === 'skipped');
  const c = el('div', 'card');
  c.innerHTML = `
    <div class="done-hero">
      <div class="big">🏁</div>
      <h2>Upload finished</h2>
      <p class="dim">${done.length} file(s) are in your Drive.</p>
    </div>
    <div class="done-stats">
      <div class="stat ok"><div class="n">${done.length}</div><div class="l">done</div></div>
      <div class="stat warn"><div class="n">${skipped.length}</div><div class="l">skipped</div></div>
      <div class="stat bad"><div class="n">${failed.length}</div><div class="l">failed</div></div>
    </div>
    <div class="row tight">
      <button class="btn sm" id="openFolder">📂 Open folder</button>
      <button class="btn sm" id="csvBtn">⬇ CSV</button>
      <button class="btn sm" id="shareAll">🔗 Share all links</button>
    </div>`;
  view.appendChild(c);

  const list = el('div', 'card');
  list.innerHTML = `<h3>Links</h3><div id="links"></div>`;
  view.appendChild(list);
  const links = $('#links');
  done.forEach((it) => {
    const r = el('div', 'link-row');
    r.innerHTML = `<span>📄</span><span class="lname">${esc(it.final_name || it.name)}</span>
      <button class="btn icon sm copy" title="Copy link">⧉</button>
      <a class="btn sm" href="${esc(it.link || '#')}" target="_blank" rel="noopener">open</a>`;
    r.querySelector('.copy').onclick = async () => {
      try { await navigator.clipboard.writeText(it.link || ''); toast('Link copied', 'ok'); }
      catch (_) { toast(it.link || 'no link'); }
    };
    links.appendChild(r);
  });
  if (!done.length) links.innerHTML = '<p class="dim">No links — check the log in the bot.</p>';

  $('#openFolder').onclick = () => {
    const id = job.destination?.folder_id;
    if (id && id !== 'root') window.open(`https://drive.google.com/drive/folders/${id}`, '_blank');
    else window.open('https://drive.google.com/drive/my-drive', '_blank');
  };
  $('#csvBtn').onclick = () => {
    const rows = [['order', 'name', 'size', 'drive_id', 'link']];
    items.forEach((it, i) => rows.push([i + 1, it.final_name || it.name, it.size,
      it.drive?.file_id || '', it.link || '']));
    const csv = rows.map((r) => r.map((v) => `"${String(v).replace(/"/g, '""')}"`).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = el('a');
    a.href = URL.createObjectURL(blob);
    a.download = `driveup-${S.jobId}.csv`;
    a.click();
    toast('CSV downloaded', 'ok');
  };
  $('#shareAll').onclick = async () => {
    const text = done.map((it) => `${it.final_name}\n${it.link}`).join('\n\n');
    try { await navigator.clipboard.writeText(text); toast('All links copied', 'ok'); }
    catch (_) { toast('clipboard blocked'); }
  };
  fireConfetti();
}

function fireConfetti(n = 90) {
  if (!motionOK()) return;
  const box = $('#confetti');
  if (!box) return;
  box.innerHTML = '';
  const colors = ['#22d3ee', '#22d3a5', '#7c9cff', '#f5b544', '#ff6b6b'];
  for (let i = 0; i < n; i++) {
    const p = el('i', 'cf');
    p.style.left = `${Math.random() * 100}%`;
    p.style.background = colors[i % colors.length];
    p.style.setProperty('--dx', `${(Math.random() - 0.5) * 260}px`);
    p.style.animationDuration = `${1.6 + Math.random() * 1.8}s`;
    p.style.animationDelay = `${Math.random() * 0.5}s`;
    box.appendChild(p);
  }
  setTimeout(() => { box.innerHTML = ''; }, 4200);
}

// ── global listeners ───────────────────────────────────────────────────────
function wireGlobal() {
  window.addEventListener('online', () => setOffline(false));
  window.addEventListener('offline', () => setOffline(true));
  // ripple on every button, delegated
  document.addEventListener('pointerdown', (e) => {
    const b = e.target.closest('.btn');
    if (!b || !motionOK()) return;
    const rect = b.getBoundingClientRect();
    const d = Math.max(rect.width, rect.height);
    const r = el('span', 'ripple');
    r.style.width = r.style.height = `${d}px`;
    r.style.left = `${e.clientX - rect.left - d / 2}px`;
    r.style.top = `${e.clientY - rect.top - d / 2}px`;
    b.appendChild(r);
    setTimeout(() => r.remove(), 640);
  });
  // keyboard shortcuts (desktop): ←/→ move through the wizard
  document.addEventListener('keydown', (e) => {
    if (e.target.matches('input, textarea, select')) return;
    const i = S.steps.indexOf(S.step);
    if (e.key === 'ArrowRight' && i < S.steps.length - 1 && S.step !== 'live') go(S.steps[i + 1]);
    if (e.key === 'ArrowLeft' && i > 0 && S.step !== 'live') go(S.steps[i - 1], true);
  });
}

