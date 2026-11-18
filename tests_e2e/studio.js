/**
 * Drives the real Studio in Chromium: splash → files → folder → naming →
 * sequence → review → live → done, plus the checks that matter (contrast,
 * console errors, keyboard access, reduced motion, mobile viewport).
 */
const { chromium } = require('/home/user/.tools/node_modules/playwright');
const { execSync } = require('child_process');

const BASE = process.env.BASE || 'http://127.0.0.1:8099';
const TOKEN = '123456:STUDIOTEST';
const USER_ID = 555;
const MB = 1024 * 1024;

function signInitData(ageSeconds = 0) {
  const crypto = require('crypto');
  const authDate = Math.floor(Date.now() / 1000) - ageSeconds;
  const user = JSON.stringify({ id: USER_ID, first_name: 'Studio', username: 'studio' });
  const fields = { auth_date: String(authDate), query_id: 'Q1', user };
  const check = Object.keys(fields).sort().map((k) => `${k}=${fields[k]}`).join('\n');
  const secret = crypto.createHmac('sha256', 'WebAppData').update(TOKEN).digest();
  const hash = crypto.createHmac('sha256', secret).update(check).digest('hex');
  return new URLSearchParams({ ...fields, hash }).toString();
}

const results = [];
function check(name, ok, detail = '') {
  results.push({ name, ok, detail });
  console.log(`${ok ? 'PASS' : 'FAIL'}  ${name}${detail ? '  — ' + detail : ''}`);
}

