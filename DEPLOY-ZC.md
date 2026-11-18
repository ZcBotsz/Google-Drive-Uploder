# ZC Drive Uploader — Step-by-step deployment

This is the quick-start guide for the supplied project ZIP. The full reference guide is in `docs/DEPLOY.md`.

## 1. Prepare the project
1. Download and extract `ZC-Drive-Uploader.zip`.
2. Create a private GitHub repository and upload the **contents** of the extracted folder (so `Dockerfile`, `render.yaml`, `requirements.txt` and `driveup/` are at the repository root).
3. Never upload `.env`, database files, OAuth credentials, or Telegram tokens.

## 2. Create credentials
1. In Telegram, speak to [@BotFather](https://t.me/BotFather), run `/newbot`, and save the token as `BOT_TOKEN`. Use the bot name **ZC Drive Uploader** if you like.
2. Get your numeric Telegram ID from [@userinfobot](https://t.me/userinfobot); save as `OWNER_ID`.
3. In [Google Cloud Console](https://console.cloud.google.com/), create a project, enable **Google Drive API**, configure the OAuth consent screen and add your Gmail as a test user.
4. Create an OAuth **Web application** client. Add the deployed callback URL exactly as `https://YOUR-DOMAIN/auth/google/callback`. Save its client ID and secret.
5. Generate keys locally (never share them):
   ```bash
   python3 -c "import secrets; print(secrets.token_urlsafe(48))"
   python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```
   Use these for `SESSION_SECRET` and `TOKEN_ENC_KEY`, respectively.

## 3. Deploy on Render (the included blueprint)
1. In Render, choose **New → Blueprint**, connect the GitHub repository, and apply `render.yaml` (recommended; it uses the included Dockerfile). Ensure the repository root contains `render.yaml`, `Dockerfile`, `main.py`, `bot.py`, `requirements.txt`, and `driveup/`—not just a nested folder.
2. Render creates the bot and local Telegram Bot API services. Complete every `sync: false` field in the Render dashboard. If you already created a manual Python web service, set **Root Directory** to the project root, **Build Command** to `pip install -r requirements.txt`, and **Start Command** to `python main.py`. A `bot.py` compatibility launcher is also included for older services whose start command is already `python bot.py`.
3. Set the bot service secrets/config: `BOT_TOKEN`, `OWNER_ID`, `BOT_USERNAME`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TOKEN_ENC_KEY`, `SESSION_SECRET`, `PUBLIC_URL` (your HTTPS service URL), and `GOOGLE_REDIRECT_URI` (`https://YOUR-DOMAIN/auth/google/callback`). Also set `TELEGRAM_API_ID` and `TELEGRAM_API_HASH` on the API service, and matching `API_ID`/`API_HASH` on the bot service; get these from https://my.telegram.org.
4. Set a unique, strong `ADMIN_PASSWORD` in the platform's secret settings (never in Git) to enable the admin panel's extra password gate. Admin access still requires your authorized Telegram account; the unlock expires after 10 minutes.
5. Set `SUPPORT_CONTACT=@ZCQXx` and `BRAND_NAME=ZC Drive Uploader` if not already populated. Configure any remaining required variables from `.env.example`.
6. Deploy and wait for `/health` to report healthy. The Render services must use the same region as specified in `render.yaml`.
6. In Google OAuth settings, confirm the redirect URL exactly matches. For public use, review Google verification requirements; while in Testing, only listed test accounts can connect.
7. Open the bot, run `/start`, then `/connect` and authorize Drive. Test a small file first, then a larger upload.

## 8. Alternative: Railway
Use the included Dockerfile and `railway.json` from the repository root. Create a persistent volume mounted at `/app/data`; provide the same secrets and public HTTPS callback URL. If using Telegram's local Bot API for files over 20 MB, deploy/configure that service separately and set the API variables as described in `docs/DEPLOY.md`.

## 5. Log and dump channels
1. Create two private Telegram channels, one for operations logs and one for the file dump.
2. Add the bot as administrator to both with permission to post messages.
3. Put their numeric IDs in Render environment variables `LOG_CHANNEL_ID` and `DUMP_CHANNEL_ID` on the **driveup bot service** (not the local Bot API service). These values are optional; blank disables that channel.
4. After deployment, upload a small test file and confirm a record appears in the log channel and the original media/caption plus metadata appear in the dump channel.

The dump channel contains copies of user-submitted media. Disclose this in your privacy notice and restrict channel membership. Link-based uploads cannot be mirrored as actual media; a log record is posted instead.

## 6. Admin and support
Set `OWNER_ID` to your Telegram numeric ID. Open the admin route documented in `docs/DEPLOY.md` and sign in as the owner. Support defaults to **@ZCQXx**. Keep the encryption/session keys safe and back up the persistent `/app/data` volume securely.

## 7. Render environment checklist
Enter these through Render → `driveup` service → Environment. Never commit the values to Git.

**Required secrets / deployment-specific values:** `BOT_TOKEN`, `OWNER_ID`, `BOT_USERNAME`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `TOKEN_ENC_KEY`, `SESSION_SECRET`, `PUBLIC_URL`, `GOOGLE_REDIRECT_URI`, `API_ID`, `API_HASH`, `ADMIN_PASSWORD`. For the local Bot API service: `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`.

**Optional but requested:** `LOG_CHANNEL_ID`, `DUMP_CHANNEL_ID`.

The blueprint supplies the remaining runtime settings, including `HOST`, `PORT`, `DB_PATH`, `TEMP_DIR`, `DOWNLOAD_DIR`, `TELEGRAM_API_URL`, `TELEGRAM_FILES_URL`, `TELEGRAM_WORK_DIR`, `DRIVE_SCOPE_MODE`, `UPLOAD_MODE`, `UPLOAD_CHUNK_MB`, `MAX_GLOBAL_UPLOADS`, `MAX_USER_UPLOADS`, and `PUBLIC_STUDIO`. Compare against `.env.example` for the full list. Use the same Telegram API ID/hash across the API service and bot service. Do not paste any old token found in the second supplied archive: regenerate it using BotFather first.

## 8. Troubleshooting
- **OAuth redirect mismatch:** make the Google authorized redirect URI exactly `https://YOUR-DOMAIN/auth/google/callback` and match `GOOGLE_REDIRECT_URI`.
- **Studio does not authenticate:** open it via `/studio` in Telegram; the Mini App relies on Telegram's signed initialization data.
- **Uploads fail after redeploy:** confirm persistent storage is attached and keys have not changed.
- **Files above 20 MB fail:** check that the local Bot API service is deployed and all API ID/hash/URL variables match; Telegram's cloud API has a 20 MB download limit.

Do not publish secrets in GitHub or send them in chat. Actual production connectivity cannot be tested until you add your own credentials and public domain. Review the included privacy/terms language for your own operation and jurisdiction before launch.
