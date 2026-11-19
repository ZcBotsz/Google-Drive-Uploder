/* DriveUp Studio — helpers, state, toasts, api.
   Classic script (no bundler); all app-*.js files share one global scope. */
/* DriveUp Studio — state, helpers, boot.
   Classic script (no bundler): app-core.js defines state + helpers,
   app-views.js the wizard steps, app.js wires the page and boots. */
/* ═══════════════════════════════════════════════════════════════════════════
 * DriveUp Studio — vanilla JS, no build step, works as a Telegram Mini-App.
 *
 * Every network call goes through api(), which:
 *   · sends the session cookie and the Mini-App initData
 *   · normalises {ok:false,code,message} into a thrown Error
 *   · shows the offline banner while the network is down
 *
 * State lives in `S` and is rendered by explicit render functions — no virtual
 * DOM, so a test can assert on the real DOM after each action.
 * ═══════════════════════════════════════════════════════════════════════════ */
'use strict';

const S = {
  jobId: '',
  source: {},
  me: null,
  steps: ['files', 'folder', 'naming', 'sequence', 'review', 'live', 'done'],
  step: 'files',
  files: [],
  folder: { id: 'root', path: 'My Drive' },
  split: null,                       // {mode:'type'|'season'|'date'|'custom', …} or null
  naming: { template: '{original}.{ext}', start: 1, step: 1, prefix: '', suffix: '',
            find: '', replace: '', regex: false, case: 'keep', strip_emoji: true,
            space: ' ', max_bytes: 255, ext_rule: 'keep', force_ext: '' },
  sequence: { mode: 'telegram_order', strict: true, order: [] },
  conflict: 'rename',
  share: 'restricted',
  preview: [],
  tree: { root: null },
  selected: 'root',
  scale: 1,
  sse: null,
  startedAt: 0,
  reduced: false,
  savePreset: false,
};

// ── tiny helpers ────────────────────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
const el = (tag, cls, html) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (html !== undefined) n.innerHTML = html;
  return n;
};
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) =>
  ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

function humanBytes(n) {
  n = Number(n || 0);
  if (n < 1024) return `${n} B`;
  const u = ['KB', 'MB', 'GB', 'TB'];
  let i = -1;
  do { n /= 1024; i++; } while (n >= 1024 && i < u.length - 1);
  return `${n.toFixed(n < 10 ? 2 : 1)} ${u[i]}`;
}
function humanSecs(s) {
  s = Math.max(0, Math.round(s || 0));
  const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`
           : `${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}
function naturalKey(s) {
  return String(s).split(/(\d+)/).filter(Boolean)
    .map((p) => (/^\d+$/.test(p) ? Number(p) : p.toLowerCase()));
}
function cmpNatural(a, b) {
  const A = naturalKey(a), B = naturalKey(b);
  for (let i = 0; i < Math.max(A.length, B.length); i++) {
    if (A[i] === undefined) return -1;
    if (B[i] === undefined) return 1;
    if (A[i] < B[i]) return -1;
    if (A[i] > B[i]) return 1;
  }
  return 0;
}
const debounce = (fn, ms = 300) => {
  let t;
  return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); };
};

// ── toasts ──────────────────────────────────────────────────────────────────
function toast(msg, kind = '') {
  const box = $('#toasts');
  const n = el('div', `toast ${kind}`, esc(msg));
  box.appendChild(n);
  setTimeout(() => {
    n.classList.add('out');
    setTimeout(() => n.remove(), 260);
  }, 3200);
  return n;
}

// ── api ─────────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  const init = {
    credentials: 'same-origin',
    headers: { ...(opts.headers || {}) },
    ...opts,
  };
  const isForm = typeof FormData !== 'undefined' && init.body instanceof FormData;
  if (init.body && typeof init.body !== 'string' && !isForm) {
    init.body = JSON.stringify(init.body);
    init.headers['Content-Type'] = 'application/json';
  }
  const tgInit = window.Telegram?.WebApp?.initData;
  if (tgInit) init.headers['X-Telegram-Init-Data'] = tgInit;
  let r;
  try {
    r = await fetch(path, init);
  } catch (e) {
    setOffline(true);
    throw new Error('network down — retrying…');
  }
  setOffline(false);
  let data = {};
  try { data = await r.json(); } catch (_) { /* empty body is fine */ }
  if (!r.ok) {
    const msg = data.message || data.detail || `HTTP ${r.status}`;
    // Inside Telegram the page has initData but no cookie yet: trade one for the
    // other once. Outside Telegram a signed /start?t= link already did this.
    if (r.status === 401 && window.Telegram?.WebApp?.initData && !sessionStorage.getItem('du_tried')) {
      sessionStorage.setItem('du_tried', '1');
      try { await authWithTelegram(); return api(path, opts); } catch (_) {}
    }
    if (r.status === 401) showAuthWall(msg);
    throw new Error(msg);
  }
  return data;
}

function setOffline(on) {
  $('#offline').classList.toggle('show', !!on);
}

// ── motion guard ────────────────────────────────────────────────────────────
const motionOK = () => !window.matchMedia('(prefers-reduced-motion: reduce)').matches;



// ── Mini-App handshake ──────────────────────────────────────────────────────
async function authWithTelegram() {
  const initData = window.Telegram?.WebApp?.initData;
  if (!initData) throw new Error('no initData');
  const r = await fetch('/auth/telegram', {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ initData }),
  });
  if (!r.ok) throw new Error('Telegram sign-in failed (' + r.status + ')');
  return r.json();
}
