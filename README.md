# ☁️ Telegram → Google Drive Uploader Bot — Advanced v2.0

A beautiful, smart, **production-ready advanced** Telegram bot that uploads **any file, video, or link** directly to **Google Drive** — with **OAuth per-user**, **single + batch uploads**, **automation**, **clone**, **admin panel**, **user settings**, and a **stunning Web Dashboard** to visually pick folders.

> 🚀 **Deploying on Render?** See **[DEPLOY_RENDER.md](./DEPLOY_RENDER.md)** for step-by-step Render deployment (Blueprint + Persistent Disk + UptimeRobot keep-alive).

Inspired by & combining the best of:
- [WZML-X](https://github.com/SilentDemonSD/WZML-X) — resumable upload, SA quota switching, task queue, automation
- [mirror-leech-telegram-bot](https://github.com/anasty17/mirror-leech-telegram-bot) — mirror/leech architecture, file selector, status
- [cdfxscrq/GDrive-Uploader-TG-Bot](https://github.com/cdfxscrq/GDrive-Uploader-TG-Bot) — Pyrogram + OAuth + clone + custom folder
- [aryanvikash/Google-Drive-Uploader](https://github.com/aryanvikash/Google-Drive-Uploader) — simple OAuth flow, TeamDrive support
- [kalanakt/All-Url-Uploader](https://github.com/kalanakt/All-Url-Uploader) — direct link handling

---

## ✨ Features

| Feature | Description |
|---|---|
| 🔗 **OAuth per User** | Each user connects their own Gmail via Google OAuth. Token auto-refresh, stored in `tokens/{id}.pickle` |
| 📤 **Single Upload** | Send Telegram file / direct link / Drive link → auto upload with progress |
| 📦 **Batch Upload** | `/batch` → send many files → `/done` → queue processes sequentially |
| 📂 **Web Folder Picker** | Beautiful HTML UI to browse Drive folders, search, create, select destination |
| 🤖 **Automation** | Auto-upload from channels/groups: `/auto <channel_id> <folder_url>` |
| 📋 **Clone** | `/clone <drive_url>` — copy Drive file/folder without downloading |
| 🌐 **Web Dashboard** | Drag-drop upload, stats, recent uploads, storage quota |
| ⚡ **Resumable Upload** | 100MB chunks, retry on 500/429, SA quota switching (WZML-X style) |
| 🔍 **Drive Manager** | `/list`, `/search`, `/newfolder`, `/delete`, `/setfolder` |
| 📊 **Status** | `/status` — storage usage, total files, recent uploads |
| 👑 **Admin Panel** | `/admin` — global stats, broadcast, ban/unban, users, restart, logs |
| ⚙️ **User Settings** | `/settings` — auto-share toggle, rename regex, folder management |
| 🛡️ **Advanced** | Ban system, file-size limits, flood control, WAL DB, health checks, keep-alive |

---

## 🚀 Quick Start

### 1. Create Telegram Bot
- Talk to [@BotFather](https://t.me/BotFather) → `/newbot` → copy **BOT_TOKEN**
- Talk to [my.telegram.org/apps](https://my.telegram.org/apps) → get **API_ID** & **API_HASH**

### 2. Create Google OAuth Client
1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create Project → Enable **Google Drive API** (APIs & Services → Library)
3. OAuth consent screen → External → Add scopes `.../auth/drive` → Save
4. Credentials → Create Credentials → **OAuth Client ID** → **Desktop app**
5. Download `client_secrets.json` → put in bot root folder

> **Detailed guide:** See [Google Cloud Setup](#google-cloud-setup) below

### 3. Configure & Run

```bash
git clone <this-repo>
cd telegram-drive-uploader
cp .env.example .env
# Edit .env with BOT_TOKEN, API_ID, API_HASH, OWNER_ID, WEB_BASE_URL
pip install -r requirements.txt
python bot.py
```

Bot starts + Web Dashboard on `http://localhost:8080`

### 4. Use It
1. Open Telegram → send `/start` to your bot
2. Tap **🔗 Connect Drive** → Authorize with Gmail → Paste code
3. Tap **📂 Choose Folder** → Web Picker opens → Select folder → **Use This Folder**
4. Send any file/link → Bot uploads → Get Drive link! ☁️

---

## 📋 Commands

```
/start          — Home with buttons
/connect        — Connect Google Drive (OAuth)
/revoke         — Disconnect Drive
/folders        — Web Folder Picker
/setfolder      — Set folder by URL (/setfolder clear to reset)
/newfolder      — Create folder
/list           — List current Drive folder
/search         — Search Drive
/clone          — Clone Drive file/folder
/batch          — Batch mode ON/OFF
/done           — Start batch upload
/cancel         — Cancel uploads
/auto           — Automation menu
/status         — Storage & stats
/web            — Web dashboard links
/help           — Help pages
```

**Send directly:**
- 📄 Telegram file/video/photo → auto upload
- 🔗 `https://...` direct link → download & upload
- 📋 `https://drive.google.com/...` → clone to your Drive
- 📦 Multiple links in one message → batch queue

---

## 🌐 Web Dashboard

| Page | URL | Purpose |
|---|---|---|
| Home | `/` | Landing + enter User ID |
| Dashboard | `/dashboard?user_id=ID` | Stats, quick upload, recent files |
| Folder Picker | `/picker?user_id=ID` | **Visual folder browser** — browse, search, create, select |
| Connect | `/connect?user_id=ID` | OAuth via web (alternative to Telegram) |

**Folder Picker features:**
- Breadcrumb navigation (My Drive › Folder › Subfolder)
- Click folder to enter, Select to choose
- Search Drive files/folders
- Create new folder inline
- One-click "Use This Folder" → saved to DB → Telegram uploads go there

---

## 🤖 Automation

Auto-upload every new file from a channel/group to Drive:

```
/auto -1001234567890 https://drive.google.com/drive/folders/xxx
```

1. Add bot as **admin** in channel/group
2. Run `/auto` with channel ID + folder URL
3. Bot watches channel → downloads → uploads → notifies you

Rules stored in DB, survive restarts.

---

## 🔧 Advanced

### Owner Single-User Mode (WZML-X style)
Generate `token.pickle` once, bot uploads as owner without per-user OAuth:
```bash
python gen_scripts/generate_drive_token.py
```

### Service Accounts (Unlimited Uploads)
Put `accounts/*.json` (SA keys), set `USE_SERVICE_ACCOUNTS=True` in `.env`. Bot auto-switches SA on quota hit — like WZML-X.

### Docker
```bash
docker-compose up -d
# or
docker build -t drive-uploader . && docker run -p 8080:8080 --env-file .env drive-uploader
```

### VPS Deploy
```bash
git clone <repo> && cd telegram-drive-uploader
pip install -r requirements.txt
nohup python bot.py > bot.log 2>&1 &
# or use pm2 / systemd
```

---

## 📁 Project Structure

```
telegram-drive-uploader/
├── bot.py                  # Main entry (bot + web thread)
├── config.py               # Env config
├── requirements.txt
├── .env.example
├── Dockerfile / docker-compose.yml
├── bot/
│   ├── core/               # config, database (SQLite), logger
│   ├── helper/             # gdrive (OAuth+upload), downloader, queue, automation, utils
│   └── modules/            # start, auth, upload, drive, batch, automation, status, help
├── web/
│   ├── app.py              # FastAPI dashboard + APIs
│   ├── templates/          # index, dashboard, folder_picker, connect (Jinja)
│   └── static/             # CSS (dark glass UI) + JS
├── gen_scripts/            # token generator
├── tokens/                 # per-user OAuth pickles (gitignored)
├── accounts/               # Service Account JSONs (gitignored)
└── downloads/              # temp downloads
```

---

## Google Cloud Setup

1. **Create Project:** https://console.cloud.google.com/projectcreate
2. **Enable Drive API:** APIs & Services → Library → Search "Google Drive API" → Enable
3. **OAuth Consent:** APIs & Services → OAuth consent screen → External
   - App name: `Telegram Drive Uploader`
   - Scopes: Add `https://www.googleapis.com/auth/drive` and `.../auth/drive.file`
   - Test users: Add your Gmail (required in Testing mode)
4. **Credentials:** APIs & Services → Credentials → Create Credentials → OAuth Client ID → Desktop app → Create
5. Download JSON, rename to `client_secrets.json`, place in bot root
6. Run bot → `/connect` → authorize → paste code ✅

> **Note:** In Testing mode, only test users can authorize. Add your Gmail as Test User, or Publish app (still works, may show "unverified" warning — click Advanced → Go to project).

---

## ❓ Troubleshooting

| Problem | Fix |
|---|---|
| `client_secrets.json not found` | Download from Google Cloud, put in root |
| `Invalid code` | Code expires in ~10 min, generate fresh via `/connect` |
| `Drive not connected` | Run `/connect` first, or check `tokens/` |
| Web Picker empty | Connect Drive first via `/connect` or `/connect?user_id=` |
| Upload fails 429 | Google quota — wait or use Service Accounts |
| Bot not responding | Check `BOT_TOKEN`, `API_ID`, `API_HASH` in `.env` |

---

## 🔒 Security

- Tokens stored per-user, never logged
- Filename sanitized (no path traversal)
- SSRF-safe direct downloads (http/https only)
- Web APIs require `user_id` + valid Drive token
- Add `.env`, `tokens/`, `accounts/`, `*.pickle`, `bot.db` to `.gitignore`

---

## 🚀 Deploy on Render

**Full guide:** [DEPLOY_RENDER.md](./DEPLOY_RENDER.md) — step-by-step with screenshots description.

**Quick:**
1. Push repo to GitHub (without `.env` / secrets)
2. Render → **New → Blueprint** → Connect repo (auto-reads `render.yaml`) OR **New → Web Service** (Python 3, Build: `pip install -r requirements.txt`, Start: `python bot.py`, Health: `/health`)
3. Add env vars: `BOT_TOKEN`, `API_ID`, `API_HASH`, `OWNER_ID`, `ADMIN_IDS`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `WEB_BASE_URL=https://your-service.onrender.com`, `WEB_SECRET`
4. (Recommended) Add **Persistent Disk** (`/opt/render/project/src/persistent`) + `DATA_DIR` env var so tokens/DB survive restarts
5. Deploy → Check `/health` → Test `/start` in Telegram → `/connect` → Upload!

**Render files included:** `render.yaml` (Blueprint), `runtime.txt` (Python 3.11.9), `Procfile`, `Dockerfile` (with healthcheck).

**Keep-alive:** Add [UptimeRobot](https://uptimerobot.com) ping to `/health` every 5 min to prevent free-tier spin-down.

---

## 🧪 Tests

```bash
pip install pytest httpx
python tests/test_bot.py
# or
pytest tests/ -v
```

Tests cover: utils, database (CRUD, ban, queue, automation), config (PORT/DATA_DIR), web endpoints (all pages + APIs), imports, Drive helper.

---

## 📄 License

MIT — Free to use, modify, deploy. Credit appreciated.

Built with ❤️ — Pyrogram + Google Drive API + FastAPI — Advanced v2.0
