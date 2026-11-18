# ZC Drive Uploader 🚀

**Telegram → Google Drive, done properly.**
Send files to a bot, pick a folder, rename them with a template, order them, and
watch a live animated progress card until every file is in Drive. There is also
a full browser **Upload Studio** (Mini App) for people who prefer a file tree to
a chat, and an **admin panel** for the person running the server.

One Python process. No database server. No passwords.

```
Telegram ──▶ Bot (aiogram 3) ──▶ worker pool ──▶ Google Drive
                    │                                ▲
                    └──▶ FastAPI (Studio + admin) ───┘
                                SQLite (WAL)
```

---

## What it can do

| Area | Highlights |
| --- | --- |
| **Chat flow** | Job card → folder → naming → sequence → start; live `<pre>` progress card; final summary with links |
| **Studio (Mini App)** | Splash → files → destination tree → naming preview → drag-to-sort sequence → review → live progress (SSE) → done screen with confetti and CSV |
| **Naming** | 17 variables (`{seq:03d}`, `{date}`, `{size}`, `{caption}` …), find/replace with regex, case tools, emoji strip, 255-byte trimming, dedupe |
| **Ordering** | Telegram order, natural name sort, size, date, or manual; renumber `seq_index` on every change |
| **Uploads** | Resumable sessions, md5 verification, duplicate policy (rename/skip/overwrite), 750 GB/day guard, resume after a restart |
| **Access** | OAuth (offline + consent + PKCE), encrypted refresh tokens, per-plan quotas, locks with TTL, force-sub |
| **Admin** | 9 pages: dashboard, users, jobs/queue, locks, plans, broadcast, settings, audit, danger zone |
| **Polish** | 4 progress styles, braille spinner, reduced-motion, ARIA live regions, i18n (en/hi), keyboard shortcuts |

---

## 1 · Before you start

You need:

