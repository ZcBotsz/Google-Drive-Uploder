import os
from dotenv import load_dotenv

load_dotenv()

def _get_port():
    # Render sets PORT env var (e.g. 10000). Fallback to WEB_PORT, then 8080.
    try:
        return int(os.getenv("PORT", os.getenv("WEB_PORT", "8080")) or 8080)
    except ValueError:
        return 8080

def _get_data_dir():
    # Render Persistent Disk mounts at /opt/render/project/src/persistent or custom DATA_DIR
    # If DATA_DIR set, use it for DB/tokens/downloads so data survives restarts.
    data_dir = os.getenv("DATA_DIR", "").strip()
    if data_dir:
        return data_dir
    # Render default persistent disk path (if disk configured)
    render_disk = "/opt/render/project/src/persistent"
    if os.path.isdir(render_disk):
        return render_disk
    return "."

class Config:
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    API_ID = int(os.getenv("API_ID", "0") or 0)
    API_HASH = os.getenv("API_HASH", "")
    OWNER_ID = int(os.getenv("OWNER_ID", "0") or 0)
    # Comma-separated admin IDs (OWNER_ID is always admin)
    ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip().isdigit()]
    if OWNER_ID and OWNER_ID not in ADMIN_IDS:
        ADMIN_IDS.append(OWNER_ID)

    CLIENT_SECRETS_FILE = os.getenv("CLIENT_SECRETS_FILE", "client_secrets.json")
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

    USE_SERVICE_ACCOUNTS = os.getenv("USE_SERVICE_ACCOUNTS", "False").lower() == "true"
    GDRIVE_ID = os.getenv("GDRIVE_ID", "").strip()
    IS_TEAM_DRIVE = os.getenv("IS_TEAM_DRIVE", "False").lower() == "true"

    WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0")
    WEB_PORT = _get_port()
    # On Render, set WEB_BASE_URL to https://<your-service>.onrender.com
    WEB_BASE_URL = os.getenv("WEB_BASE_URL", "http://localhost:8080").rstrip("/")
    WEB_SECRET = os.getenv("WEB_SECRET", "change_me_to_random_string")

    # Persistent storage (Render Disk)
    DATA_DIR = _get_data_dir()
    DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", os.path.join(DATA_DIR, "downloads") if DATA_DIR != "." else "downloads")
    TOKEN_DIR = os.getenv("TOKEN_DIR", os.path.join(DATA_DIR, "tokens") if DATA_DIR != "." else "tokens")
    ACCOUNTS_DIR = os.getenv("ACCOUNTS_DIR", os.path.join(DATA_DIR, "accounts") if DATA_DIR != "." else "accounts")
    DB_PATH = os.getenv("DB_PATH", os.path.join(DATA_DIR, "bot.db") if DATA_DIR != "." else "bot.db")

    MAX_CONCURRENT_UPLOADS = int(os.getenv("MAX_CONCURRENT_UPLOADS", "2"))
    AUTO_SHARE = os.getenv("AUTO_SHARE", "True").lower() == "true"
    GD_DESCRIPTION = os.getenv("GD_DESCRIPTION", "Uploaded via Telegram Drive Uploader Bot")

    # Advanced
    MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", "2000"))  # Telegram bot limit ~2GB
    ENABLE_WEBHOOK = os.getenv("ENABLE_WEBHOOK", "False").lower() == "true"
    WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").rstrip("/")  # e.g. https://your-service.onrender.com
    WEBHOOK_PATH = os.getenv("WEBHOOK_PATH", "/webhook")
    KEEP_ALIVE = os.getenv("KEEP_ALIVE", "True").lower() == "true"  # self-ping for Render free tier
    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    OAUTH_SCOPES = [
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/drive.file",
    ]

    @classmethod
    def is_admin(cls, user_id: int) -> bool:
        return user_id in cls.ADMIN_IDS

    @classmethod
    def validate(cls):
        missing = []
        if not cls.BOT_TOKEN:
            missing.append("BOT_TOKEN")
        if not cls.API_ID:
            missing.append("API_ID")
        if not cls.API_HASH:
            missing.append("API_HASH")
        if missing:
            print(f"[WARNING] Missing config: {', '.join(missing)} - Bot will not start until set in Render Environment Variables")
        else:
            print(f"[OK] Config loaded. WEB_PORT={cls.WEB_PORT} DATA_DIR={cls.DATA_DIR} WEB_BASE_URL={cls.WEB_BASE_URL}")
        return len(missing) == 0
