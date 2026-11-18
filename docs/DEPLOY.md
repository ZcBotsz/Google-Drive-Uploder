# Deploying ZC Drive Uploader — Railway & Render, click by click

This guide takes you from "nothing" to "the bot answers on Telegram and the
Studio opens in a browser". It is written for **both** platforms side by side —
do the four shared steps once, then pick Railway **or** Render.

Total time: about 25 minutes, most of it waiting for Google's consent screen.

---

## 0 · Pick a platform

| | Railway | Render |
| --- | --- | --- |
| Free to try | $5 trial credit | Free tier (no disk → **not usable long-term**) |
| Cheapest usable plan | Hobby ≈ $5/mo, usage-based | Starter $7/mo + $0.25/GB disk |
| Persistent disk | Volume, free up to plan limit | Disk, paid plans only |
| Sleeps when idle | No (on paid) | **Yes** on free — kills uploads |
| Custom domain | Yes, free | Yes, free |
| Best for | quickest path, cheap | predictable monthly price |

> **Rule of thumb:** if you only ever run one thing, use Railway. If you want a
> flat bill and don't mind a slow first request, use Render **Starter**.

The shipped defaults brand the service **ZC Drive Uploader** and direct support to **@ZCQXx**; change `BRAND_NAME` or `SUPPORT_CONTACT` if desired. Either way you need the same things: a GitHub repo, a Telegram bot token, a
Google OAuth client, and two generated keys.

---

## 0.5 · ⚠️ Repo layout — read this before you deploy

Railway and Render read config from the **root of your repository**. If your
repo root contains a single `driveup/` folder and nothing else, the build fails
with:

```
✖ Railpack could not determine how to build the app.
   The app contents that Railpack analyzed contains:
   ./
   └── driveup/
```

That means `railway.json`, `Dockerfile` and `requirements.txt` are one level too
deep — the platform never saw them. Fix it with **either**:

* **Root Directory** → `driveup` (Railway: Settings → Source; Render:
  Settings → Root Directory), or
* use `ZC-Drive-Uploader.zip`, whose files sit at the archive root.

Full walkthrough: [`FIX-RAILWAY-BUILD.md`](FIX-RAILWAY-BUILD.md).

---

## 1 · What the zip is

`ZC-Drive-Uploader.zip` contains the exact repository to push. Unzip it anywhere:

```
driveup/                  ← push THIS folder's contents to GitHub
├── driveup/              the application package
├── main.py               entry point
├── requirements.txt
├── Dockerfile            used by Railway and Render
├── docker-entrypoint.sh  fixes volume permissions, drops root
├── docker-compose.yml    for a VPS instead of a PaaS
├── render.yaml           one-click blueprint (optional)
├── railway.json
├── .env.example          every variable, placeholders only
├── README.md
└── docs/                 ARCHITECTURE · DEPLOY (this file is docs/DEPLOY.md)
```

There is **no `.env`** in the zip — secrets never ship in an archive. You will
type them into Railway/Render instead (step 5). Set a unique, long `ADMIN_PASSWORD`
in the platform's secret settings to enable the second admin password gate. Admin
access still requires an authorized Telegram account; the extra unlock expires
in 10 minutes. Do not commit this password.

---

## 2 · Generate the two keys (2 minutes)

Run these on your laptop. You only do it once; **keep the output**.

```bash
python3 -c "import secrets; print('SESSION_SECRET =', secrets.token_urlsafe(48))"
```

```bash
python3 -c "from cryptography.fernet import Fernet; print('TOKEN_ENC_KEY =', Fernet.generate_key().decode())"
```

If `cryptography` isn't installed: `pip install cryptography`, or use any online
Fernet generator — you need a **44-character base64 string ending in `=`**.

Write both values in a notes file. Put them in your password manager.

> ⚠️ `TOKEN_ENC_KEY` encrypts every user's Google refresh token. If you lose or
> change it later, every existing user gets "needs reconnect" and must run
> `/connect` again. Generate once, keep forever.

---

## Telegram audit and dump channels