1. **A Telegram bot token** — DM [@BotFather](https://t.me/BotFather) → `/newbot`.
2. **Your Telegram numeric id** — DM [@userinfobot](https://t.me/userinfobot). You become the owner.
3. **A Google Cloud project** — free, takes about 10 minutes (next section).
4. **A public HTTPS URL** — Telegram Mini Apps refuse plain HTTP. Render, Railway,
   Fly.io, a VPS with Caddy, or Cloudflare Tunnel all work.

> **No Google Cloud yet?** You can still run the bot: `/connect` will tell users
> Drive is unavailable, and everything else (jobs, naming, ordering, admin) works.

---

## 2 · Google Cloud, step by step

### 2.1 Create the project

1. Open <https://console.cloud.google.com/>.
2. Top bar → project selector → **New project** → name it `ZC Drive Uploader` → **Create**.
3. Wait for the bell notification, then select the new project.

### 2.2 Enable the API

1. Left menu → **APIs & Services → Library**.
2. Search **Google Drive API** → open it → **Enable**.

### 2.3 Consent screen

1. **APIs & Services → OAuth consent screen** (newer consoles: *Google Auth Platform*).
2. **User type: External** → **Create**.
3. App name `ZC Drive Uploader`, your support email, developer contact = your email → **Save and continue**.
4. **Scopes** → **Add or remove scopes** → paste:
   - `https://www.googleapis.com/auth/drive.file` — the default, uploads only what the app creates
   - `https://www.googleapis.com/auth/drive` — only if you set `DRIVE_SCOPE_MODE=full`
     (needed to browse and write *any* folder the user already has)

   Add the one you plan to use, then **Update** → **Save and continue**.
5. **Test users** → **Add users** → your own Gmail address → **Save**.
   (While the app is in *Testing*, only these accounts can consent. **Publish** the
   app later to allow everyone — Google may ask for verification if you use the
   full `drive` scope.)

### 2.4 The OAuth client

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Web application**.
3. **Authorised redirect URIs** → **Add URI** and paste **exactly**:

   ```
   https://YOUR-DOMAIN/auth/google/callback
   ```

   `PUBLIC_URL` + `/auth/google/callback`, nothing else. A single stray slash is
   the most common `redirect_uri_mismatch` cause.
4. **Create** → copy the **Client ID** and **Client secret** into `GOOGLE_CLIENT_ID`
   and `GOOGLE_CLIENT_SECRET`.

> Running locally? Use `http://127.0.0.1:8080/auth/google/callback` and
> `PUBLIC_URL=http://127.0.0.1:8080`. Google allows `http` for loopback hosts.

### 2.5 Optional: the Picker key

If you want the *in-Studio* Google Picker (Choose folder → native Drive dialog):

1. **Credentials → Create credentials → API key**.
2. Restrict it to your domain and to the Drive API; put it in `PICKER_API_KEY`.

Without this key the Studio uses its own built-in folder tree — the Picker is a
convenience, not a requirement.

---

## 3 · Configure Telegram

1. **Mini App** — DM [@BotFather](https://t.me/BotFather) → `/newapp` → pick your bot →
   title `DriveUp Studio` → upload any 640×360 PNG → **Web App URL** = your
   `PUBLIC_URL`. The bot's **🌐 Open Upload Studio** button opens it with a signed
   `initData` payload.
2. **Menu button** (optional but nice) → `/setmenubutton` → choose the app you just made.
3. **Commands** — send this block to BotFather, then `/setcommands`:

   ```
   start - Open the main menu
   connect - Link your Google Drive
   folders - Pick a destination folder
   naming - Rename files with a template
   sequence - Choose the upload order
   queue - See the active job and queue
   history - Past uploads
   mystats - Your usage
   plan - Plans and limits
   settings - Your preferences
   studio - Open the Upload Studio
   disconnect - Unlink Drive
   account - Account overview
   cancel - Cancel the current job
   help - How it works
   ```

4. **Force-join channel** (optional): add the bot as an admin of your channel and set
   `FORCE_SUB_CHANNELS=@yourchannel` — users must join before `/start` proceeds.

---

## 4 · Environment variables

Copy `.env.example` to `.env` and fill it in. Only **seven** values are required.

| Variable | Required | What it does |
| --- | --- | --- |
| `BOT_TOKEN` | **yes** | From BotFather. Full secret — never commit it |
| `OWNER_ID` | **yes** | Your numeric id; unlocks the admin panel |
| `GOOGLE_CLIENT_ID` | **yes** | OAuth client from §2.4 |
| `GOOGLE_CLIENT_SECRET` | **yes** | Its secret |
| `TOKEN_ENC_KEY` | **yes** | Fernet key that encrypts stored refresh tokens |
| `SESSION_SECRET` | **yes** | Signs the Studio session cookie |
| `PUBLIC_URL` | **yes** | Public https URL of this app |
| `GOOGLE_REDIRECT_URI` | recommended | `PUBLIC_URL/auth/google/callback` — set it explicitly |
| `DRIVE_SCOPE_MODE` | – | `file` (default, least privilege) or `full` |
| `UPLOAD_MODE` | – | `auto` streams files >32 MiB (default), `stream`, or `disk` |
| `UPLOAD_CHUNK_MB` | – | Drive chunk size, multiple of 256 KiB; default `8` |
| `MAX_GLOBAL_UPLOADS` | – | Uploads running at once, whole server; default `4` |
| `MAX_USER_UPLOADS` | – | …per single user; default `2` |
| `DB_PATH` | – | SQLite file; default `data/driveup.sqlite3` |
| `TEMP_DIR` / `DOWNLOAD_DIR` | – | Scratch space for `UPLOAD_MODE=disk|auto` |
| `FORCE_SUB_CHANNELS` | – | Comma-separated `@channel` / `-100…` ids |
| `LOG_CHANNEL_ID` | – | Where audit and upload events are posted |
| `LOCK_CHANNEL_ID` | – | Where lock/unlock notices go (falls back to `LOG_CHANNEL_ID`) |
| `DUMP_CHANNEL_ID` | – | Mirror channel for copies of uploaded files |

When configured, each completed Telegram-media upload is copied to the dump channel with an audit note; link-based uploads produce an audit note (the bot cannot copy remote content that was not sent to Telegram). Channel copy failures are best-effort and never fail the Google Drive upload. The copy mirrors the original Telegram media/caption, so disclose this in your bot privacy notice.
| `BRAND_NAME`, `SUPPORT_CONTACT`, `PLAN_PRICE_TEXT` | – | Texts shown to users |
| `PROGRESS_STYLE` | – | `classic · striped · glow · segmented` |
| `DEFAULT_MAX_FILE_MB`, `DEFAULT_DAILY_GB` | – | Free-tier limits before a plan is granted |
| `MAINTENANCE` | – | `true` locks everyone out except admins |
| `ADMIN_IDLE_MINUTES` | – | Admin re-auth timer; default `10` |
| `AUTO_LOCK_LIMIT` / `AUTO_LOCK_MINUTES` / `AUTO_LOCK_WINDOW` | – | Abuse auto-lock |
| `ENABLE_ANIMATED_EMOJI` | – | Premium emoji on messages (needs a bot with access) |
| `PUBLIC_STUDIO` | – | `false` for a bot-only deployment |

> **Verify it yourself:** `python3 scripts/check_env.py` prints
> `OK — every variable matches` and fails if `config.py`, `.env.example` and the
> deploy files ever drift apart.

Generate the two keys:

```bash
python3 -c "import secrets; print('SESSION_SECRET =', secrets.token_urlsafe(48))"
python3 -c "from cryptography.fernet import Fernet; print('TOKEN_ENC_KEY =', Fernet.generate_key().decode())"
```

---

## 5 · Run it

### Docker (recommended)

```bash
cp .env.example .env      # then edit .env
docker compose up -d --build
docker compose logs -f    # "DriveUp 1.0.0 ready on 0.0.0.0:8080"
```

### Plain Python

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python3 -m driveup                    # or: python3 main.py
```

`GET /health` → `{"ok": true, "version": "1.0.0", ...}`. Open `PUBLIC_URL/admin`
after signing in from the bot.

### Step-by-step, click by click

[`docs/DEPLOY.md`](docs/DEPLOY.md) is a full walkthrough for **Railway** and
**Render** — Telegram bot, Google Cloud consent screen, variables, volumes,
the Mini-App URL, and a troubleshooting table keyed to the exact error text.

### Render

1. Push this repo to GitHub.
2. Render → **New → Blueprint** → pick the repo (it reads `render.yaml`).
3. Type the `sync: false` secrets into the dashboard.
4. Set `PUBLIC_URL` to the `onrender.com` URL Render gives you, then go back to
   Google Cloud and add `…/auth/google/callback` to the redirect URIs.

> Use at least the **Starter** plan. The free tier sleeps after 15 minutes of
> inactivity and kills in-flight uploads.

### Railway

```bash
railway init && railway up
railway variables set BOT_TOKEN=... OWNER_ID=... PUBLIC_URL=https://...
```

Attach a **Volume** at `/app/data` so SQLite survives deploys.

### Behind nginx / Caddy

The app must be reachable over HTTPS for Telegram. Caddy:

```
driveup.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

---

## 6 · First run checklist

1. Send `/start` — you should get the welcome card with a **📂 Choose folder** button.
2. Send `/connect` → **Link Google Drive** → consent → “Drive connected”.
3. Send a few files (as documents, not media, if you want the original name kept).
4. The job card appears. Tap **✏️ Naming** → try `{seq:03d} - {original}.{ext}`.
5. **🔢 Sequence** → **Name (natural)** to get `1, 2, 10` instead of `1, 10, 2`.
6. **🚀 Start** — the progress card appears and updates every ~2.5 s.
7. Tap **🌐 Open Upload Studio** — the same job opens in the browser.
8. Open `PUBLIC_URL/admin` for the panel (owner only).

### Two files in one folder

By default the app **renames**: `video.mp4` → `video (1).mp4`. Change the policy
per job in the Studio (**Review → If a file exists**) or with `/settings`:
`rename`, `skip`, or `overwrite`.

---

## 7 · How the pieces fit

| Folder | Responsibility |
| --- | --- |
| `driveup/config.py` | Every env var, validated at boot, masked when printed |
| `driveup/db/` | SQLite schema + repository functions (one place for SQL) |
| `driveup/core/` | `naming`, `sequence`, `worker`, `access` (gates & locks), `progress` |
| `driveup/drive/` | OAuth, token encryption, resumable upload engine, folder policy |
| `driveup/tg/` | Telegram → disk/stream download with progress and cancel |
| `driveup/bot/` | aiogram handlers, keyboards, animated cards |
| `driveup/web/` | FastAPI routes, Studio (`static/app.js`), admin panel |
| `driveup/tests/` | 175 pytest tests: naming, sequence, drive, worker, access, web, bot, acceptance, boot |
| `driveup/tests_e2e/` | Real-Chromium runs of the Studio (`studio.js`) and admin (`admin.js`) |

Deeper notes (state machine, resume contract, callback format, security model)
are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). The full acceptance
matrix — every requirement with the test that proves it — is in
[`docs/ACCEPTANCE.md`](docs/ACCEPTANCE.md), and the deployment walkthrough is
[`docs/DEPLOY.md`](docs/DEPLOY.md).

### Upload pipeline

```
item → downloading ──▶ uploading ──▶ verifying ──▶ done
            │                │
            └── cancelled ◀──┴── failed (retry button)
```

* A resumable session URI is persisted **before** the first chunk, so a restart
  or a lost connection resumes from `Range` instead of byte zero.
* `5xx` → status probe with `Content-Range: bytes */total`; `404` → new session;
  `403` rate-limit → backoff, `403 quotaExceeded` → job paused until tomorrow.
* After the last chunk the file's md5 is compared with Drive's `md5Checksum`.

---

## 8 · Customising

**Branding** — `BRAND_NAME` in `.env`, or live in **Admin → Settings**. Default: `ZC Drive Uploader`; support: `@ZCQXx`.
Colours live in `driveup/web/static/app.css` under `:root`:

```css
--bg:#081014; --surface:#0f1d22; --accent:#22d3ee; --accent-2:#22d3a5;
```

**Progress styles** — `/settings` in the bot (or Admin → Settings →
`progress_style`): `classic`, `striped`, `glow`, `segmented`.

**Naming variables** — `core/naming.py` has one dict; add a key there and it
appears in the Studio help automatically.

**Plans** — seeded on first boot (`free`, `pro`, `business`) and editable in
Admin → Plans. Each plan carries `max_file_mb`, `daily_gb`, `files_per_job`,
`days` and the feature flags `web_studio, bulk_jobs, shared_drives,
large_files_2gb, custom_naming, presets`.

**Strings** — `strings.py` holds the en/hi table. Missing keys fall back to en.

**Lock screen** — `core/access.py::gate()` is the single choke point for
maintenance → ban/lock → force-sub → plan → quota. Add a rule there and both the
bot and the Studio obey it.

---

## 9 · Testing

```bash
python3 -m pytest driveup/tests -q                    # 175 tests, ~46 s

# browser suites (need a real Chromium + a running server on :8099)
npx playwright install chromium
node driveup/tests_e2e/studio.js                       # 31 checks
node driveup/tests_e2e/admin.js                        # 24 checks
```

The browser suites boot against a fake Drive + fake Bot API, so they never touch
Google or Telegram. The fixture hook they use (`POST /__fixture/job`) only exists
when `Settings.e2e_fixtures` is on, which the production entry point never sets.

---

## 10 · Troubleshooting

| Symptom | Cause & fix |
| --- | --- |
| `redirect_uri_mismatch` | The URI in Google Cloud must equal `PUBLIC_URL/auth/google/callback` **exactly** — https, no trailing slash, no `www` unless `PUBLIC_URL` has it |
| `access_denied` / “app not verified” | Consent screen is in *Testing*: add the account under **Test users**, or publish the app |
| Bot silent, log says `Unauthorized` | `BOT_TOKEN` is wrong or revoked — re-copy from BotFather |
| Studio says “Open this page from the bot” | You opened it without Telegram. Use the **🌐 Open Upload Studio** button, or log in first |
| Mini App opens blank | `PUBLIC_URL` must be **https**; Telegram loads the page in a WebView that blocks mixed content |
| `403 storageQuotaExceeded` | Drive is full, or the account hit 750 GB/day — the job pauses and resumes tomorrow |
| Upload stuck at 0 % | Telegram file > 20 MB *and* you use a cloud Bot API server: run `--local` or set `TELEGRAM_API_URL` to your local server |
| `invalid_grant` after weeks | Refresh token revoked (password change, 6-month idle). `/connect` again — the bot flags it as **needs reconnect** |
| Admin panel 403 | Your id is not `OWNER_ID`/`ADMIN_IDS`, or the idle timer expired — reload to re-auth |
| `database is locked` | Two processes on one SQLite file. Run **one** instance per database |
| Files uploaded twice | Two `docker compose` replicas, or a retried job — keep one replica; dedupe is on `file_unique_id` |
| Nigerian/idle warnings | `AUTO_LOCK_*` tripped an auto-lock: Admin → Locks → Unlock |

Logs: `docker compose logs -f` or `journalctl -u driveup -f`. Set
`LOG_CHANNEL_ID` to get the same events inside Telegram.

---

## 11 · Security notes

* **OAuth only.** The app never asks for a Google password, cookies, or TOTP codes,
  and never stores them. Refresh tokens are encrypted with `TOKEN_ENC_KEY`.
* **`drive.file` by default** — the app can only touch files it created. `full`
  is opt-in because Google reviews it more strictly.
* **Signed `initData`** — the Studio verifies Telegram's HMAC, rejects
  `auth_date` older than one hour, and compares in constant time.
* **CSRF** — every admin write needs `X-CSRF-Token`; cookies are `HttpOnly`,
  `SameSite=Lax`, `Secure` behind HTTPS.
* **Ownership** — every `job_id` is checked against the caller; foreign ids 404/403.
* **CSP** — `default-src 'self'`, `frame-ancestors telegram.org`, `nosniff`.

Run `grep -rn "password\|cookie" driveup/ --include=*.py` before shipping: the
only hits should be cookie-configuration code and comments.

---

## 12 · Coming from DriveGram?

`docs/MIGRATION.md` maps every DriveGram feature to where it lives here — and
lists the three things that are deliberately gone (password login, mirror/leech,
Mongo). Short version:

1. Copy your `.env`; rename `API_ID`/`API_HASH` → `TELEGRAM_API_ID`/`TELEGRAM_API_HASH` (still accepted).
2. Users re-run `/connect` once — Mongo-held tokens are not portable.
3. Anything stored in Mongo is not migrated; jobs are per-user and short-lived.

---

## License

MIT — do what you want, no warranty. Google Drive quotas are yours to mind.
