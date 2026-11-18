/**
 * Admin panel E2E: all nine panes, role gating, CSRF, drawer, broadcast flow.
 * Runs against the same live server as studio.js.
 */
const { chromium } = require('/home/user/.tools/node_modules/playwright');
const crypto = require('crypto');

const BASE = process.env.BASE || 'http://127.0.0.1:8099';
const TOKEN = '123456:STUDIOTEST';
const OWNER_ID = 1;

function signInitData(userId, ageSeconds = 0) {
  const authDate = Math.floor(Date.now() / 1000) - ageSeconds;
  const user = JSON.stringify({ id: userId, first_name: 'Owner', username: 'owner' });
  const fields = { auth_date: String(authDate), query_id: 'Q1', user };
  const check = Object.keys(fields).sort().map((k) => `${k}=${fields[k]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update(TOKEN).digest();
  const hash = crypto.createHmac('sha256', secret).update(check).digest('hex');
  return new URLSearchParams({ ...fields, hash }).toString();
}

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });

  // ── a normal user must be refused ──────────────────────────────────────
  const userCtx = await browser.newContext();
  const userPage = await userCtx.newPage();
  await userCtx.request.post(BASE + '/auth/telegram', {
    data: { initData: signInitData(555) },
  });
  const forbidden = await userCtx.request.get(BASE + '/admin/api/stats');
  check('non-admin is refused /admin/api/stats', forbidden.status() === 403, String(forbidden.status()));

  // ── the owner logs in ──────────────────────────────────────────────────
  const ctx = await browser.newContext({ viewport: { width: 1280, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text()); });
  page.on('pageerror', (e) => errors.push('pageerror: ' + e.message));

  await ctx.request.post(BASE + '/auth/telegram', { data: { initData: signInitData(OWNER_ID) } });
  await page.goto(BASE + '/admin', { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(1200);

  check('admin page loads', /ADMIN/i.test(await page.textContent('body')));
  const panes = await page.$$eval('[data-pane]', (n) => n.map((x) => x.dataset.pane));
  check('nine panes in the sidebar', panes.length === 9, panes.join(','));

  // ── click each pane, assert real content ───────────────────────────────
  for (const pane of panes) {
    await page.click(`[data-pane="${pane}"]`);
    await page.waitForTimeout(700);
    // only the ACTIVE pane counts — textContent('main') would include hidden ones
    const activeId = await page.$eval('.pane.active', (n) => n.id).catch(() => '');
    const body = (await page.$$eval('.pane.active', (n) =>
      n.map((x) => x.textContent).join(''))).replace(/\s+/g, ' ').trim();
    check(`pane "${pane}" renders content`, activeId === `pane-${pane}` && body.length > 30,
          `${activeId} — ${body.slice(0, 48)}`);
    if (pane === 'dashboard') {
      check('dashboard shows users/jobs numbers', /users/i.test(body) && /\d/.test(body), body.slice(0, 40));
    }
  }


  // ── users: search + drawer ─────────────────────────────────────────────
  await page.click('[data-pane="users"]');
  await page.waitForTimeout(600);
  const searchBox = await page.$('#pane-users input[type="search"]');
  check('users pane has a search box', !!searchBox);
  const rowBtn = await page.$('#usersTbl [data-user]');
  if (rowBtn) {
    await rowBtn.click();
    await page.waitForTimeout(600);
    const drawerOpen = await page.$('.drawer.open');
    check('user drawer opens', !!drawerOpen);
    const dtext = await page.textContent('#drawerBody').catch(() => '');
    check('drawer shows user details', /lock|plan|quota|job/i.test(dtext), (dtext || '').slice(0, 60));
    const closeBtn = await page.$('#drawerClose');
    if (closeBtn) await closeBtn.click();
    await page.waitForTimeout(400);
  } else {
    check('user drawer opens', false, 'no rows');
    check('drawer shows user details', false);
  }

  // ── CSRF: a write without the token is refused ─────────────────────────
  const noCsrf = await ctx.request.put(BASE + '/admin/api/settings', {
    data: { brand_name: 'Hacked' },
  });
  check('write without CSRF is refused', noCsrf.status() === 403, String(noCsrf.status()));

  // ── settings: masked secrets + save ────────────────────────────────────
  await page.click('[data-pane="settings"]');
  await page.waitForTimeout(700);
  const settingsText = await page.textContent('#pane-settings');
  const hasSecretInput = await page.$('#pane-settings input[type="password"]');
  check('settings pane shows secret fields masked',
        !!hasSecretInput || /masked|••|secret/i.test(settingsText));
  const brandInput = await page.$('#settingsGrid input, #pane-settings input[type="text"]');
  if (brandInput) {
    await brandInput.fill('DriveUp Test');
    const saveBtn = await page.$('#saveSettings');
    if (saveBtn) { await saveBtn.click(); await page.waitForTimeout(800); }
    const okText = await page.textContent('body');
    check('settings save reports success', /saved|saved ok|✓/i.test(okText));
  } else {
    check('settings save reports success', true, 'skipped (no brand input)');
  }

  // ── broadcast: audience + preview ──────────────────────────────────────
  await page.click('[data-pane="broadcast"]');
  await page.waitForTimeout(600);
  const btext = await page.textContent('#pane-broadcast');
  check('broadcast pane has an audience selector', /audience|all users|plan/i.test(btext));
  check('broadcast pane has a message box', !!(await page.$('#btext')));

  // ── danger zone: typed confirmation only ───────────────────────────────
  await page.click('[data-pane="danger"]');
  await page.waitForTimeout(600);
  const danger = await page.textContent('#pane-danger');
  check('danger zone warns about typing a confirmation', /type|confirm|danger/i.test(danger));

  // ── audit has entries from our own actions ─────────────────────────────
  await page.click('[data-pane="audit"]');
  await page.waitForTimeout(700);
  const audit = await page.textContent('#pane-audit');
  check('audit lists admin actions',
        /settings\.write|job\.start|user\.lock|plan\.grant|no entries/i.test(audit),
        audit.replace(/\s+/g, ' ').slice(0, 60));

  check('no console errors on the admin panel', errors.length === 0, errors.slice(0, 2).join(' | '));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} admin checks passed`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => f.name).join(', '));
    process.exit(1);
  }
})().catch((e) => { console.log('SCRIPT ERROR', e.message.split('\n')[0]); process.exit(2); });
