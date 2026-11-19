# ZC Drive Uploader — Render deployment guide

This guide deploys the Telegram bot, Upload Studio, admin panel, local Telegram Bot API for large downloads, Google Drive OAuth, and optional log/dump channels. The root of the GitHub repository must contain `render.yaml`, `Dockerfile`, `main.py`, `bot.py`, `requirements.txt`, and the `driveup/` folder.

## 1. Prepare the repository

1. Download and extract `ZC-Drive-Uploader.zip`.
2. Create a GitHub repository and upload the **contents** of the extracted folder—not the parent folder. Confirm `render.yaml`, `Dockerfile`, `main.py`, `bot.py`, `requirements.txt`, and `driveup/` are at the repository root.
3. Do not upload a `.env`, database files, Google credentials, or Telegram secrets.

> If Render says `python: can't open file .../bot.py`, it is using the wrong root directory/repository or an older checkout. This package includes `bot.py` for compatibility; preferably deploy the included Docker Blueprint. For a manual Python service use `pip install -r requirements.txt` as the Build Command and `python main.py` as the Start Command.

## 2. Create the Telegram bot and local API credentials

1. Create the bot with [@BotFather](https://t.me/BotFather). Save its fresh token as `BOT_TOKEN` and its username (without `@`) as `BOT_USERNAME`.
2. Get your numeric Telegram user ID from [@userinfobot](https://t.me/userinfobot); use it for `OWNER_ID`.
3. For large files, create/reuse Telegram **API ID** and **API hash** at <https://my.telegram.org> → **API development tools**. Use these same values in both the local API service and bot service, as shown in Step 4.
4. Make two private Telegram channels if you want upload logs and mirrored files. Add the bot as an administrator with permission to post. Keep their numeric IDs for Step 5.

## 3. Create Google OAuth credentials

1. In [Google Cloud Console](https://console.cloud.google.com/), select/create a project and enable **Google Drive API**.
2. Configure the OAuth consent screen. While it is in Testing, add the Google account you will test with as a test user.
3. Create an OAuth client of type **Web application**.
4. Add this as an **Authorized JavaScript origin** (domain only):
   `https://YOUR-RENDER-SERVICE.onrender.com`
5. Add this as an **Authorized redirect URI** (full path):
   `https://YOUR-RENDER-SERVICE.onrender.com/auth/google/callback`
6. Save the client ID and secret. The redirect URI must exactly match `GOOGLE_REDIRECT_URI` in Render, and both values must be from this same OAuth client.

## 4. Deploy the Render Blueprint and enter environment values

1. In Render choose **New → Blueprint**, connect the repository, and apply `render.yaml`. It creates two services: the private local Bot API service and the `driveup` web/bot service. They must be in the same region.
2. Enter all `sync: false` values prompted by Render. In the **local Bot API service**, set:
   - `TELEGRAM_API_ID` — from Step 2
   - `TELEGRAM_API_HASH` — matching API hash
   - Keep Blueprint values `TELEGRAM_LOCAL=1` and `FILE_SERVER=1`. Do not add `PORT` in Render’s environment settings; the API image uses its default ports, and Render manages the web service’s `PORT`.
3. In the **driveup bot service**, set these secrets/values:
   - `BOT_TOKEN`, `BOT_USERNAME`, `OWNER_ID`
   - `API_ID` and `API_HASH` — same values as `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`
   - `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`
   - `TOKEN_ENC_KEY` — generate with `python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `SESSION_SECRET` — generate with `python3 -c "import secrets; print(secrets.token_urlsafe(48))"`
   - `PUBLIC_URL=https://YOUR-RENDER-SERVICE.onrender.com`
   - `GOOGLE_REDIRECT_URI=https://YOUR-RENDER-SERVICE.onrender.com/auth/google/callback`
   - Set a strong `ADMIN_PASSWORD` in Render’s secret settings for the extra admin gate.
4. The Blueprint supplies `TELEGRAM_API_URL` and `TELEGRAM_FILES_URL` using `fromService`; do not set them to `api.telegram.org`. It also supplies the app port, database paths, and upload settings.
5. Add `SUPPORT_CONTACT=@ZCQXx` and `BRAND_NAME=ZC Drive Uploader` if they are not already configured.
6. Wait for both services to deploy and for the bot service health check to pass.

### If your local API service was renamed or created manually

The `fromService.name` entries in `render.yaml` must exactly match the current service name. Alternatively, open the local API service’s **Connect** panel and copy its private hostname. On the **bot service**, set both `TELEGRAM_API_URL` and `TELEGRAM_FILES_URL` to that private hostname with port `8081`, e.g. `http://PRIVATE-HOST:8081`. The app maps the file-download connection to the image’s file server port `8080`. Do not use the public `api.telegram.org` URL.

## 5. Configure log and dump channels

On the **driveup bot service**, set:

- `LOG_CHANNEL_ID` to the private channel for events and upload success/failure records.
- `DUMP_CHANNEL_ID` to the private channel where successfully uploaded Telegram messages should be copied.

These can also be set from the website Admin → Settings. The bot needs post permission in each channel. A dump mirrors the original Telegram media/caption and adds a metadata record. Link-based uploads get an audit entry, but the bot cannot mirror remote files as Telegram media. Disclose the dump behavior to users and restrict channel membership.

## 6. Verify large-file mode and limits

1. Message the bot `/system` as the owner.
2. Confirm it reports **Bot API: self-hosted (local)** and **per-file cap: 2000 MB**. If it reports Telegram cloud / 20 MB, `TELEGRAM_API_URL` is empty or wrong; API ID/hash alone do not enable local mode.
3. If `/system` reports local but a user still sees a 20 MB plan limit, open website Admin → **Plans** and raise that plan’s max file size to `2000 MB`. Plans created before local API was enabled can retain the old 20 MB cap.
4. Test a small file first, then a 44–50 MB file. Check both channels if logging/dump are enabled.

## 7. Use the website without opening the bot

1. Open the service root URL (`https://YOUR-RENDER-SERVICE.onrender.com/`) and choose **Sign in with Google**. The browser goes through the same Google OAuth callback configured in Step 3; after consent it returns directly to `/studio` with a secure web session.
2. The user can upload files from the browser, paste supported links, choose Drive folders, adjust supported naming/order settings, watch progress, and review their history/stats without starting a Telegram chat. Google Drive quota, deployment/API ceilings, and the user’s plan still apply.
3. This login does not ask for a separate website password. Google OAuth authorizes the Google account for Drive access. `/connect` also starts this direct web flow.
4. The Telegram bot remains optional for website-originated uploads. Telegram-message intake, bot notifications, and bot-only commands still inherently use Telegram. The Admin panel keeps its existing protected admin sign-in flow; `/admin` remains permission-checked.
5. If Google shows `redirect_uri_mismatch`, ensure the exact callback URI from Step 3 is under **Authorized redirect URIs** for the same client ID configured in Render, then redeploy.
6. The owner can still use `/admin` in Telegram to open the existing protected admin website; the additional `ADMIN_PASSWORD` unlock is required when configured.

## Troubleshooting

- **Still 20 MB:** `/system` says cloud. Fix `TELEGRAM_API_URL` and `TELEGRAM_FILES_URL`, ensure the local API service is running, and check exact service reference/region.
- **Local API URL set but downloads fail:** check `FILE_SERVER=1`, private-network access, and that both services share a region. This ZIP includes the hostport-to-file-port mapping for Render’s API 8081 / file 8080 setup.
- **`redirect_uri_mismatch`:** check the exact callback and OAuth client ID/secret pair.
- **`bot.py` missing:** update the repository with this ZIP’s root contents or set Render Root Directory to the folder containing `bot.py`, `main.py`, `requirements.txt`, and `driveup/`.
- **Uploads fail after redeploy:** confirm the persistent disk is mounted and that `TOKEN_ENC_KEY` / `SESSION_SECRET` did not change.

Never commit or post your bot token, API hash, Google client secret, or generated encryption/session keys. The supplied reference archive contained credential-looking values; use a freshly regenerated bot token and your own secrets only. Review privacy/terms text for your own operation before launch.
