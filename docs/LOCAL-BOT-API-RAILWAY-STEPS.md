# Local Bot API on Railway — step by step

> 📌 **Isse pehle ye padho:** `LOCAL-API-RAILWAY-STEP-BY-STEP.md`
> Wahan hi saaf likha hai ki Railway me service ka naam **fix karna zaroori nahi**
> jab `${{...}}` reference variables use karo, aur port reference
> (`${{service.PORT}}`) **kaam nahi karta** — port haath se likhna padta hai.
> Neeche wala guide naam hard-code karta hai, jo bhi chalega — par naya wala
> kam galat hone wala rasta hai.


Follow in order. Every value you need to type is in a grey box — copy it exactly.

**Time:** ~20 minutes. **You can do all of this from your phone.**

---

## ⚠️ Read this before you start (2 minutes)

**What this buys you:** files up to 2000 MB instead of 20 MB.

**What it costs:** one extra Railway service + disk space, plus a few new ways
for the bot to break.

**The one thing that makes this hard on Railway:** a Railway volume can be
attached to **only one service**. Your Bot API server and your bot are two
separate services, so the bot **cannot read the server's disk**. That is why we
will run the server's **file server** (`FILE_SERVER=1`), which exposes the same
files over HTTP so your bot can fetch them. This is not optional — without it
every download returns 404.

**Do not skip Part 1.** Your old `API_HASH` was posted in a chat; it must be
replaced.

---

# Part 1 · Clean up the old credentials

### 1.1 Log the bot out of Telegram's cloud API

A bot token cannot work on the cloud API and a local server **at the same time**.
Open this in your phone browser, replacing `<TOKEN>` with your real bot token:

```
https://api.telegram.org/bot<TOKEN>/logOut
```

✅ You must see: `{"ok":true,"result":true}`

> Cloud login is blocked for ~10 minutes afterwards. That is normal — finish
> Parts 2–5 first, then redeploy.

### 1.2 Create a new API pair

1. Open **https://my.telegram.org** in your phone browser
2. Log in with **your own phone number** (not the bot)
3. Tap **API development tools**
4. If an app already exists, **delete it** — that kills the leaked hash
5. Fill in: **App title** → `DriveUp`, **Short name** → `driveup`, **Platform** → *Other*
6. Tap **Create application**
7. Copy the **api_id** (a number) and **api_hash** (a long string) somewhere private

❌ Do not paste these into any chat again.

---

# Part 2 · Create the Bot API server service

### 2.1 Add the service

In Railway, open **your existing DriveUp project** (the one with the bot):

1. Tap **+ New** (top right)
2. Choose **Docker Image**
3. In the image field type exactly:

```
ghcr.io/gramiojs/telegram-bot-api:latest
```

4. Tap **Deploy**

### 2.2 Rename it — this matters

The internal hostname is built from the service name, so it must be exact.

1. Open the new service
2. Tap the name at the top → rename to:

```
tg-bot-api
```

3. Confirm

> ⚠️ If the name is wrong, `tg-bot-api.railway.internal` will not resolve and
> your bot will fail to start. This is the single most common mistake.

### 2.3 Why this image

Use `gramiojs/telegram-bot-api`, **not** `aiogram/telegram-bot-api`.

The aiogram image is stale and its `/file/` route is reported broken. The
gramiojs one is rebuilt from upstream, runs non-root, and — the part that
matters here — can serve files over HTTP for us.

---

# Part 3 · Add the variables

Open your **tg-bot-api** service → **Variables** tab → **+ New Variable**.

Add each of these exactly (name on the left, value on the right):

```
TELEGRAM_API_ID      12345678      ← your NEW api_id
TELEGRAM_API_HASH    0123456789...     ← your NEW api_hash
TELEGRAM_LOCAL       1
FILE_SERVER          1
```

