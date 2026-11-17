# 🚀 Deploy to Render — Step-by-Step Guide

Deploy this **Advanced Telegram → Google Drive Uploader** on **Render** (free tier works!). This guide covers **everything** — no guesswork.

**Time needed:** ~15 minutes
**Cost:** Free (Render free web service + optional $1/mo disk for persistence)

---

## 📋 What You'll Need

| # | Item | Where to Get It | Cost |
|---|---|---|---|
| 1 | Telegram Bot Token | [@BotFather](https://t.me/BotFather) | Free |
| 2 | Telegram API ID + Hash | [my.telegram.org/apps](https://my.telegram.org/apps) | Free |
| 3 | Google OAuth Client ID + Secret | [Google Cloud Console](https://console.cloud.google.com/) | Free |
| 4 | Render Account | [render.com](https://render.com) | Free |
| 5 | GitHub Account (to push code) | [github.com](https://github.com) | Free |
| 6 | Your Telegram User ID | [@userinfobot](https://t.me/userinfobot) | Free |

---

## Step 1: Create Telegram Bot (2 min)

1. Open Telegram → search **@BotFather** → Start
2. Send `/newbot` → Choose name (e.g. `My Drive Uploader`) → Username (e.g. `mydriveuploader_bot`)
3. **Copy the BOT_TOKEN** — looks like `123456789:AAE...` — **save it!**
4. Open [my.telegram.org/apps](https://my.telegram.org/apps) → Login with phone → Fill app title/platform → Create
5. **Copy API_ID** (numbers) and **API_HASH** (letters+numbers) — **save both!**
6. Open [@userinfobot](https://t.me/userinfobot) → Start → **Copy your User ID** (numbers) — this is your `OWNER_ID`

> ⚠️ **Never share BOT_TOKEN/API_HASH publicly.** Only put them in Render env vars (secret).

---

## Step 2: Create Google OAuth Client (5 min)

This is the most important step — bot needs Google permission to upload to users' Drives.

### 2.1 Create Project
1. Go to [Google Cloud Console](https://console.cloud.google.com/) → Sign in with Gmail
2. Top bar → Project dropdown → **New Project** → Name: `Telegram Drive Uploader` → Create

### 2.2 Enable Drive API
1. Left menu → **APIs & Services → Library**
2. Search **"Google Drive API"** → Click → **Enable**

### 2.3 Configure OAuth Consent Screen
1. Left menu → **APIs & Services → OAuth consent screen**
2. User Type: **External** → Create
3. Fill:
   - App name: `Telegram Drive Uploader`
   - User support email: your Gmail
   - Developer contact: your Gmail
4. **Scopes** → **Add or Remove Scopes** → Check:
   - `.../auth/drive` (Full access)
   - `.../auth/drive.file` (Per-file access)
   → Update → Save
5. **Test Users** → **Add Users** → Add **your Gmail** (required in Testing mode!) → Save
6. Back to Dashboard → **Publish App** (optional — if you publish, users see "unverified" warning but can still click Advanced → Go to project. If you stay in Testing, only test users can connect.)

### 2.4 Create OAuth Client ID
1. Left menu → **APIs & Services → Credentials**
2. **Create Credentials → OAuth Client ID**
3. Application type: **Desktop app** → Name: `Telegram Bot Desktop` → Create
4. **Copy Client ID** (`xxx.apps.googleusercontent.com`) and **Client Secret** — **save both!**
5. (Optional) Download JSON — you won't need the file on Render if you use env vars (Step 4)

> 💡 **Why Desktop app?** Bot uses InstalledAppFlow with `http://localhost` redirect — user copies code and pastes in Telegram/Web. No domain verification needed. Perfect for bots.

---

## Step 3: Push Code to GitHub (3 min)

### Option A: Upload via GitHub Web (easiest, no git needed)
1. Go to [github.com](https://github.com) → **+ → New repository** → Name: `telegram-drive-uploader` → Public → Create
2. Click **"uploading an existing file"** → Drag **all files** from `telegram-drive-uploader/` folder (except `.env`, `client_secrets.json`, `tokens/`, `bot.db`)
3. Commit → Done

### Option B: Git CLI
```bash
cd telegram-drive-uploader
git init
git add .
git commit -m "Telegram Drive Uploader Bot"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/telegram-drive-uploader.git
git push -u origin main
```

> ⚠️ **Do NOT push:** `.env`, `client_secrets.json`, `token.pickle`, `tokens/*.pickle`, `bot.db` — they're in `.gitignore` already. Secrets go in Render env vars only.

---

## Step 4: Create Render Web Service (5 min)

### 4.1 Create Service
1. Go to [Render Dashboard](https://dashboard.render.com/) → **New → Web Service**
2. Connect your GitHub repo (`telegram-drive-uploader`) → Select branch `main`
3. Fill:
   - **Name:** `telegram-drive-uploader` (becomes `https://telegram-drive-uploader.onrender.com`)
   - **Region:** Singapore (closest to India) or Oregon
   - **Runtime:** **Python 3** (auto-detects `runtime.txt` / `render.yaml`)
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python bot.py`
   - **Health Check Path:** `/health`
   - **Instance Type:** **Free** (to start; upgrade to Starter $7/mo for always-on)

> 💡 **Blueprint method (recommended):** If you pushed `render.yaml`, choose **New → Blueprint** instead — Render auto-creates service with all settings. Then just add env vars in Step 4.2.

### 4.2 Add Environment Variables (CRITICAL!)
In Render → your service → **Environment** → **Add Environment Variable**:

| Key | Value | Notes |
|---|---|---|
| `BOT_TOKEN` | *(from Step 1)* | Secret |
| `API_ID` | *(from Step 1)* | |
| `API_HASH` | *(from Step 1)* | Secret |
| `OWNER_ID` | *(your Telegram ID)* | Admin |
| `ADMIN_IDS` | *(your ID, comma-separated)* | e.g. `123456789` |
| `GOOGLE_CLIENT_ID` | *(from Step 2.4)* | |
| `GOOGLE_CLIENT_SECRET` | *(from Step 2.4)* | Secret |
| `WEB_BASE_URL` | `https://telegram-drive-uploader.onrender.com` | **Replace with YOUR Render URL!** |
| `WEB_SECRET` | *(random 32 chars)* | Generate: `openssl rand -hex 16` |
| `PYTHON_VERSION` | `3.11.9` | |
| `PYTHONUNBUFFERED` | `1` | For live logs |

**Optional:**
| `USE_SERVICE_ACCOUNTS` | `False` | |
| `AUTO_SHARE` | `True` | |
| `MAX_CONCURRENT_UPLOADS` | `2` | |
| `KEEP_ALIVE` | `True` | |

> ⚠️ **WEB_BASE_URL is critical!** After first deploy, copy your Render URL (e.g. `https://telegram-drive-uploader.onrender.com`) and set it here, then **Manual Deploy → Deploy latest commit**. Bot uses this for Web Dashboard links in Telegram.

### 4.3 (Recommended) Add Persistent Disk
Without disk, **tokens + DB reset on every restart** (users must reconnect Drive). With disk, data survives.

1. Render → your service → **Settings → Disk** → **Add Disk**
   - Name: `bot-data`
   - Mount Path: `/opt/render/project/src/persistent`
   - Size: **1 GB** (free tier includes 1 disk)
2. Add env var: `DATA_DIR` = `/opt/render/project/src/persistent`
3. Redeploy

> 💡 Bot code auto-detects this path via `Config.DATA_DIR` — DB, tokens, downloads go there and persist!

---

## Step 5: Deploy & Verify (2 min)

1. Click **Create Web Service** → Render builds (2-3 min) → Starts
2. Go to **Logs** — you should see:
   ```
   [OK] Config loaded. WEB_PORT=10000 DATA_DIR=. WEB_BASE_URL=https://...
   🌐 Starting Web Dashboard on 0.0.0.0:10000
   🚀 Starting Telegram Drive Uploader Bot...
   ✅ Bot is running! Send /start in Telegram
   ✅ Bot commands set
   ```
3. Open `https://your-service.onrender.com/health` → Should show:
   ```json
   {"status": "ok", "bot": "telegram-drive-uploader", "version": "2.0.0-advanced", ...}
   ```
4. Open `https://your-service.onrender.com/` → Beautiful landing page ✅
5. Open Telegram → your bot → `/start` → Should show welcome with buttons ✅

> ❌ **If bot doesn't respond:** Check Logs for `Missing config` — you forgot an env var. Add it → Manual Deploy.

---

## Step 6: Connect Google Drive & Test (3 min)

1. In Telegram, send `/start` to bot → Tap **🔗 Connect Drive**
2. Tap **🔐 Authorize Google Drive** → Sign in with Gmail → **Allow** → Copy code
3. Paste code in Telegram → Bot replies **✅ Connected Successfully!**
4. Tap **📂 Choose Folder** → Web Picker opens → Browse folders → **✅ Use This Folder**
5. Send any file in Telegram → Watch `⬇️ Downloading...` → `📤 Uploading...` → `✅ Uploaded!` with Drive link
6. Send a direct link (e.g. `https://example.com/file.pdf`) → Bot downloads & uploads
7. Send a Drive link → Bot clones it
8. Open Web Dashboard: `https://your-service.onrender.com/dashboard?user_id=YOUR_ID` → See stats + recent uploads

🎉 **You're live!**

---

## Step 7: Keep Bot Alive (Free Tier)

Render **free tier spins down after 15 min** of no traffic. Bot will sleep and wake on next Telegram message (takes ~30s to wake). To keep it always-on:

### Option A: UptimeRobot (Free, Recommended)
1. Go to [uptimerobot.com](https://uptimerobot.com) → Free account
2. **Add New Monitor** → Type: **HTTP(s)** → URL: `https://your-service.onrender.com/health` → Interval: **5 min**
3. Save — UptimeRobot pings every 5 min, Render never sleeps!

### Option B: Upgrade Render
Starter plan ($7/mo) → Always-on, no spin-down, faster.

### Option C: Self-ping (Built-in, partial)
Bot has `KEEP_ALIVE=True` self-pinger, but it only works when bot is awake. Use UptimeRobot for reliability.

---

## 🔧 Troubleshooting

| Problem | Cause | Fix |
|---|---|---|
| Build fails: `tgcrypto` error | Python version mismatch | Set `PYTHON_VERSION=3.11.9` in env vars |
| `Missing config` in logs | Env var not set | Add in Render → Environment → Redeploy |
| Bot starts but no reply in Telegram | Wrong BOT_TOKEN or bot not started | Check Logs for `Bot is running`, verify token with BotFather |
| `/connect` → `client_secrets.json not found` | Using file method on Render | Use `GOOGLE_CLIENT_ID`/`SECRET` env vars instead (Step 4.2) |
| Web Picker shows "Not connected" | User hasn't run `/connect` yet | Connect via Telegram or `/connect?user_id=ID` web page |
| Users must reconnect after restart | No Persistent Disk | Add Disk (Step 4.3) + `DATA_DIR` env var |
| Bot sleeps / slow first reply | Render free spin-down | Add UptimeRobot ping (Step 7) |
| `429 userRateLimitExceeded` | Google quota hit | Wait 1 hour, or add Service Accounts (`accounts/*.json`) |
| Health check fails | Web server not on `$PORT` | Bot reads `PORT` automatically — check Logs for `WEB_PORT=10000` |
| `Invalid code` on connect | Code expired (>10 min) | Run `/connect` again, paste fresh code quickly |

---

## 🔄 Updating Bot

1. Edit code locally → Push to GitHub (`git push`)
2. Render → **Manual Deploy → Deploy latest commit** (or auto-deploy if enabled)
3. Check Logs → `/health` → Test in Telegram

---

## 📊 Monitoring

- **Render Logs:** Dashboard → Logs (live)
- **Health:** `https://your-service.onrender.com/health`
- **Admin:** Telegram `/admin` or Web `/admin?user_id=YOUR_ID`
- **Stats:** Telegram `/status` or Web `/dashboard?user_id=YOUR_ID`

---

## 🔒 Security Checklist

- [ ] BOT_TOKEN/API_HASH only in Render env vars (never in GitHub)
- [ ] `client_secrets.json` NOT in GitHub (use env vars on Render)
- [ ] `WEB_SECRET` is random (not default)
- [ ] OWNER_ID/ADMIN_IDS set correctly
- [ ] Google OAuth Test Users includes your Gmail
- [ ] Persistent Disk enabled (so tokens persist securely)

---

## ❓ FAQ

**Q: Can I use free tier?**
A: Yes! Free web service + free disk works. Add UptimeRobot to prevent sleep.

**Q: Do I need client_secrets.json on Render?**
A: No — use `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` env vars. Bot builds OAuth flow from them.

**Q: Why do users need to connect their own Drive?**
A: Each user uploads to **their own** Google Drive (per-user OAuth). Owner token mode is also supported for single-user bots.

**Q: Can bot upload >2GB files?**
A: Telegram bot API limit is 2GB per file. Larger files need user session (premium) — not included by default.

**Q: How to add Service Accounts for unlimited uploads?**
A: Create SA in Google Cloud → Download JSON → Upload to `accounts/` via Render Shell or GitHub (private repo) → Set `USE_SERVICE_ACCOUNTS=True`. Bot auto-switches on quota.

---

**Need help?** Check Render Logs first — 90% of issues show there. Then check `/health` endpoint.
