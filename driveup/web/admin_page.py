"""The admin panel page (server-rendered shell + vanilla JS).

Kept in one file on purpose: Django-style templates would need another
dependency, and the panel is a handful of tables that all render from
`/admin/api/*`. Every colour comes from the same CSS tokens as the Studio.
"""
from __future__ import annotations

from .admin_css import ADMIN_CSS
from .admin_plans_js import PLANS_JS


def render_admin(brand: str = "DriveUp") -> str:
    return """<!DOCTYPE html>
<html lang="en" data-bar="glow">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#081014">
<title>__BRAND__ · Admin</title>
<link rel="stylesheet" href="/static/app.css">
__ADMIN_CSS__
</head>
<body>
<div class="app">
  <header class="topbar">
    <div class="brand"><span class="logo">🛠</span><span>__BRAND__ · ADMIN</span></div>
    <div class="spacer"></div>
    <span class="who" id="who">…</span>
    <button class="btn sm" id="lockBtn" title="Lock the panel">🔒 Lock</button>
  </header>

  <div class="wrap admin-shell">
    <aside class="side card tight" id="side">
      <a href="#dashboard" data-pane="dashboard" class="active">📊 Dashboard</a>
      <a href="#users" data-pane="users">👥 Users</a>
      <a href="#jobs" data-pane="jobs">📦 Jobs</a>
      <a href="#locks" data-pane="locks">🔒 Locks</a>
      <a href="#plans" data-pane="plans">💎 Plans</a>
      <a href="#broadcast" data-pane="broadcast">📣 Broadcast</a>
      <a href="#settings" data-pane="settings">⚙️ Settings</a>
      <a href="#audit" data-pane="audit">📜 Audit</a>
      <a href="#danger" data-pane="danger">☢️ Danger</a>
    </aside>

    <main>
      <section class="pane active" id="pane-dashboard">
        <div class="tiles" id="tiles"></div>
        <div class="card" style="margin-top:14px">
          <h3>Uploads — last 14 days</h3>
          <div class="spark" id="sparkUploads"></div>
          <div class="divider"></div>
          <h3>New users — last 14 days</h3>
          <div class="spark" id="sparkUsers"></div>
        </div>
        <div class="card">
          <h3>System</h3>
          <div id="system" class="statrow"></div>
          <div class="divider"></div>
          <h3>Recent errors</h3>
          <div id="errList" class="mini dim">none</div>
        </div>
      </section>

      <section class="pane" id="pane-users">
        <div class="card">
          <div class="row">
            <input type="search" id="uq" placeholder="Search id / name / @username" style="flex:1">
            <button class="btn sm" id="ucsv">⬇ CSV</button>
          </div>
          <div class="tblwrap" style="margin-top:12px"><table class="atable" id="usersTbl">
            <thead><tr><th>ID</th><th>Name</th><th>Plan</th><th>Drive</th><th>Status</th><th></th></tr></thead>
            <tbody></tbody></table></div>
        </div>
      </section>

      <section class="pane" id="pane-jobs">
        <div class="card">
          <div class="row">
            <select id="jstatus" style="width:auto">
              <option value="">All</option><option>running</option><option>queued</option>
              <option>paused</option><option>done</option><option>failed</option><option>cancelled</option>
            </select>
            <button class="btn sm" id="pauseAll">⏸ Pause all</button>
            <button class="btn sm" id="resumeAll">▶ Resume all</button>
          </div>
          <div class="tblwrap" style="margin-top:12px"><table class="atable" id="jobsTbl">
            <thead><tr><th>Job</th><th>User</th><th>Status</th><th>Files</th><th>Created</th><th></th></tr></thead>
            <tbody></tbody></table></div>
        </div>
      </section>

      <section class="pane" id="pane-locks">
        <div class="card">
          <div class="row" style="margin-bottom:12px">
            <input type="number" id="lockId" placeholder="user id" style="max-width:140px">
            <input type="number" id="lockMin" placeholder="minutes" value="60" style="max-width:120px">
            <input type="text" id="lockReason" placeholder="reason" style="flex:1">
            <button class="btn sm danger" id="lockGo">🔒 Lock</button>
          </div>
          <div class="row tight" style="margin-bottom:12px">
            <button class="btn sm" data-lockpreset="30|Spam: too many requests">spam 30 min</button>
            <button class="btn sm" data-lockpreset="10080|Chargeback / fraud">chargeback 7 days</button>
            <button class="btn sm" data-lockpreset="1440|Abuse of the service">abuse 24 h</button>
          </div>
          <div class="tblwrap"><table class="atable" id="locksTbl">
            <thead><tr><th>User</th><th>Reason</th><th>Until</th><th>By</th><th></th></tr></thead>
            <tbody></tbody></table></div>
        </div>
      </section>

      <section class="pane" id="pane-plans">
        <div class="card">
          <h3>Plans</h3>
          <div class="tblwrap"><table class="atable" id="plansTbl">
            <thead><tr><th>ID</th><th>Name</th><th>Price</th><th>Days</th><th>Max file</th>
            <th>Daily GB</th><th>Files/job</th></tr></thead><tbody></tbody></table></div>
          <div class="divider"></div>
          <h3>Edit a plan</h3>
          <div class="mini dim" style="margin-bottom:8px">Tap any row above to load it,
            change the numbers, then Save. Per-file size is capped by Telegram — see the
            badge in the table.</div>
          <div class="row" style="margin-bottom:8px">
            <select id="eId" style="width:auto"></select>
            <button class="btn sm" id="eLoad">Load</button>
          </div>
          <div class="grid two">
            <label>Name <input type="text" id="eName" placeholder="Pro"></label>
            <label>Price text <input type="text" id="ePrice" placeholder="₹99 / month"></label>
            <label>Duration (days) <input type="number" id="eDays" min="0" step="1"></label>
            <label>Max file (MB) <input type="number" id="eMaxFile" min="1" step="1"></label>
            <label>Daily GB <input type="number" id="eDailyGb" min="1" step="1"></label>
            <label>Concurrent jobs <input type="number" id="eConc" min="1" step="1"></label>
            <label>Files per job <input type="number" id="eFiles" min="1" step="1"></label>
          </div>
          <div class="row" style="margin-top:10px">
            <button class="btn primary" id="eSave">Save plan</button>
            <span class="mini dim" id="eHint"></span>
          </div>
          <div class="divider"></div>
          <h3>Grant a plan</h3>
          <div class="row">
            <input type="number" id="gUserId" placeholder="user id" style="max-width:140px">
            <select id="gPlan" style="width:auto"></select>
            <input type="number" id="gDays" value="30" style="max-width:110px">
            <button class="btn sm primary" id="grantBtn">Grant</button>
            <button class="btn sm danger" id="revokeBtn">Revoke</button>
          </div>
          <div class="divider"></div>
          <h3>Expiring in 7 days</h3>
          <div id="expiring" class="mini dim">none</div>
        </div>
      </section>

      <section class="pane" id="pane-broadcast">
        <div class="card">
          <h3>Broadcast</h3>
          <p class="hint">Preview on the right, real HTML in the message.</p>
          <textarea id="btext" rows="7" placeholder="<b>Hello</b> — HTML allowed"></textarea>
          <div class="row" style="margin-top:10px">
            <select id="baud" style="width:auto">
              <option value="all">All users</option><option value="active">Active 7 days</option>
              <option value="plan">Paid plans</option>
            </select>
            <button class="btn sm" id="bTest">Send to me</button>
            <button class="btn sm primary" id="bGo">Send</button>
          </div>
          <div class="divider"></div>
          <h3>Preview</h3>
          <div class="card flat" id="bprev" style="min-height:60px"></div>
        </div>
      </section>

      <section class="pane" id="pane-settings">
        <div class="card">
          <h3>Global settings</h3>
          <div class="grid two" id="settingsGrid"></div>
          <div class="row" style="margin-top:12px">
            <button class="btn primary sm" id="saveSettings">💾 Save</button>
            <button class="btn sm" id="emojiTest">✨ Test animated emoji</button>
          </div>
          <div id="emojiOut" class="mini" style="margin-top:10px"></div>
          <p class="mini dim">Animated emoji render as moving glyphs for everyone with the
             <b>free</b> ids (7), and for Telegram Premium viewers with the rest (29).
             Everyone else always sees the plain character — nothing can break.</p>
          <div class="divider"></div>
          <h3>Runtime (read-only, from env)</h3>
          <div id="cfgList" class="mini dim"></div>
        </div>
      </section>

      <section class="pane" id="pane-audit">
        <div class="card">
          <div class="row"><input type="search" id="aq" placeholder="search audit" style="flex:1">
            <button class="btn sm" id="aGo">Search</button></div>
          <div class="tblwrap" style="margin-top:12px"><table class="atable" id="auditTbl">
            <thead><tr><th>When</th><th>Actor</th><th>Action</th><th>Target</th><th>Detail</th></tr></thead>
            <tbody></tbody></table></div>
        </div>
      </section>

      <section class="pane" id="pane-danger">
        <div class="card">
          <h3>☢️ Danger zone (owner only)</h3>
          <p class="hint">Every action is audited. Destructive ones ask for the database name.</p>
          <div class="row">
            <button class="btn sm" data-danger="backup">🗄 Export backup summary</button>
            <button class="btn sm" data-danger="sweep">🧹 Run TTL sweep</button>
            <button class="btn sm" data-danger="clear_errors">🧽 Clear error log</button>
            <button class="btn sm" data-danger="restart_workers">♻️ Restart workers</button>
            <button class="btn sm danger" data-danger="purge_history">🗑 Purge jobs &gt; 90 days</button>
          </div>
        </div>
      </section>
    </main>
  </div>
</div>

<div class="drawer" id="drawer"><button class="btn icon sm close" id="drawerClose">✕</button>
  <div id="drawerBody"></div></div>
<div id="toasts"></div>

<script>
'use strict';
let CSRF = '';
const $ = (s, r=document) => r.querySelector(s);
const $$ = (s, r=document) => Array.from(r.querySelectorAll(s));
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function toast(msg, kind='') {
  const n = document.createElement('div');
  n.className = 'toast ' + kind; n.textContent = msg;
  $('#toasts').appendChild(n);
  setTimeout(() => { n.classList.add('out'); setTimeout(()=>n.remove(), 240); }, 3000);
}
async function api(path, opts={}) {
  const init = { credentials:'same-origin', headers:{'Content-Type':'application/json'}, ...opts };
  if (CSRF) init.headers['X-CSRF'] = CSRF;
  if (init.body && typeof init.body !== 'string') init.body = JSON.stringify(init.body);
  const r = await fetch(path, init);
  let d = {}; try { d = await r.json(); } catch(_) {}
  if (r.status === 401) {
    if (window.Telegram?.WebApp?.initData && !sessionStorage.getItem('du_tried')) {
      sessionStorage.setItem('du_tried','1');
      const a = await fetch('/auth/telegram', { method:'POST', credentials:'same-origin',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ initData: window.Telegram.WebApp.initData }) });
      if (a.ok) return api(path, opts);
    }
    document.body.innerHTML =
    '<div class="blocker"><div class="inner"><div class="icon">🔐</div><h1>Panel locked</h1>' +
    '<p>Send <b>/admin</b> to the bot and tap the button — that link carries a fresh session.</p>' +
    '<p class="hint">Opening /admin directly can never work: the session cookie only comes from the bot.</p></div></div>';
    throw new Error('locked'); }
  if (!r.ok) throw new Error(d.message || d.detail || ('HTTP ' + r.status));
  return d;
}
const humanBytes = (n) => { n = Number(n||0); if (n<1024) return n+' B';
  const u=['KB','MB','GB','TB']; let i=-1; do { n/=1024; i++; } while (n>=1024 && i<u.length-1);
  return n.toFixed(n<10?2:1)+' '+u[i]; };
const when = (ts) => ts ? new Date(ts*1000).toLocaleString() : '—';

// ── boot ────────────────────────────────────────────────────────────────
async function boot() {
  try {
    const c = await api('/admin/api/csrf'); CSRF = c.token;
    const me = await api('/admin/api/whoami');
    const u = me.user || {};
    $('#who').textContent = (u.name || 'admin') + ' · ' + (u.role || '');
    if (u.role === 'support') document.body.dataset.role = 'support';
    await loadDashboard(); await loadUsers(); await loadJobs(); await loadLocks();
    await loadPlans(); await loadSettings(); await loadAudit();
    document.body.dataset.ready = '1';
  } catch (e) { if (e.message !== 'locked') toast(e.message, 'err'); }
}

// ── nav ────────────────────────────────────────────────────────────────
$$('#side a').forEach(a => {
  a.onclick = (e) => {
    e.preventDefault();
    $$('#side a').forEach(x => x.classList.remove('active'));
    a.classList.add('active');
    $$('.pane').forEach(p => p.classList.remove('active'));
    $('#pane-' + a.dataset.pane).classList.add('active');
    location.hash = a.dataset.pane;
  };
});
if (location.hash) {
  const a = $('#side a[data-pane="' + location.hash.slice(1) + '"]');
  if (a) a.click();
}

// ── dashboard ──────────────────────────────────────────────────────────
async function loadDashboard() {
  const d = await api('/admin/api/stats');
  const s = d.stats;
  const tiles = [
    ['Users', s.users], ['Active 24 h', s.active_24h], ['Jobs running', s.jobs_running],
    ['Queued', s.jobs_queued], ['Failed', s.jobs_failed], ['Uploads today', s.uploads_today],
    ['Bytes today', humanBytes(s.bytes_today)], ['Locked', s.locked],
    ['Plans active', s.plans_active], ['Errors 24 h', s.errors_today],
  ];
  $('#tiles').innerHTML = tiles.map(([l, n]) =>
    '<div class="tile"><div class="n">' + esc(n) + '</div><div class="l">' + l + '</div></div>').join('');
  const drawSpark = (host, key, color) => {
    const vals = d.days.map(x => Number(x[key] || 0));
    const max = Math.max(1, ...vals);
    host.innerHTML = vals.map(v =>
      '<i style="height:' + Math.max(3, v / max * 100) + '%;' +
      (color ? 'background:' + color : '') + '"></i>').join('');
  };
  drawSpark($('#sparkUploads'), 'uploads');
  drawSpark($('#sparkUsers'), 'new_users', 'linear-gradient(180deg,#22d3a5,rgba(34,211,165,.15))');
  const sys = d.system || {};
  $('#system').innerHTML = [
    ['CPU', (sys.cpu||0).toFixed(0) + '%', ''],
    ['RAM', (sys.ram||0).toFixed(0) + '%', ''],
    ['Disk', (sys.disk||0).toFixed(0) + '%', ''],
    ['Uptime', Math.round((d.uptime||0)/60) + ' min', ''],
    ['Workers', d.workers.global + ' global / ' + d.workers.user + ' per user', ''],
  ].map(([l, n]) => '<div class="tile"><div class="n">' + esc(n) + '</div><div class="l">' + l + '</div></div>').join('');
  $('#errList').innerHTML = (d.errors || []).length
    ? d.errors.map(e => '<div class="kv"><span>' + esc(e.where_) + '</span><span>' +
        esc((e.detail||'').slice(0, 90)) + '</span></div>').join('')
    : 'none';
}

// ── users ──────────────────────────────────────────────────────────────
let USERS = [];
async function loadUsers(q='') {
  const d = await api('/admin/api/users?q=' + encodeURIComponent(q));
  USERS = d.users;
  $('#usersTbl tbody').innerHTML = d.users.map(u =>
    '<tr><td class="mono" data-l="ID">' + u.user_id + '</td>' +
    '<td data-l="Name">' + esc(u.name) +
    (u.username ? ' <span class="dim mini">@' + esc(u.username) + '</span>' : '') +
    '</td><td data-l="Plan">' + esc(u.plan_name || u.plan) + '</td>' +
    '<td class="mini" data-l="Drive">' + esc(u.drive || '—') +
    '</td><td data-l="Status">' + (u.locked ? '<span class="pill bad">locked</span>'
      : '<span class="pill ok">active</span>') +
    '</td><td class="act"><button class="btn sm" data-user="' + u.user_id + '">open</button></td></tr>').join('')
    || '<tr><td colspan=6 class="dim">no users yet</td></tr>';
  $$('#usersTbl [data-user]').forEach(b => b.onclick = () => openUser(b.dataset.user));
}
$('#uq').oninput = (() => { let t; return (e) => { clearTimeout(t);
  t = setTimeout(() => loadUsers(e.target.value), 260); }; })();
$('#ucsv').onclick = () => { location.href = '/admin/api/export/users.csv'; };

async function openUser(id) {
  const d = await api('/admin/api/users/' + id);
  const u = d.user;
  const drawer = $('#drawer');
  $('#drawerBody').innerHTML =
    '<h2>' + esc(u.name || 'User') + '</h2>' +
    '<p class="dim mono">id ' + u.user_id + (u.username ? ' · @' + esc(u.username) : '') + '</p>' +
    '<div class="kv"><span>Plan</span><span>' + esc(d.plan.name || d.plan.id) + '</span></div>' +
    '<div class="kv"><span>Drive</span><span>' + esc(d.drive.email || 'not connected') +
      (d.drive.needs_reconnect ? ' ⚠️' : '') + '</span></div>' +
    '<div class="kv"><span>Uploaded today</span><span>' + humanBytes(d.quota.bytes) +
      ' / ' + d.quota.files + ' files</span></div>' +
    '<div class="kv"><span>All time</span><span>' + d.stats.files + ' files · ' +
      humanBytes(d.stats.bytes) + '</span></div>' +
    '<div class="kv"><span>Locked</span><span>' + (d.lock ? esc(d.lock.reason) : 'no') + '</span></div>' +
    '<div class="divider"></div>' +
    '<div class="row tight">' +
      '<button class="btn sm danger" data-act="lock">🔒 Lock</button>' +
      '<button class="btn sm" data-act="unlock">🔓 Unlock</button>' +
      '<button class="btn sm" data-act="plan">💎 Grant plan</button>' +
      '<button class="btn sm" data-act="quota">↺ Reset quota</button>' +
      '<button class="btn sm" data-act="disconnect">🔌 Disconnect Drive</button>' +
      '<button class="btn sm" data-act="message">📩 Message</button>' +
      '<button class="btn sm danger" data-act="delete">🗑 Delete data</button>' +
    '</div>' +
    '<div class="divider"></div><h3>Last jobs</h3>' +
    (d.jobs.length ? d.jobs.map(j => '<div class="kv"><span class="mono">' + j.id.slice(0,8) +
      '</span><span>' + esc(j.status) + ' · ' + (j.totals.files||0) + ' files</span></div>').join('')
      : '<p class="dim">none</p>');
  drawer.classList.add('open');
  $$('#drawerBody [data-act]').forEach(b => b.onclick = async () => {
    const act = b.dataset.act;
    try {
      if (act === 'lock') {
        const mins = Number(prompt('Minutes?', '60') || 60);
        const reason = prompt('Reason?', 'Admin decision') || 'Admin decision';
        const r = await api('/admin/api/users/' + id + '/lock',
          { method:'POST', body:{ minutes: mins, reason } });
        toast('Locked · ' + r.jobs_cancelled + ' job(s) cancelled · ' +
              r.sessions_killed + ' session(s) killed', 'ok');
      } else if (act === 'unlock') {
        await api('/admin/api/users/' + id + '/unlock', { method:'POST' });
        toast('Unlocked', 'ok');
      } else if (act === 'plan') {
        const days = Number(prompt('Days?', '30') || 30);
        const plan = prompt('Plan id?', 'pro') || 'pro';
        await api('/admin/api/users/' + id + '/plan',
          { method:'POST', body:{ plan_id: plan, days } });
        toast('Plan granted', 'ok');
      } else if (act === 'quota') {
        await api('/admin/api/users/' + id + '/quota', { method:'POST' });
        toast('Quota reset', 'ok');
      } else if (act === 'disconnect') {
        await api('/admin/api/users/' + id + '/disconnect', { method:'POST' });
        toast('Drive disconnected', 'ok');
      } else if (act === 'message') {
        const text = prompt('Message to send?');
        if (text) { await api('/admin/api/users/' + id + '/message',
          { method:'POST', body:{ text } }); toast('Sent', 'ok'); }
      } else if (act === 'delete') {
        if (prompt('Type DELETE to erase this user') === 'DELETE') {
          await api('/admin/api/users/' + id, { method:'DELETE' });
          toast('Deleted', 'ok'); drawer.classList.remove('open');
        }
      }
      await loadUsers(); await loadDashboard(); await loadLocks(); await loadAudit();
    } catch (e) { toast(e.message, 'err'); }
  });
}
$('#drawerClose').onclick = () => $('#drawer').classList.remove('open');

// ── jobs ───────────────────────────────────────────────────────────────
async function loadJobs() {
  const st = $('#jstatus').value;
  const d = await api('/admin/api/jobs' + (st ? '?status=' + st : ''));
  $('#jobsTbl tbody').innerHTML = d.jobs.map(j =>
    '<tr><td class="mono mini" data-l="Job">' + j.id.slice(0,8) + '</td>' +
    '<td data-l="User">' + esc(j.user || j.user_id) +
    '</td><td data-l="Status"><span class="pill ' + ({running:'info',done:'ok',failed:'bad',paused:'warn'}[j.status]||'') +
    '">' + esc(j.status) + '</span></td><td data-l="Files">' + (j.totals.files||0) + '</td>' +
    '<td class="mini" data-l="Created">' +
    when(j.created_at) + '</td><td class="act">' +
    ['pause','resume','cancel','retry'].map(a =>
      '<button class="btn sm" data-job="' + j.id + '" data-a="' + a + '">' + a + '</button>').join(' ') +
    '</td></tr>').join('') || '<tr><td class="dim">no jobs</td></tr>';
  $$('#jobsTbl [data-job]').forEach(b => b.onclick = async () => {
    try { await api('/admin/api/jobs/' + b.dataset.job + '/' + b.dataset.a, { method:'POST' });
      toast(b.dataset.a + ' ok', 'ok'); loadJobs(); } catch (e) { toast(e.message, 'err'); }
  });
}
$('#jstatus').onchange = loadJobs;
$('#pauseAll').onclick = async () => { await api('/admin/api/queue/pause_all', {method:'POST'});
  toast('Queue paused', 'ok'); };
$('#resumeAll').onclick = async () => { await api('/admin/api/queue/resume_all', {method:'POST'});
  toast('Queue resumed', 'ok'); };

// ── locks ──────────────────────────────────────────────────────────────
async function loadLocks() {
  const d = await api('/admin/api/locks');
  $('#locksTbl tbody').innerHTML = d.locks.map(l =>
    '<tr><td class="mono" data-l="User">' + l.user_id + ' <span class="dim mini">' + esc(l.name||'') +
    '</span></td><td data-l="Reason">' + esc(l.reason) + '</td>' +
    '<td class="mini" data-l="Until">' + when(l.expires_at) +
    '</td><td class="mono mini" data-l="By">' + l.by_id +
    '</td><td class="act"><button class="btn sm" data-unlock="' +
    l.user_id + '">unlock</button></td></tr>').join('')
    || '<tr><td class="dim">no active locks</td></tr>';
  $$('#locksTbl [data-unlock]').forEach(b => b.onclick = async () => {
    await api('/admin/api/users/' + b.dataset.unlock + '/unlock', { method:'POST' });
    toast('Unlocked', 'ok'); loadLocks();
  });
}
$('#lockGo').onclick = async () => {
  try {
    await api('/admin/api/locks', { method:'POST', body:{
      user_id: Number($('#lockId').value), minutes: Number($('#lockMin').value||60),
      reason: $('#lockReason').value || 'Admin decision' } });
    toast('Locked', 'ok'); loadLocks(); loadUsers();
  } catch (e) { toast(e.message, 'err'); }
};
$$('[data-lockpreset]').forEach(b => b.onclick = () => {
  const [m, r] = b.dataset.lockpreset.split('|');
  $('#lockMin').value = m; $('#lockReason').value = r;
  toast('Preset applied — enter a user id');
});

__PLANS_JS__
// ── broadcast ──────────────────────────────────────────────────────────
$('#btext').oninput = () => { $('#bprev').innerHTML = $('#btext').value || '<span class="dim">…</span>'; };
$('#bTest').onclick = async () => {
  try { const r = await api('/admin/api/broadcast',
    { method:'POST', body:{ text: $('#btext').value, test: true } });
    toast('Sent to ' + r.sent + ' (you)', 'ok'); } catch (e) { toast(e.message, 'err'); }
};
$('#bGo').onclick = async () => {
  if (!confirm('Send to ' + $('#baud').value + ' audience?')) return;
  try { const r = await api('/admin/api/broadcast',
    { method:'POST', body:{ text: $('#btext').value, audience: $('#baud').value } });
    toast('sent ' + r.sent + ' · failed ' + r.failed, 'ok'); } catch (e) { toast(e.message, 'err'); }
};

// ── settings ───────────────────────────────────────────────────────────
const SETTING_FIELDS = [
  ['brand_name', 'Brand name', 'text'],
  ['support_contact', 'Support contact', 'text'],
  ['plan_price_text', 'Plan price text', 'text'],
  ['maintenance_msg', 'Maintenance message', 'text'],
  ['force_sub_channels', 'Force-sub channels (comma)', 'text'],
  ['log_channel_id', 'Log channel id', 'text'],
  ['lock_channel_id', 'Lock channel id', 'text'],
  ['filename_tag', 'Filename tag', 'text'],
  ['progress_style', 'Progress style', 'select:classic,striped,glow,segmented'],
  ['default_max_file_mb', 'Default max file MB', 'number'],
  ['chunk_mb', 'Upload chunk MB', 'number'],
  ['maintenance', 'Maintenance mode', 'bool'],
  ['registration', 'Registration open', 'bool'],
  ['filename_tag_enabled', 'Filename tag on', 'bool'],
  ['animated_emoji', 'Animated emoji (36 ids bundled)', 'bool'],
];
async function loadSettings() {
  const d = await api('/admin/api/settings');
  const s = d.settings;
  $('#settingsGrid').innerHTML = SETTING_FIELDS.map(([k, label, kind]) => {
    const v = s[k];
    if (kind === 'bool') return '<label class="switch"><input type="checkbox" data-k="' + k + '"' +
      (v ? ' checked' : '') + '><span>' + label + '</span></label>';
    if (kind.startsWith('select:')) {
      const opts = kind.slice(7).split(',');
      return '<label class="field"><span>' + label + '</span><select data-k="' + k + '">' +
        opts.map(o => '<option ' + (o === v ? 'selected' : '') + '>' + o + '</option>').join('') +
        '</select></label>';
    }
    return '<label class="field"><span>' + label + '</span><input type="' +
      (kind === 'number' ? 'number' : 'text') + '" data-k="' + k + '" value="' +
      esc(v ?? '') + '"></label>';
  }).join('') +
  Object.entries(s.features || {}).map(([k, v]) =>
    '<label class="switch"><input type="checkbox" data-feat="' + k + '"' + (v ? ' checked' : '') +
    '><span>feature: ' + k + '</span></label>').join('');
  $('#cfgList').innerHTML = Object.entries(d.cfg).map(([k, v]) =>
    '<div class="kv"><span>' + k + '</span><span>' + esc(typeof v === 'object'
      ? JSON.stringify(v) : v) + '</span></div>').join('')
    + (d.cfg.telegram_cloud_api
        ? '<div class="card tight" style="margin-top:10px;border-color:#b45309">' +
          '<b>⚠️ Telegram cloud API: files over ' + d.cfg.max_file_mb_reachable +
          ' MB cannot be downloaded.</b><div class="mini dim" style="margin-top:6px">' +
          'A user can send a 2 GB video, but <code>getFile</code> refuses anything over ' +
          '20 MB on api.telegram.org, so DriveUp can never read those bytes. ' +
          'Files above the limit are now refused at intake with a clear message ' +
          'instead of failing mid-upload. To lift this to 2 GB, run a local Bot API ' +
          'server and set <code>TELEGRAM_API_URL</code>.</div></div>'
        : '');
}
$('#emojiTest').onclick = async () => {
  try {
    const d = await api('/admin/api/emoji/preview', { method:'POST', body:{} });
    $('#emojiOut').innerHTML =
      '<div class="kv"><span>catalogue</span><span>' + d.catalogue + ' ids</span></div>' +
      '<div class="kv"><span>on</span><span><code style="white-space:pre-wrap">' +
        esc(d.deck) + '</code></span></div>' +
      '<div class="kv"><span>wire</span><span class="dim">tags added: ' +
        (d.on.match(/tg-emoji/g)||[]).length + '</span></div>';
    toast('Preview rendered');
  } catch (e) { toast(e.message, 'err'); }
};
$('#saveSettings').onclick = async () => {
  const patch = {};
  $$('#settingsGrid [data-k]').forEach(i => {
    patch[i.dataset.k] = i.type === 'checkbox' ? i.checked
      : (i.type === 'number' ? Number(i.value) : i.value);
  });
  const feats = {};
  $$('#settingsGrid [data-feat]').forEach(i => { feats[i.dataset.feat] = i.checked; });
  if (Object.keys(feats).length) patch.features = feats;
  try { await api('/admin/api/settings', { method:'PUT', body: patch });
    toast('Saved', 'ok'); loadSettings(); } catch (e) { toast(e.message, 'err'); }
};

// ── audit ──────────────────────────────────────────────────────────────
async function loadAudit(q='') {
  const d = await api('/admin/api/audit?q=' + encodeURIComponent(q));
  $('#auditTbl tbody').innerHTML = d.audit.map(a =>
    '<tr><td class="mini" data-l="When">' + when(a.ts) +
    '</td><td class="mono mini" data-l="Actor">' + a.actor +
    '</td><td data-l="Action"><span class="pill">' + esc(a.action) + '</span></td>' +
    '<td class="mono mini" data-l="Target">' + esc(a.target) +
    '</td><td class="mini dim" data-l="Detail">' + esc((a.detail||'').slice(0, 80)) +
    '</td></tr>').join('') || '<tr><td class="dim">empty</td></tr>';
}
$('#aGo').onclick = () => loadAudit($('#aq').value);

// ── danger ─────────────────────────────────────────────────────────────
$$('[data-danger]').forEach(b => b.onclick = async () => {
  const act = b.dataset.danger;
  if (act === 'purge_history' &&
      prompt('Type the database name to confirm') !== 'driveup') {
    toast('Cancelled', ''); return;
  }
  try {
    const r = await api('/admin/api/danger/' + act, { method:'POST', body:{} });
    toast(act + ' done' + (r.backup ? ' · ' + r.backup.users + ' users' : ''), 'ok');
  } catch (e) { toast(e.message, 'err'); }
});

$('#lockBtn').onclick = async () => {
  // the session cookie is HttpOnly — only the server can really end it
  try { await api('/admin/api/logout', { method:'POST', body:{} }); } catch (_) {}
  location.reload();
};

boot();
</script>
</body>
</html>""".replace("__BRAND__", brand).replace("__ADMIN_CSS__", ADMIN_CSS).replace("__PLANS_JS__", PLANS_JS)
