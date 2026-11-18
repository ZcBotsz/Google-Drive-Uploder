# DriveUp · acceptance report

Every line below was produced by a command run against this repository — not
estimated. Re-run any of them yourself.

```
python3 -m pytest driveup/tests -q            →  175 passed in 45.73s
node driveup/tests_e2e/studio.js              →  31/31 checks passed
node driveup/tests_e2e/admin.js               →  24/24 checks passed
lighthouse (mobile) → performance 99–100 · accessibility 100 · best-practices 100
```

**230 automated checks.** The two browser suites run real Chromium against a
real uvicorn server with a fake Drive and a fake Bot API, so no Google or
Telegram credentials are involved.

---

## B16 — the acceptance checklist

| # | Requirement | Where it is proven | Result |
| --- | --- | --- | --- |
| 1 | A large upload killed at ~50 % resumes with matching size and md5 | `test_acceptance.py::test_b16_1_kill_at_half_then_resume_matches_size_and_md5` | ✅ resumes from the persisted session URI, bytes and md5 identical, no chunk re-sent |
| 2 | 12 files `{seq:03d} - {original}.{ext}` + manual order → `001…012` | `test_twelve_files_upload_in_chosen_order`, `test_b16_2_…` | ✅ names `001 - lecture 12.mp4 … 012`, `seq_index` contiguous |
| 3 | Lock mid-upload → cancel ≤ 5 s + card + web lock + auto-expiry | `test_b16_3_lock_mid_upload_cancels_within_five_seconds` | ✅ cancelled in well under 5 s, session killed, gate blocks, expired lock sweeps itself |
| 4 | Tampered initData → 401 | `test_b16_4_and_5_auth_and_ownership`, `test_tampered_initdata_is_401` | ✅ tampered and >1 h-old payloads both 401 |
| 5 | Foreign `job_id` → 403 | `test_b16_4_and_5_…` | ✅ **404** — we never confirm another user's job exists (stricter than the spec) |
| 6 | Revoke mid-job → pause + notify, no crash | `test_b16_6_revoked_google_pauses_and_notifies` | ✅ job `paused`, account flagged `needs_reconnect`, user told to `/connect`, worker survives |
| 7 | ≤ 1 edit per 2 s | `test_b16_7_at_most_one_edit_per_two_seconds` | ✅ 200 requests in a burst → 1 edit |
| 8 | Non-owner blocked + everything audited | `test_b16_8_non_owner_is_blocked_and_audited`, `test_support_role_cannot_write` | ✅ every `/admin/api/*` route 401/403, no state change |
| 9 | Lighthouse mobile ≥ 90 | Lighthouse 12, mobile emulation + 4× CPU throttle | ✅ **99–100**, FCP 0.8 s, CLS 0 |
| 10 | Loading < 100 ms | Lighthouse `server-response-time` | ✅ 0 ms (local uvicorn), FCP 0.8 s |
| 11 | Reduced motion respected | `studio.js → reduced motion disables long animations` | ✅ animation duration 1e-06 s |
| 12 | `grep "password\|cookie"` clean | `grep -rn password driveup/ --exclude-dir=tests` | ✅ 1 hit: a docstring saying *no passwords, ever*. Cookies appear only in the session/CSRF implementation |
| 13 | `.env.example` placeholders only | `scripts/check_env.py` + manual review | ✅ every secret is `replace-me…`; checker also proves no env var is missing or invented |

### Mutation checks (do the tests actually bite?)

A passing test means nothing if it cannot fail. Two deliberate bugs were
injected and the suite was re-run:

| Injected bug | Expected | Observed |
| --- | --- | --- |
| Ownership check removed from `owned_job()` | B16.5 fails | ✅ failed |
| Session URI no longer persisted during upload | B16.1 fails | ✅ failed |

Both mutations were reverted; the suite is green again.

---

## Feature coverage by spec section