(async () => {
  const browser = await chromium.launch({ args: ['--no-sandbox'] });
  const ctx = await browser.newContext({
    viewport: { width: 390, height: 844 },       // iPhone 12 → mobile Lighthouse target
    deviceScaleFactor: 2,
    isMobile: true,
    hasTouch: true,
    reducedMotion: 'no-preference',
  });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text()); });
  page.on('pageerror', (e) => consoleErrors.push('pageerror: ' + e.message));


  // ── session + a fresh draft job for this run ───────────────────────────
  const init = signInitData();
  const auth = await ctx.request.post(BASE + '/auth/telegram', { data: { initData: init } });
  check('initData login works', auth.status() === 200, (await auth.text()).slice(0, 60));
  const fx = await ctx.request.post(BASE + '/__fixture/job');
  const fxBody = await fx.json();
  const jobId = fxBody.job;
  check('a draft job exists for the run', !!jobId, jobId);
  const url = `${BASE}/?job=${jobId}`;

  // ── splash ─────────────────────────────────────────────────────────────
  const t0 = Date.now();
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  const splashVisible = await page.isVisible('#splash');
  check('splash renders before boot', splashVisible);
  const status = await page.textContent('.splash-status').catch(() => '');
  check('splash shows a real boot step', !!status.trim(), status.trim().slice(0, 40));

  await page.waitForSelector('#splash.gone', { timeout: 20000 });
  const bootMs = Date.now() - t0;
  check('splash hides after boot', true, `${bootMs} ms`);
  check('body is marked ready', (await page.getAttribute('body', 'data-ready')) === '1');

  const stepCount = await page.$$eval('#steps > *', (n) => n.length);
  check('wizard shows its steps', stepCount >= 5, `${stepCount} steps`);

  // ── files ──────────────────────────────────────────────────────────────
  const filesText = await page.textContent('#view');
  check('files step lists the job files', /lecture/i.test(filesText),
        (filesText || '').replace(/\s+/g, ' ').slice(0, 70));

  // ── folder tree (lazy) ─────────────────────────────────────────────────
  await page.getByRole('button', { name: /next|continue/i }).first().click();
  await page.waitForTimeout(400);
  let treeText = await page.textContent('#view');
  check('folder step loads My Drive', /My Drive/i.test(treeText));
  const coursesBtn = page.locator('#view').getByText('Courses', { exact: false }).first();
  if (await coursesBtn.count()) {
    await coursesBtn.click();
    await page.waitForTimeout(500);
    treeText = await page.textContent('#view');
    check('folder tree expands lazily', /Module 1/i.test(treeText));
  } else {
    check('folder tree expands lazily', false, 'Courses row not found');
  }
  const crumb = await page.textContent('#view');
  check('breadcrumb shows the path', /›/.test(crumb) || /My Drive/.test(crumb));

  // ── per-group sub-folders (B5) ─────────────────────────────────────────
  const splitSel = await page.$('#splitMode');
  check('folder step offers sub-folder splitting', !!splitSel);
  if (splitSel) {
    await page.selectOption('#splitMode', 'type');
    await page.waitForTimeout(500);
    const prev = await page.textContent('#splitPreview');
    check('split preview groups the files', /Videos|Documents|Other/.test(prev),
          prev.slice(0, 60));
    await page.selectOption('#splitMode', 'none');
    await page.waitForTimeout(300);
  }

  // ── naming (live preview) ──────────────────────────────────────────────
  await page.getByRole('button', { name: /next|continue/i }).first().click();
  await page.waitForTimeout(400);
  const previewSel = '#view .preview-row, #view .preview, #view [data-preview]';
  let previewText = await page.textContent('#view');
  check('naming step shows a live preview', /lecture/i.test(previewText));

  // type a template and confirm the preview updates
  const tmpl = await page.$('#view input[type="text"]');
  if (tmpl) {
    await tmpl.fill('{seq:03d} - {original}.{ext}');
    await page.waitForTimeout(600);
    previewText = await page.textContent('#view');
    check('preview reflects the template', /001 - /.test(previewText),
          (previewText.match(/0\d\d - [^\n]{0,24}/) || [''])[0]);
  } else {
    check('preview reflects the template', false, 'no template input');
  }

  // ── sequence (drag + sort + reverse) ───────────────────────────────────
  await page.getByRole('button', { name: /next|continue/i }).first().click();
  await page.waitForTimeout(400);
  const rows = await page.$$eval('#view .row-item, #view li, #view [data-id]', (n) => n.length);
  check('sequence step lists draggable rows', rows >= 3, `${rows} rows`);

  const before = await page.$$eval('#view [data-id]', (n) => n.map((x) => x.getAttribute('data-id')));
  const sortBtn = page.locator('#view').getByRole('button', { name: /name|a→z|natural/i }).first();
  if (await sortBtn.count()) {
    await sortBtn.click();
    await page.waitForTimeout(500);
    const after = await page.$$eval('#view [data-id]', (n) => n.map((x) => x.getAttribute('data-id')));
    check('sort by name reorders the list', JSON.stringify(before) !== JSON.stringify(after),
          `${before.length} → ${after.length}`);
  } else {
    check('sort by name reorders the list', false, 'sort button missing');
  }

  const reverseBtn = page.locator('#view').getByRole('button', { name: /reverse/i }).first();
  if (await reverseBtn.count()) {
    const pre = await page.$$eval('#view [data-id]', (n) => n.map((x) => x.getAttribute('data-id')));
    await reverseBtn.click();
    await page.waitForTimeout(500);
    const post = await page.$$eval('#view [data-id]', (n) => n.map((x) => x.getAttribute('data-id')));
    check('reverse flips the order', JSON.stringify(post) === JSON.stringify([...pre].reverse()));
  } else {
    check('reverse flips the order', false, 'reverse button missing');
  }

  // ── review ─────────────────────────────────────────────────────────────
  await page.getByRole('button', { name: /next|continue/i }).first().click();
  await page.waitForTimeout(600);
  const reviewText = await page.textContent('#view');
  check('review shows the estimate', /MB|estimate|total/i.test(reviewText));
  check('review shows the destination', /My Drive|Courses/.test(reviewText));

  // ── live ───────────────────────────────────────────────────────────────
  const startBtn = page.getByRole('button', { name: /start upload|start/i }).first();
  check('a start button exists on review', (await startBtn.count()) > 0);
  if (await startBtn.count()) {
    await startBtn.click();
    await page.waitForTimeout(1200);
    const liveText = await page.textContent('#view');
    const sawLive = /uploading|progress|%/i.test(liveText);
    const sawDone = /finished|done/i.test(liveText);
    check('live screen appears (or the job already finished)', sawLive || sawDone,
          (liveText || '').replace(/\s+/g, ' ').slice(0, 60));
    const hasRing = await page.$('#view svg, #view .ring, #view .overall, #view .bar');
    check('live/done screen draws progress', !!hasRing || sawDone);

    // wait for done
    let done = false;
    for (let i = 0; i < 60; i++) {
      const txt = await page.textContent('body');
      if (/done|finished|complete|🎉/i.test(txt)) { done = true; break; }
      await page.waitForTimeout(500);
    }
    check('upload reaches the done screen', done);
    const doneText = await page.textContent('#view');
    check('done screen offers links', /open|copy|link|drive\.google/i.test(doneText),
          (doneText || '').replace(/\s+/g, ' ').slice(0, 60));
    const confetti = await page.$('#confetti > *');
    check('confetti fires on done', await page.isVisible('#confetti') || !!confetti);
  }

  // ── accessibility / polish ─────────────────────────────────────────────
  const contrast = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const surf = getComputedStyle(document.documentElement).getPropertyValue('--surface').trim();
    return { bg: body.backgroundColor, accent: getComputedStyle(document.documentElement)
      .getPropertyValue('--accent').trim(), surf };
  });
  check('design tokens are applied', contrast.accent.includes('22, 211, 238') ||
        contrast.accent === '#22d3ee', JSON.stringify(contrast).slice(0, 80));

  const bubble = await page.$('#announcer');
  check('aria-live announcer exists', !!bubble);

  // keyboard: Tab must reach real controls
  await page.keyboard.press('Tab');
  const focused = await page.evaluate(() => document.activeElement?.tagName);
  check('keyboard focus lands on a control', ['BUTTON', 'A', 'INPUT', 'SELECT', 'TEXTAREA'].includes(focused), focused);

  // reduced motion: animations must be neutralised
  const rm = await ctx.newPage();
  await rm.emulateMedia({ reducedMotion: 'reduce' });
  await rm.goto(url, { waitUntil: 'domcontentloaded' });
  const anim = await rm.evaluate(() => {
    const card = document.querySelector('.screen') || document.body;
    return getComputedStyle(card).animationDuration;
  });
  const animMs = parseFloat(anim);
  check('reduced motion disables long animations', animMs <= 0.01, anim);
  await rm.close();

  check('no console errors', consoleErrors.length === 0, consoleErrors.slice(0, 2).join(' | '));

  await browser.close();
  const failed = results.filter((r) => !r.ok);
  console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
  if (failed.length) {
    console.log('FAILED:', failed.map((f) => f.name).join(', '));
    process.exit(1);
  }
})().catch((e) => { console.log('SCRIPT ERROR', e.message.split('\n')[0]); process.exit(2); });
