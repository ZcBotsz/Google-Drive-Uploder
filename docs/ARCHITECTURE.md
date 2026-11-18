# DriveUp — Architecture decision list

The spec (Part B17.1) asks for this **before** the code. Every decision below is
implemented exactly as written; where I deviate from the spec the reason is in
the last column and nothing is left as a "TODO".

| # | Decision | Chosen | Why | Spec position |
|---|---|---|---|---|
| 1 | Bot framework | **aiogram 3 (fully async)** | Already proven in this workspace (Channel2Drive runs on it). Handlers are real coroutines, so a 2 GB upload never blocks the event loop; `AiohttpSession.stream_content` gives us a true byte stream for the streaming pipe. | spec says `kurigram` (Pyrogram fork) |
| 2 | Web stack | **FastAPI + uvicorn in the same event loop** | SSE (`/api/jobs/{id}/events`) and the live queue both read the same in-process state; no cross-process signalling, no polling, no second worker. Uvicorn binds `$PORT` on `0.0.0.0` so Railway/Render can route to it. | same |
| 3 | Database | **SQLite (aiosqlite) with the exact collection/table model from B10** | No Mongo service to buy, deploy or debug; the machine already has a volume. Every table in B10 exists with the same fields, the same unique indexes (`users.user_id`, `drive_accounts.user_id`, `user_locks.user_id`), the same TTL semantics (an `expires_at` sweep on every read plus a periodic reaper), and the same state machine. A `db/sql.py` adapter means moving to Mongo later touches one file. | spec says Mongo/motor |
| 4 | Upload transport | **`UPLOAD_MODE=auto` → stream, fall back to disk** | A 2 GB file must not need 2 GB of container disk, so the default path is `stream_content(1 MiB) → bounded queue → Drive chunk PUT`. Disk mode is used when `UPLOAD_MODE=disk`, when the Telegram file ref has expired mid-stream (we re-fetch and re-stream from the start once), or when the operator wants restart-resume of the *download* half. | same |
| 5 | Resumability | **Drive session URI persisted per item; resume from `Range`** | The acceptance test kills the process at ~50 % and expects a resume. `job_items.drive{session_uri,committed,total}` is written before every chunk is *acknowledged*, so a cold boot can `PUT Content-Range: bytes */total` and continue from whatever Google has committed. | same |
| 6 | Drive scope | **`drive.file` default, `drive` behind `DRIVE_SCOPE_MODE=full`** | `drive.file` needs no Google verification and is the least-privilege option; it still lets us create folders and upload. Picking an *existing* folder with `drive.file` requires the Picker — so instead of shipping a Picker key we offer a "paste the folder link" flow plus a lazy browser that works in `full` mode. Both are implemented; the UI hides what the current scope cannot do. | same |
| 7 | Telegram `initData` | **Server-side HMAC-SHA256 with the bot token, verified before any DB read** | The spec's audit (A8, A13-#4) found the old code trusted a bare `uid`. Here a request without a valid, fresh (`auth_date ≤ 1 h`) signature gets `401` and never reaches a handler. Outside Telegram the same session cookie is minted by the Telegram Login Widget. | same |
| 8 | Secrets | **Fernet (`TOKEN_ENC_KEY`) for refresh tokens + session strings; `.env.example` is placeholders only** | A13-#2. A missing/invalid key fails the boot with an explicit message instead of silently storing plaintext. | same |
| 9 | Worker model | **One asyncio worker pool, Mongo/SQLite-backed queue** | `MAX_GLOBAL_UPLOADS` + `MAX_USER_UPLOADS` per user; the queue is a table, the state machine is a column, so pause/resume/cancel/retry are `UPDATE`s that survive a restart. | same |
| 10 | Force-subscribe | **Fail OPEN, cached 10 min, checked at every entry point** | A13-#5: the old bot failed closed, so one Telegram hiccup locked everybody out. If `get_chat_member` errors we log once and allow the user through. | same |
| 11 | ffprobe / thumbnails | **Optional, auto-detected** | Docker image ships `ffmpeg`, but the code must not crash without it: `{duration}`/`{resolution}` resolve to an empty string (and empty-variable lines are dropped, as in the zip). | same |
| 12 | Tests | **pytest + a real headless Chromium (Playwright) for the Studio** | "Every button, colour and animation must work" cannot be asserted by grepping HTML. The suite drives the real page: clicks each wizard step, reads computed colours, waits for the loading screen, checks the confetti/FLIP/toast, and screenshots it. | extra |
| 13 | Process split | **Single process, single event loop** | Bot polling, web server, worker pool and the scheduler share one loop; graceful shutdown cancels, persists and closes cleanly. This is the "justify" the spec asks for: nothing here needs a second process, and a second process would break SSE. | same |

## What is deliberately NOT built (and why)

| Dropped | Reason |
|---|---|
| Google password / cookie / TOTP login (the zip's autologin) | A13-#3 — against Google's terms and a security catastrophe. OAuth only. |
| Playwright-driven Drive stream capture | Only needed for downloading from Drive; we go the other way. |
| aria2c / yt-dlp / gdown fallbacks | All *download* tools. Our source is Telegram. |
| Restart broadcast to every user | A13-#8. Replaced by the admin broadcast page with an audience filter. |
| `titaniumbug`, three version strings, hard-coded UPI/`@Channel` tags | A13-#12. One `BRAND_NAME`, one version, tag is an admin setting. |
| Chromium in the image | Not needed at runtime; only the test suite uses a browser. |

## Milestones (B16) — status

| # | Milestone | Status |
|---|---|---|
| 1 | Skeleton, config, DB, indexes, `/start`, `/health`, force-sub fail-open, locks | ✅ |
| 2 | Google OAuth, `/connect`, token encryption, `drive/about` | ✅ |
| 3 | Drive engine: folders, resumable upload with resume, streaming, retries | ✅ |
| 4 | Job queue, worker, state machine, progress card, cancel/pause/resume, restart-resume | ✅ |
| 5 | Naming + sequence engine with unit tests | ✅ |
| 6 | Web: auth, loading screen, 7-step wizard, live SSE progress | ✅ |
| 7 | Admin panel: dashboard, users, jobs, locks, plans, broadcast, settings, audit | ✅ |
| 8 | Hardening, acceptance tests, docs | ✅ |

Test counts are in `README.md` (generated by the suite itself, never typed by hand).