The project has optional `LOG_CHANNEL_ID` and `DUMP_CHANNEL_ID` settings. Create two private Telegram channels, add the bot as an administrator with permission to post, and paste each channel’s numeric ID (usually begins `-100`) into Render’s Environment settings. Do not copy any bot token from the older reference ZIP: that archive contained credential-looking values; rotate the bot token with BotFather before deployment and use only freshly issued secrets.

`LOG_CHANNEL_ID` receives successful upload records (user ID, file name, size, job and Drive link) and item failures. Existing bot-side events such as draft/start/cancel are also sent there. `DUMP_CHANNEL_ID` receives a copy of the original Telegram media message after a successful Drive upload plus a metadata record. URL uploads cannot be mirrored as media; they receive a record only. The dump operation is best-effort and does not undo a successful Drive upload. Tell users that uploaded Telegram media is also copied to your private dump channel.

For Render, set both variables on the **bot service**, not the local Telegram Bot API service. If either is blank, that feature is disabled. Verify posting rights by running one small upload and checking both channels.

## 3 · Create the Telegram bot (3 minutes)

1. Open Telegram, message **[@BotFather](https://t.me/BotFather)**.
2. Send `/newbot`.
3. **Name** (shown in chats): `DriveUp`
4. **Username** (must end in `bot`): e.g. `MyDriveUpBot`
5. BotFather replies with a line like:

   ```
   Use this token to access the HTTP API:
   1234567890:AAH4xK9pQrStUvWxYz1234567890abcdefg
   ```

   That whole line is your **`BOT_TOKEN`**. Save it.

6. Get your numeric id: message **[@userinfobot](https://t.me/userinfobot)**.
   It replies `Id: 123456789`. That number is your **`OWNER_ID`** — it unlocks
   the admin panel.

7. Set the commands users will see. In BotFather: `/setcommands` → choose your
   bot → paste:

   ```
   start - Open the main menu
   connect - Link your Google Drive
   folders - Pick a destination folder
   naming - Rename files with a template
   sequence - Choose the upload order
   queue - See the active job
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

> The **Mini App** (`/newapp`) step comes *after* you have a public URL — see
> step 8. You cannot register the Studio URL until the app is deployed.

---

## 4 · Google Cloud: OAuth client (10 minutes, the fiddly one)

You need a real `https` URL before Google will accept the redirect URI, so:

* **Render** — your URL is predictable: `https://<service-name>.onrender.com`
* **Railway** — deploy first (steps 5–7), then come back here with the domain
  Railway gives you.

If you want to do Google first, use Render's predictable URL, or deploy Railway
with a placeholder and fix the redirect URI afterwards (it takes 30 seconds).

### 4.1 Project

1. Go to <https://console.cloud.google.com/>.
2. Top bar → project dropdown → **New project** → name `DriveUp` → **Create**.
3. Wait for the toast, then make sure the project selector shows `DriveUp`.

### 4.2 Enable the Drive API

1. ☰ → **APIs & Services** → **Library**.
2. Search `Google Drive API` → open it → **Enable**.

### 4.3 Consent screen

1. ☰ → **APIs & Services** → **OAuth consent screen**
   (newer consoles call it *Google Auth Platform → Branding*).
2. User type: **External** → **Create**.
3. Fill in:
   * App name: `DriveUp`
   * User support email: your address
   * Developer contact email: your address
4. **Save and continue**.
5. **Scopes** → **Add or remove scopes** → paste this into the filter box:

   ```
   https://www.googleapis.com/auth/drive.file
   ```

   Tick it → **Update** → **Save and continue**.

   * Want users to browse and write to folders they already have? Add
   `https://www.googleapis.com/auth/drive` too and set
   `DRIVE_SCOPE_MODE=full` later. Google reviews this scope more strictly.*

   **What this means for users:** with `file`, DriveUp can only write to folders it
   created itself. Telling a user to pick an existing folder from My Drive leads to
   `403 PERMISSION_DENIED` on the first upload. DriveUp now checks the folder before
   starting the job and explains this on screen — no silent failure.

6. **Test users** → **Add users** → add the Gmail address you will test with →
   **Save**.

   While the app is in *Testing*, **only these addresses can connect**. That is
   fine for you and a few friends; to open it to everyone, go back and
   **Publish app** (Google may ask for verification if you use the full scope).

### 4.4 The client itself

1. ☰ → **APIs & Services** → **Credentials** → **Create credentials** →
   **OAuth client ID**.
2. Application type: **Web application**.
3. Name: `DriveUp Web`.
4. **Authorised redirect URIs** → **Add URI** → paste **exactly**:

   ```
   https://YOUR-DOMAIN/auth/google/callback
   ```

   Examples:
   * `https://driveup.onrender.com/auth/google/callback`
   * `https://driveup-production.up.railway.app/auth/google/callback`

   No trailing slash. No `www.` unless your domain has it. `http` for localhost
   only.
5. **Create**. A dialog shows **Client ID** and **Client secret** — copy both.

That is the whole Google side. Three values result:

| Value | Goes into |
| --- | --- |
| Client ID (`…apps.googleusercontent.com`) | `GOOGLE_CLIENT_ID` |
| Client secret (`GOCSPX-…`) | `GOOGLE_CLIENT_SECRET` |
| The redirect URI you typed | `GOOGLE_REDIRECT_URI` |

---

## 5 · Push the code to GitHub

```bash
cd driveup            # the folder you unzipped
git init
git add .
git commit -m "DriveUp 1.0.0"
git branch -M main
git remote add origin https://github.com/YOUR-NAME/driveup.git
git push -u origin main
```

Check on github.com that `Dockerfile`, `driveup/`, `main.py` and
`requirements.txt` are at the **top level** of the repo. If GitHub shows a
single folder you clicked into, your files are nested one level too deep —
move them up or point the platform at the subfolder (both have a "Root
Directory" setting).

> The `.gitignore` already excludes `.env`, `data/`, `__pycache__`.
> **Never commit `.env`.**

---

## 6 · Deploy on Railway

### 6.1 Create the project

1. Go to <https://railway.app> → **Login with GitHub**.
2. **New Project** → **Deploy from GitHub repo** → pick `driveup`.
3. Railway reads `railway.json` + `Dockerfile` and starts building. The first
   build takes 2–4 minutes (it installs Python packages).

### 6.2 Add the variables

1. Click the service box → **Variables** tab → **Raw Editor** (fastest way).
2. Paste this block, replacing the `<…>` values:

   ```
   BOT_TOKEN=<token from BotFather>
   OWNER_ID=<your numeric id>
   BOT_USERNAME=<your bot username without @>
   GOOGLE_CLIENT_ID=<…apps.googleusercontent.com>
   GOOGLE_CLIENT_SECRET=<GOCSPX-…>
   TOKEN_ENC_KEY=<44-char Fernet key>
   SESSION_SECRET=<48-char random string>
   PUBLIC_URL=https://<domain-you-get-in-6.4>
   GOOGLE_REDIRECT_URI=https://<domain-you-get-in-6.4>/auth/google/callback
   DRIVE_SCOPE_MODE=file
   UPLOAD_MODE=auto
   UPLOAD_CHUNK_MB=8
   MAX_GLOBAL_UPLOADS=4
   MAX_USER_UPLOADS=2
   ```

3. **Deploy** (Railway redeploys automatically when variables change).

> Don't set `PORT` or `HOST` — Railway injects `PORT`, and the app already
> binds `0.0.0.0`. Overriding them is the #1 cause of "service unavailable".

### 6.3 Add the volume (this is what keeps your data)

1. Right-click the service canvas → **Attach Volume** (or the service's
   **Volumes** tab → **Add Volume**).
2. **Mount path**: `/app/data` ← exactly this.
3. Save. Railway restarts the service.

Your SQLite file becomes `/app/data/driveup.sqlite3`. The container's entrypoint
fixes the volume's ownership on boot, so you don't get
`unable to open database file`.

### 6.4 Get the domain and finish

1. Service → **Settings** → **Networking** → **Generate Domain**.
2. Copy the `https://…up.railway.app` URL.
3. Go back to **Variables** and fix `PUBLIC_URL` and `GOOGLE_REDIRECT_URI` to
   that domain.
4. In Google Cloud → Credentials → your OAuth client → add
   `https://…up.railway.app/auth/google/callback` to the redirect URIs.
5. Open `https://…up.railway.app/health`. You want:

   ```json
   {"ok":true,"status":"ok","db":true,"version":"1.0.0"}
   ```

### 6.5 Keep it to ONE instance

Settings → **Deploy** → **Replicas = 1**. Two replicas share one SQLite file and
two bots poll the same token — you will get duplicated uploads and
`database is locked`. This app is deliberately single-process.

---

## 7 · Deploy on Render

### 7.1 The one-click way (uses `render.yaml`)

1. <https://dashboard.render.com> → **New** → **Blueprint**.
2. Connect GitHub → pick your repo.
3. Render reads `render.yaml` and shows **two** services: `driveup` (the bot,
   10 GB disk at `/app/data`) and `driveup-tg-api` (a self-hosted Bot API,
   private, 10 GB disk). Both must stay in the same region — the blueprint keeps
   both on `singapore`. See `docs/RENDER-LOCAL-API.md` for why the second
   service exists and what the 10 GB does and does not mean.
4. It lists the `sync: false` variables — type the ones from step 4 and 2:
   `BOT_TOKEN`, `OWNER_ID`, `BOT_USERNAME`, `GOOGLE_CLIENT_ID`,
   `GOOGLE_CLIENT_SECRET`, `TOKEN_ENC_KEY`, `SESSION_SECRET`, `PUBLIC_URL`,
   `GOOGLE_REDIRECT_URI`, and the four Telegram-API values
   (`API_ID`, `API_HASH`, `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`).
   `TELEGRAM_API_URL` / `TELEGRAM_FILES_URL` are filled in by Render itself
   (`fromService: hostport`) — never type them.
5. **Apply**. First build ≈ 3–5 minutes.

### 7.2 Or the manual way

1. **New** → **Web Service** → connect the repo.
2. **Language**: `Docker` (it finds the `Dockerfile` automatically).
3. **Instance type**: **Starter** or higher — the free tier cannot attach a
   disk and sleeps after 15 minutes, which kills uploads mid-flight.
4. **Advanced** → **Add Disk**:
   * Name `driveup-data`, Mount path `/app/data`, Size 10 GB (≥ your largest file)
5. **Environment** → add the same variables as in §6.2.
6. **Health Check Path**: `/health`
7. **Create Web Service**.

### 7.3 After the first deploy

1. Copy the URL Render shows: `https://<name>.onrender.com`.
2. Set `PUBLIC_URL` and `GOOGLE_REDIRECT_URI` to it, save (Render redeploys).
3. Add `https://<name>.onrender.com/auth/google/callback` in Google Cloud.
4. Hit `/health` — same JSON as above.

> Render's **Free** instances do not support disks. If you deploy free anyway,
> everything works until the instance restarts — then the database is empty.

---

## 8 · Register the Mini App (Studio) in BotFather

Now that you have a stable `https` domain:

1. Message **@BotFather** → `/newapp` → pick your bot.
2. **Title**: `DriveUp Studio`
3. **Description**: `Pick files, folder, names and order, then watch the upload.`
4. **Photo**: any square PNG, 640×640 or larger.
5. **Web App URL**: paste `https://YOUR-DOMAIN` (just the domain, no path).
6. Optionally `/setmenubutton` → choose the app you just created.

The bot's **🌐 Open Upload Studio** button already opens this URL with a signed
session — users never see a login screen.

> Telegram Mini Apps are loaded in a WebView and **refuse plain `http`**. Both
> Railway and Render give you `https` on their domains for free.

---

## 9 · First-run checklist (do this now)

Send these to your bot, in order:

1. `/start` → a welcome card with **📂 Choose folder**.
2. `/connect` → **Link Google Drive** → pick the test account you added in
   §4.3 → you may see *"Google hasn't verified this app"* → **Advanced** →
   **Go to DriveUp (unsafe)** → **Allow**. This is normal while the app is in
   Testing.
3. Back in Telegram: **"✅ Google Drive connected"**.
4. Send 3 files as **documents** (paperclip → File) so the original names survive.
5. The job card appears. Tap **🚀 Start** → watch the live progress card.
6. Tap **🌐 Open Upload Studio** → the Studio opens with the same job.
7. Open `https://YOUR-DOMAIN/admin` → sign in from the bot first → the 9-pane
   admin panel.

If `/connect` fails, jump to §11 — it is almost always the redirect URI.

### 9.1 · The 20 MB rule — read before you set plan limits

Telegram's **cloud** Bot API refuses `getFile` for any file over **20 MB**
(`400 Bad Request: file is too big`). Your users can *send* a 2 GB video to the
bot, but DriveUp cannot download a byte of it, so the job would fail at 0 %.

DriveUp now handles this honestly:

| What you see | What it means |
|---|---|
| Plans show **`20 MB`** even though you typed `2000` | The per-file size is clamped to what this server can actually fetch. |
| A red **`≤20 real`** badge in Admin → Plans | Telegram is the ceiling, not the plan. |
| An amber banner in Admin → Settings | You are on the cloud API. Same cause. |
| User sends an oversized file → a clear **"limit is 20 MB"** message | Refused at intake, before the job is created. No silent failure. |

**If you genuinely need 2 GB files**, you must run your own Bot API server.
This is **not** a checkbox: in `--local` mode the server returns an absolute
path on *its own* disk and does not serve files over HTTP, so on a
multi-service host (Railway/Render) you also need its file endpoint.

The full, click-by-click walkthrough is in **`docs/LOCAL-BOT-API-RAILWAY-STEPS.md`**. Summary:

```
tg-bot-api service  → ghcr.io/gramiojs/telegram-bot-api:latest
   TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_LOCAL=1 / FILE_SERVER=1
   volume at /var/lib/telegram-bot-api

bot service
   TELEGRAM_API_URL   = http://tg-bot-api.railway.internal:8081
   TELEGRAM_FILES_URL = http://tg-bot-api.railway.internal:8080
```

**Do not use `aiogram/telegram-bot-api`** — its file route is reported broken
and stale. DriveUp refuses to boot if the URL is set but the credentials or a
way to reach the files is missing.
---

## 10 · Updating a deployed version

**Railway** — push to `main`, it rebuilds automatically.
**Render** — push to `main`; with `autoDeploy: true` it rebuilds automatically.

Your database and volume are untouched by redeploys. To roll back, redeploy an
older commit from the dashboard.

---

## 10.5 · Link uploads, thumbnails and Drive copy

New in this build. All three are on by default and need no extra configuration.

**Paste a link.** Send any direct `http(s)` link in the chat and it streams
straight into Drive — no Telegram round-trip, so it is not limited by the 20 MB
`getFile` ceiling, and no full copy lands on the server disk. Rename on the way
in: `https://host/file.zip | my movie.mkv` (a `*` works too).

Guarded, at our end:

- only `http`/`https`; the host must resolve to a **public** address;
- loopback, `10/8`, `172.16/12`, `192.168/16`, `169.254/16` (cloud metadata),
  reserved, multicast and IPv6 loopback are **refused**;
- every redirect is re-validated before it is followed, because a public URL can
  302 to an internal one;
- the plan's per-file ceiling is enforced while reading, not after.

Two knobs, both in `.env.example`:

| Variable | Default | Meaning |
|---|---|---|
| `URL_UPLOAD_ENABLED` | `1` | `0` removes the feature entirely |
| `URL_UPLOAD_MAX_MB` | `2000` | hard ceiling for one link download |

> The guard closes the practical hole. It resolves the hostname and then connects,
> so a determined attacker could still race the DNS answer (rebinding). Treat it
> as a strong filter, not a sandbox.

**Thumbnails.** `/thumb_set`, then send a photo under 2 MB; every later upload
carries it as the Drive preview image. `/thumb` shows it, `/delthumb` removes it.
It is stored in SQLite, so it survives a redeploy — and the upload still works if
Drive rejects the hint.

**Copy.** `/copy <drive link>` duplicates a file or a whole folder inside Drive
without downloading anything. Folders are walked with a cycle guard, a
2,000-file ceiling and a depth cap. It only works for files the connected account
can already open.

## 11 · Troubleshooting, by exact message

| What you see | What it actually means | Fix |
| --- | --- | --- |
| `redirect_uri_mismatch` | The URI in Google Cloud ≠ the one the app sends | They must match **character for character**: `https`, the exact domain, `/auth/google/callback`, no trailing slash |
| `Error 403: access_denied` | Account not in **Test users** | Add it under Consent screen → Test users, or **Publish app** |
| `Google hasn't verified this app` | App is in Testing | **Advanced → Go to … (unsafe)** — expected, not a bug |
| Bot does not answer at all | Wrong/revoked token, or two bots polling | Re-copy `BOT_TOKEN`; stop any other deployment using it |
| `/health` → `502` / `Application failed to start` | Usually a missing required variable | Check logs for `ConfigError: …` — it names the variable |
| `unable to open database file` | Volume not mounted at `/app/data` | Railway: mount path exactly `/app/data`. Render: disk mount path `/app/data` |
| `database is locked` | More than one replica | Set replicas to **1** |
| Studio says *"Open this page from the bot"* | You opened the URL with no session | Use **🌐 Open Upload Studio** in the bot |
| Mini App blank/white screen | `PUBLIC_URL` not `https`, or it doesn't match the domain | Fix `PUBLIC_URL`, redeploy, reopen from the bot |
| `403 storageQuotaExceeded` | Drive full or 750 GB/day hit | Job pauses automatically and resumes tomorrow |
| `file is too big` / uploads stuck at 0 % | **Cloud Bot API caps `getFile` at 20 MB.** DriveUp now refuses these at intake with an explicit message — see **§9.1**. | Use a local Bot API server (`TELEGRAM_API_URL`) for 2 GB files; `UPLOAD_MODE=disk` does not help |
| Users must reconnect Drive after a redeploy | `TOKEN_ENC_KEY` changed | Restore the original key; then reconnect once |
| `QuotaExceeded` on Railway volume | Volume smaller than the file | Enlarge the volume or use `UPLOAD_MODE=stream` (default `auto` already streams >32 MiB) |
| Everything works but admin panel is 403 | Your id isn't `OWNER_ID` | Set `OWNER_ID` to the numeric id from @userinfobot |

**Reading logs**

* Railway: service → **Deployments** → **View Logs**.
* Render: service → **Logs** (live tail).

A healthy boot looks like:

```
config OK — bot_token=1234…TEST  google_client_id=***  fernet=set
scope mode: file · chunk: 8 MB · upload mode: auto
database ready at /app/data/driveup.sqlite3
web listening on 0.0.0.0:8080 (public: https://…)
bot @YourBot online — /start to begin
```

If you see `bot @… online` and the `/health` JSON, the deployment is correct.

---

## 12 · Costs and limits, plainly

| | Railway Hobby | Render Starter |
| --- | --- | --- |
| Base | $5/mo credit, then usage | $7/mo + disk |
| Disk | included in usage | $0.25/GB/mo |
| Sleep | never (paid) | never (paid) |
| Bandwidth | metered | 100 GB included |

**Telegram and Google cost nothing** for this app. The real limits you will hit
first are Google's: **750 GB per account per day**, and the **15 GB** free
Drive quota.

---

## 13 · Prefer your own server?

The zip ships a `docker-compose.yml`:

```bash
unzip ZC-Drive-Uploader.zip && cd driveup
cp .env.example .env
nano .env                     # fill in the 7 required values
docker compose up -d --build
docker compose logs -f
```

Put HTTPS in front with Caddy (one line):

```
driveup.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Then set `PUBLIC_URL=https://driveup.example.com` and add the matching redirect
URI in Google Cloud.