| Variable | What it does |
|---|---|
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` | login for the server. Without them it will not start |
| `TELEGRAM_LOCAL` | enables local mode → lifts the 20 MB limit |
| `FILE_SERVER` | **the critical one** — runs nginx inside the container on port 8080 serving the files |

Leave the ports alone; they default to 8081 (API) and 8080 (files).

Then **Deploy** / **Redeploy** the service.

### 3.1 Check it started

Open the service → **Deploy Logs**. You want to see the server booting with no
`api_id` / `api_hash` errors. If it crashes immediately with an auth message,
one of the two variables is wrong or empty.

---

# Part 4 · Disk — choose one, read the trade-off

Telegram files land in `/var/lib/telegram-bot-api`. **The server never deletes
them**, and your bot container cannot reach that disk to clean up (Railway
volumes are one-service-only). So pick your poison:

### Option A · Volume (files survive redeploys — recommended)

1. tg-bot-api service → **Settings** → **Volumes** → **+ Add Volume**
2. Mount path, exactly:

```
/var/lib/telegram-bot-api
```

3. Size: start at **5 GB**

✅ Files survive restarts. ❌ The volume **fills up over time** and needs manual
attention — see Part 8.

### Option B · No volume (self-cleaning, but files vanish on redeploy)

Skip the volume entirely. Files live on the container's temporary disk and are
wiped every time the service restarts or redeploys.

✅ Never fills permanently — just redeploy to clear it.
❌ If Railway restarts the container mid-upload, that upload fails.

> **Honest note:** neither option gives automatic cleanup, because the bot
> cannot delete files on a disk it cannot see. Option A with an occasional
> redeploy-plus-volume-reset is the most predictable.

---

# Part 5 · Point your bot at the server

Open your **bot** service (the DriveUp one) → **Variables**. Add:

```
API_ID              12345678
API_HASH            0123456789...
TELEGRAM_API_URL    http://tg-bot-api.railway.internal:8081
TELEGRAM_FILES_URL  http://tg-bot-api.railway.internal:8080
```

| Variable | Why |
|---|---|
| `TELEGRAM_API_URL` | port **8081** — where `getMe`, `getFile` go |
| `TELEGRAM_FILES_URL` | port **8080** — where the actual file bytes come from |
| `API_ID` / `API_HASH` | DriveUp only checks these exist; the *server* uses them |

> 📌 **8081 vs 8080 is the thing to get right.** 8081 = the API. 8080 = the
> files. Swapping them gives 404s on every download.

Now **Redeploy the bot**.

---

# Part 6 · Verify — do not skip

### 6.1 Logs look right

Bot service → **Deploy Logs**. Expect:

```
driveup · config OK
web listening on 0.0.0.0:8080
bot @Ricxyuploaderbot online
```

If it exits with **"Configuration is not usable"**, read the message — it names
the exact variable that is wrong:

| Message says | Fix |
|---|---|
| `API_ID/API_HASH are missing` | add both to the **bot** service (Part 5) |
| `cannot see its files … TELEGRAM_FILES_URL is empty` | add `TELEGRAM_FILES_URL` (Part 5) and `FILE_SERVER=1` (Part 3) |

### 6.2 The limit is gone in the admin panel

1. Open `https://drivegram-bot-production.up.railway.app/admin`
2. **Settings** → the amber *"files over 20 MB cannot be downloaded"* banner must be **gone**
3. **Plans** → the `≤20 real` badge must be **gone**; limits read `2000 MB`

### 6.3 The real test

Send yourself a **30 MB** file to the bot. It could not work before. It should
now download and upload to Drive.

If it fails, the error will tell you which half is broken:

| Error | Meaning |
|---|---|
| `404` on a `…:8080/…` URL | `FILE_SERVER=1` missing, or wrong port |
| `404` on a `…:8081/file/…` URL | `TELEGRAM_FILES_URL` not set |
| connection refused / DNS | service not named exactly `tg-bot-api` |
| `file is too big` | still on the cloud API — `TELEGRAM_API_URL` not applied |

---

# Part 7 · If it goes wrong, roll back in 60 seconds

Open the **bot** service → Variables → delete these four:

```
API_ID
API_HASH
TELEGRAM_API_URL
TELEGRAM_FILES_URL
```

Redeploy. You are back on the cloud API with the 20 MB limit, exactly as today.
Then call `logOut` once more if you want the bot to re-register on the cloud:

```
https://api.telegram.org/bot<TOKEN>/logOut
```

Nothing else in your project is affected. The tg-bot-api service can be deleted
whenever you like.

---

# Part 8 · Living with it (the honest part)

**The disk fills.** With a 2 GB ceiling, one busy day can eat a 5 GB volume.
When the volume is full, uploads fail. Watch it here:

```
tg-bot-api service → Settings → Volumes → usage
```

When it gets high, the only remedy is to reset the volume (deletes the cache;
harmless, since files are already in Google Drive by then).

**Deploys get slower.** Every redeploy of the bot now also depends on the
tg-bot-api service being healthy.

**Keep 1 replica on both services.** Railway does not allow replicas on a
service with a volume anyway.

---

# Part 9 · Full variable summary

**tg-bot-api service**

```
TELEGRAM_API_ID    <new api_id>
TELEGRAM_API_HASH  <new api_hash>
TELEGRAM_LOCAL     1
FILE_SERVER        1
```

**bot service (additions only — keep everything else you already have)**

```
API_ID             <new api_id>
API_HASH           <new api_hash>
TELEGRAM_API_URL   http://tg-bot-api.railway.internal:8081
TELEGRAM_FILES_URL http://tg-bot-api.railway.internal:8080
```

That is the whole configuration. Two services, four variables each.