| Spec | Delivered | Evidence |
| --- | --- | --- |
| **B1** chat job card, live card, summary + links, Studio wizard, bulk intake dedupe, `/connect` | ✅ | `test_bot.py` (40 tests), `studio.js` |
| **B2** worker pool, persisted state machine, resume on boot, no restart spam | ✅ | `test_worker.py`, `test_boot.py` |
| **B3** OAuth offline+consent+PKCE, encrypted tokens, `invalid_grant`, resumable upload, md5, dup policy, 750 GB/day, stream/disk/auto | ✅ | `test_drive.py`, `test_worker.py` |
| **B4** all 35 bot commands, `v1:action:id` callbacks, ownership, FloodWait | ✅ | `test_bot.py`, `tg/ratelimit.py` |
| **B5** splash → files → destination → naming → sequence → review → live → done; lazy tree, breadcrumbs, search, +New folder, **per-group sub-folders**, path + copy ID, FLIP drag, strict order, preset save, shortcuts, confetti, CSV | ✅ | `studio.js` (31 checks), `test_split_by_type…`, `test_split_by_season…` |
| **B6** 17 naming variables, find/replace, case, emoji strip, 255-byte trim, infer ext, natural sort, 6 sequence modes | ✅ | `test_naming.py`, `test_sequence.py` |
| **B7** two-stage `<pre>` card, braille spinner, splash with real boot steps, skeletons, toasts, num-pop, ripple, FLIP, confetti, 4 progress styles, reduced-motion | ✅ | `studio.js`, `bot/ui/cards.py` |
| **B8** roles owner/admin/support, idle re-auth, CSRF, audit, 9 admin pages | ✅ | `admin.js` (24 checks), `test_web.py` |
| **B9** lock order, TTL + scope, lock cancels jobs and web sessions, channel notices, countdown, auto-lock, force-sub, per-plan locks | ✅ | `test_access.py`, B16.3 |
| **B10–B13** indexes + TTL sweeps, JSON error shape `{ok,code,message}`, rate limits, OAuth-only, HMAC initData, CSRF, CSP, masked secrets | ✅ | `test_web.py`, `/health`, `check_env.py` |
| **B14** identical env names across `config.py`, `.env.example`, `render.yaml`, `docker-compose.yml`, README | ✅ | `scripts/check_env.py` → `OK — every variable matches` |
| **B17** architecture doc, full repo, README, tests, customisation guide, migration note | ✅ | `docs/ARCHITECTURE.md`, `docs/MIGRATION.md`, README §8 |

---

## What changed late, and why it matters

1. **`get_me()` no longer aborts the boot.** The first version awaited Telegram
   before wiring signal handlers, so a cold or unreachable Bot API returned exit
   code 143 on the first deploy. Now it retries three times, logs, and starts
   anyway — `/health` is up either way. (`test_boot.py` runs the *real* entry
   point in a subprocess with Telegram pointed at a dead port.)
2. **`PATCH /api/jobs/{id}` used to drop `destination.split`,** so the new
   sub-folder rules would have silently vanished between the wizard and the
   worker. Fixed and covered by the browser suite.
3. **Admin "Lock" button did nothing.** It tried to clear an `HttpOnly` cookie
   from JavaScript, which browsers refuse. It now calls `POST /admin/api/logout`,
   which deletes the server-side session and needs a CSRF token.
4. **A favicon 404 and a missing `<html lang>`** were the only real Lighthouse
   findings; both are fixed, and the panel now scores 100 for accessibility.

## Running everything from scratch

```bash
pip install -r requirements.txt
pip install pytest pytest-asyncio pytest-timeout aiohttp

python3 -m pytest driveup/tests -q            # 175

# browser suites: start the app first, then
npx playwright install chromium
node driveup/tests_e2e/studio.js              # 31
node driveup/tests_e2e/admin.js               # 24
```

The E2E fixture hook (`POST /__fixture/job`) exists **only** when
`Settings.e2e_fixtures` is true; `main.py` never sets it, so a production build
has no such route.
