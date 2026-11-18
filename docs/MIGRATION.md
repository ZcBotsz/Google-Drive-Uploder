# Migrating from DriveGram

DriveGram was a large (≈84k-line) Telegram → Drive bot. DriveUp is a rewrite
with the same job to be done and far less machinery. This page is the honest
map: what came across, what changed shape, and what is gone on purpose.

## 1 · Feature map

| DriveGram | DriveUp | Notes |
| --- | --- | --- |
| Upload files/links to Drive | ✅ same | Files only — links/mirror are out (see §3) |
| Folder picker in chat | ✅ `/folders`, `/setfolder` | Paged inline keyboard, per-user default |
| Rename before upload | ✅ `/naming` | 17 variables, regex find/replace, live preview |
| Order selection | ✅ `/sequence` | 6 modes incl. manual, `seq_index` renumbered on change |
| Progress message | ✅ better | Animated `<pre>` card, 12-sample speed, ETA, 4 styles |
| Web upload panel | ✅ **Upload Studio** | 7-step wizard, SSE live progress, confetti finish |
| Admin bot commands | ✅ same list | `/stats /users /lock /unlock /broadcast /export …` |
| Admin web panel | ✅ 9 pages | Dashboard, Users, Jobs, Locks, Plans, Broadcast, Settings, Audit, Danger |
| Plans & quotas | ✅ | Seeded `free/pro/business`, editable live |
| Force-subscribe | ✅ | Multi-channel, cached 10 min, fail-open |
| Locks with TTL | ✅ | Scope `all|upload|web`, countdown screen in bot **and** web |
| Mongo (motor) | ❌ → **SQLite** | One file, WAL, TTL sweeps in-process |
| Pyrogram/kurigram | ❌ → **aiogram 3** | Actively maintained, `Bot` API + `Dispatcher` |
| Password / OTP Drive login | ❌ **removed** | Impossible for third parties; OAuth only |
| Mirror / leech | ❌ **removed** | Out of scope by design |
| Custom Bot API server | ⚠️ optional | `TELEGRAM_API_URL` empty = cloud API (20 MB download cap); set it to a local server for 2 GB files |

## 2 · Moving your deployment

1. **Environment.** Rename the two legacy keys — both spellings are accepted, so
   you can also just add the new ones:

   | DriveGram | DriveUp |
   | --- | --- |
   | `API_ID` | `TELEGRAM_API_ID` |
   | `API_HASH` | `TELEGRAM_API_HASH` |

   Then add the ones driveup needs and DriveGram did not have:
   `SESSION_SECRET`, `TOKEN_ENC_KEY`, `PUBLIC_URL`, `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI`.
   `python3 scripts/check_env.py` tells you what is still missing.

2. **Users.** DriveGram rows are not imported. The first `/start` creates the user
   row; plan/quota defaults come from `DEFAULT_MAX_FILE_MB` / `DEFAULT_DAILY_GB`.

3. **Drive connections.** **Must be redone.** DriveGram stored tokens in Mongo;
   the format and the encryption key differ, and a token encrypted with an unknown
   key is worthless. Every user runs `/connect` once (about 20 seconds each).
   Their files in Drive are untouched.

4. **Jobs.** Short-lived queue state. Old DriveGram jobs are not resumed; a new
   DriveUp job picks up whatever the user sends next.

5. **Uploads in flight.** Drain DriveGram before switching. Two bots polling the
   same token will fight over `getUpdates` and one of them will look broken.

## 3 · What is gone, and why

* **Password / OTP login to Google.** Google does not let third-party apps collect
  a user's password, and the "app password" workaround was killed for consumer
  accounts. OAuth is the only correct path — that is `BOT → 🔗 Connect` now.
* **Mirror/leech.** DriveUp is a Telegram → Drive tool, not a cloner. Copying a
  channel's files may violate its rights; the code paths that did it are gone.
* **Mongo.** SQLite in WAL mode handles this write pattern (~thousands of rows
  per job) with zero ops. Single process, single file, one backup (`cp`).

## 4 · Behaviour differences to tell your users

| Topic | DriveGram | DriveUp |
| --- | --- | --- |
| Consent screen | asked every reconnect | `access_type=offline` + `prompt=consent` stored once |
| Scope | full Drive | `drive.file` by default (`DRIVE_SCOPE_MODE=full` to widen) |
| Duplicate name | overwrote | **renames** by default (`rename|skip|overwrite`) |
| Restart mid-job | job lost | resumes from the byte it stopped at |
| Progress card | edits a text message | animated `<pre>` card, edits ≤ 1 / 2 s |
| Locks | unlimited | optional TTL, auto-lock on repeated failures |
| Admin | bot commands only | bot commands **and** a web panel |

## 5 · Rollback

DriveUp never writes to DriveGram's Mongo database and does not reuse its token
files, so rolling back is: stop DriveUp → start DriveGram with its old `.env`.
The only shared mutable state is the Google account itself (Drive files and the
granted OAuth token), and a re-grant on either side simply replaces the other.
