"""DriveUp configuration — every env var in one place, validated at boot.

Rules (B14/Security):
  * names here are the ONLY names that exist; .env.example, README and the
    deployment files use the same ones (the zip had three mismatched sets).
  * a missing required value raises ConfigError with a message that says what
    to do — never a bare KeyError.
  * no secret is ever logged; `mask()` is used everywhere we print one.
"""
from __future__ import annotations

import base64
import hashlib
import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()          # .env in the CWD, if any — no-op when missing

BRAND_NAME = os.getenv("BRAND_NAME", "ZC Drive Uploader")

_TRUE = {"1", "true", "yes", "on", "y"}
_CALLBACK_PATH = "/auth/google/callback"


def _clean_base(raw: str) -> str:
    """`PUBLIC_URL` is used to build absolute links, so it needs a scheme.

    Railway shows the domain as `myapp.up.railway.app` (no scheme), and pasting
    that verbatim yields `myapp.up.railway.app/?job=…` — which Telegram rejects
    and browsers treat as a relative path.
    """
    url = (raw or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def _clean_redirect(raw: str) -> str:
    """Normalise a redirect URI the way Google compares it.

    Trailing slashes, surrounding whitespace and a pasted `?code=…` tail are
    the three ways this value goes wrong in real life, and all three produce
    the same opaque `Error 400: redirect_uri_mismatch`.
    """
    uri = (raw or "").strip().split("?")[0].split("#")[0]
    return uri.rstrip("/")


class ConfigError(RuntimeError):
    """Boot-time configuration problem, phrased so a human can fix it."""


def _s(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return default if v is None else v.strip()


def _i(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a whole number") from None


def _f(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw.strip())
    except ValueError:
        raise ConfigError(f"{name}={raw!r} is not a number") from None


def _b(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in _TRUE


def _list(name: str) -> List[str]:
    raw = _s(name)
    return [p.strip() for p in raw.replace("\n", ",").split(",") if p.strip()]


def mask(secret: str, keep: int = 4) -> str:
    """Show enough of a secret to recognise it, never enough to use it."""
    if not secret:
        return "(empty)"
    if len(secret) <= keep * 2:
        return "*" * len(secret)
    return f"{secret[:keep]}…{secret[-keep:]}"


@dataclass
class Settings:
    # ── Telegram ──────────────────────────────────────────────────────────
    bot_token: str = field(default_factory=lambda: _s("BOT_TOKEN") or _s("TELEGRAM_BOT_TOKEN"))
    api_id: int = field(default_factory=lambda: _i("API_ID", 0) or _i("TELEGRAM_API_ID", 0))
    api_hash: str = field(default_factory=lambda: _s("API_HASH") or _s("TELEGRAM_API_HASH"))
    telegram_api_url: str = field(default_factory=lambda: _s("TELEGRAM_API_URL") or "https://api.telegram.org")
    # Only needed when the Bot API server's disk is NOT readable by this
    # container (e.g. two Railway services). Points at the image's bundled
    # nginx (FILE_SERVER=1), which serves the same directory over HTTP at
    # token-less, path-based URLs.
    telegram_files_url: str = field(default_factory=lambda: _s("TELEGRAM_FILES_URL"))
    progress_tick: float = field(default_factory=lambda: _f("PROGRESS_TICK", 0.5))
    url_upload_enabled: bool = field(
        default_factory=lambda: _s("URL_UPLOAD_ENABLED", "1") not in ("0", "false", "no"))
    url_upload_max_mb: int = field(
        default_factory=lambda: int(_s("URL_UPLOAD_MAX_MB") or "2000"))
    telegram_work_dir: str = field(default_factory=lambda: _s("TELEGRAM_WORK_DIR")
                                   or "/var/lib/telegram-bot-api")
    owner_id: int = field(default_factory=lambda: _i("OWNER_ID", 0))
    admin_ids: List[int] = field(default_factory=lambda: [int(x) for x in _list("ADMIN_IDS") if x.lstrip("-").isdigit()])

    # ── Web ───────────────────────────────────────────────────────────────
    public_url: str = field(default_factory=lambda: _clean_base(_s("PUBLIC_URL")))
    port: int = field(default_factory=lambda: _i("PORT", 8080))
    host: str = field(default_factory=lambda: _s("HOST", "0.0.0.0"))
    session_secret: str = field(default_factory=lambda: _s("SESSION_SECRET"))
    login_widget_bot_username: str = field(default_factory=lambda: _s("BOT_USERNAME"))

    # ── Storage ───────────────────────────────────────────────────────────
    db_path: str = field(default_factory=lambda: _s("DB_PATH", "data/driveup.sqlite3"))
    temp_dir: str = field(default_factory=lambda: _s("TEMP_DIR", "data/tmp"))
    download_dir: str = field(default_factory=lambda: _s("DOWNLOAD_DIR", "data/downloads"))

    # ── Google ────────────────────────────────────────────────────────────
    google_client_id: str = field(default_factory=lambda: _s("GOOGLE_CLIENT_ID"))
    google_client_secret: str = field(default_factory=lambda: _s("GOOGLE_CLIENT_SECRET"))
    google_redirect_uri: str = field(
        default_factory=lambda: _clean_redirect(_s("GOOGLE_REDIRECT_URI")))
    drive_scope_mode: str = field(default_factory=lambda: (_s("DRIVE_SCOPE_MODE") or "file").lower())
    token_enc_key: str = field(default_factory=lambda: _s("TOKEN_ENC_KEY"))
    picker_api_key: str = field(default_factory=lambda: _s("PICKER_API_KEY"))
    # test hook: point at a fake Google that speaks the real resumable protocol
    google_api_base: str = field(default_factory=lambda: _s("GOOGLE_API_BASE"))

    # ── Channels ──────────────────────────────────────────────────────────
    log_channel_id: str = field(default_factory=lambda: _s("LOG_CHANNEL_ID"))
    lock_channel_id: str = field(default_factory=lambda: _s("LOCK_CHANNEL_ID"))
    dump_channel_id: str = field(default_factory=lambda: _s("DUMP_CHANNEL_ID"))
    force_sub_channels: List[str] = field(default_factory=lambda: _list("FORCE_SUB_CHANNELS"))

    # ── Locks / abuse ─────────────────────────────────────────────────────
    lock_default_minutes: int = field(default_factory=lambda: _i("LOCK_DEFAULT_MINUTES", 60))
    auto_lock_limit: int = field(default_factory=lambda: _i("AUTO_LOCK_LIMIT", 60))
    auto_lock_window: int = field(default_factory=lambda: _i("AUTO_LOCK_WINDOW", 60))
    auto_lock_minutes: int = field(default_factory=lambda: _i("AUTO_LOCK_MINUTES", 15))
    admin_idle_minutes: int = field(default_factory=lambda: _i("ADMIN_IDLE_MINUTES", 10))
    # Optional second factor for admin routes. Keep only in the platform secret store.
    admin_password: str = field(default_factory=lambda: _s("ADMIN_PASSWORD"))

    # ── Uploads ───────────────────────────────────────────────────────────
    max_global_uploads: int = field(default_factory=lambda: _i("MAX_GLOBAL_UPLOADS", 3))
    max_user_uploads: int = field(default_factory=lambda: _i("MAX_USER_UPLOADS", 1))
    upload_chunk_mb: int = field(default_factory=lambda: _i("UPLOAD_CHUNK_MB", 8))
    upload_mode: str = field(default_factory=lambda: (_s("UPLOAD_MODE") or "auto").lower())
    stall_seconds: int = field(default_factory=lambda: _i("STALL_SECONDS", 180))
    download_chunk_kb: int = field(default_factory=lambda: _i("DOWNLOAD_CHUNK_KB", 1024))

    # ── Defaults / quotas ─────────────────────────────────────────────────
    default_max_file_mb: int = field(default_factory=lambda: _i("DEFAULT_MAX_FILE_MB", 2000))
    default_daily_gb: float = field(default_factory=lambda: _f("DEFAULT_DAILY_GB", 20.0))
    drive_daily_quota_gb: float = field(default_factory=lambda: _f("DRIVE_DAILY_QUOTA_GB", 750.0))
    timezone: str = field(default_factory=lambda: _s("DEFAULT_TIMEZONE") or "Asia/Kolkata")
    lang: str = field(default_factory=lambda: (_s("DEFAULT_LANG") or "hinglish").lower())
    support_contact: str = field(default_factory=lambda: _s("SUPPORT_CONTACT") or "@ZCQXx")
    plan_price_text: str = field(default_factory=lambda: _s("PLAN_PRICE_TEXT") or "₹0")

    # ── Features ──────────────────────────────────────────────────────────
    enable_animated_emoji: bool = field(default_factory=lambda: _b("ENABLE_ANIMATED_EMOJI", False))
    progress_style: str = field(default_factory=lambda: (_s("PROGRESS_STYLE") or "glow").lower())
    maintenance: bool = field(default_factory=lambda: _b("MAINTENANCE", False))
    public_studio: bool = field(default_factory=lambda: _b("PUBLIC_STUDIO", True))

    # ── derived ───────────────────────────────────────────────────────────
    @property
    def is_owner(self) -> int:
        return self.owner_id

    @property
    def redirect_uri(self) -> str:
        if self.google_redirect_uri:
            return self.google_redirect_uri
        base = self.public_url or f"http://127.0.0.1:{self.port}"
        return f"{base}/auth/google/callback"

    @property
    def scope_mode_full(self) -> bool:
        return self.drive_scope_mode == "full"

    @property
    def drive_scope(self) -> str:
        if self.scope_mode_full:
            return "https://www.googleapis.com/auth/drive"
        return "https://www.googleapis.com/auth/drive.file"

    @property
    def chunk_bytes(self) -> int:
        """Drive requires a 256 KiB multiple; clamp 256 KiB … 64 MiB."""
        mb = max(1, min(64, self.upload_chunk_mb))
        size = mb * 1024 * 1024
        return size - size % (256 * 1024) if size > 256 * 1024 else 256 * 1024

    # ── validation ────────────────────────────────────────────────────────
    # Test-only: exposes POST /__fixture/job so the browser suite can create a
    # fresh draft. Default off; main.py never sets it.
    e2e_fixtures: bool = False

    @property
    def api_base(self) -> str:
        """TELEGRAM_API_URL with a scheme, always.

        Render's `fromService: hostport` gives a bare `host:port`, and aiogram's
        `TelegramAPIServer.from_base()` accepts that without complaint — then
        every single API call fails, which to a user is exactly "the bot does not
        work". Adding the scheme here makes that class of mistake impossible.
        """
        url = (self.telegram_api_url or "").strip().rstrip("/")
        if not url:
            return "https://api.telegram.org"
        if "://" not in url:
            # an internal host:port is plain HTTP; a public hostname is HTTPS
            scheme = "http" if (":" in url.split("/")[0]) else "https"
            url = f"{scheme}://{url}"
        return url

    @property
    def uses_local_api(self) -> bool:
        """True when TELEGRAM_API_URL points somewhere other than Telegram's cloud.

        This is the switch that lifts the download cap from 20 MB to 2 GB, so it
        is asked in several places — keep the definition in one spot.
        """
        return (self.telegram_api_url or "").rstrip("/") not in (
            "", "https://api.telegram.org")

    def validate(self) -> List[str]:
        """Everything wrong with this configuration, in one list."""
        p: List[str] = []
        if not self.bot_token or ":" not in self.bot_token:
            p.append("BOT_TOKEN is missing or malformed (expected 123456:ABC-DEF… — BotFather → API Token)")
        if not self.google_client_id:
            p.append("GOOGLE_CLIENT_ID is missing (Google Cloud → Credentials → OAuth client → Web application)")
        if not self.google_client_secret:
            p.append("GOOGLE_CLIENT_SECRET is missing (same screen as the client id)")
        if self.drive_scope_mode not in ("file", "full"):
            p.append(f"DRIVE_SCOPE_MODE={self.drive_scope_mode!r} — use 'file' or 'full'")
        if self.upload_mode not in ("auto", "stream", "disk"):
            p.append(f"UPLOAD_MODE={self.upload_mode!r} — use 'auto', 'stream' or 'disk'")
        if not self.token_enc_key:
            p.append("TOKEN_ENC_KEY is missing — generate one: python -c \"from cryptography.fernet import Fernet;"
                     "print(Fernet.generate_key().decode())\"")
        else:
            try:
                self.fernet_key()
            except Exception:
                p.append("TOKEN_ENC_KEY is not a valid Fernet key (32 url-safe base64 bytes)")
        if not self.session_secret:
            p.append("SESSION_SECRET is missing — generate one: python -c \"import secrets;print(secrets.token_urlsafe(32))\"")
        if self.owner_id <= 0:
            p.append("OWNER_ID is missing (your numeric Telegram id — @userinfobot)")
        if self.upload_chunk_mb < 1 or self.upload_chunk_mb > 64:
            p.append(f"UPLOAD_CHUNK_MB={self.upload_chunk_mb} — must be 1…64")
        if self.max_global_uploads < 1:
            p.append("MAX_GLOBAL_UPLOADS must be ≥ 1")
        # OAuth redirect: a wrong value here is Google's opaque
        # "Error 400: redirect_uri_mismatch", so catch it at boot instead.
        uri = self.redirect_uri
        if not uri.startswith(("http://", "https://")):
            p.append(f"PUBLIC_URL/GOOGLE_REDIRECT_URI must be a full URL, got {uri!r}")
        elif self.public_url and not self.public_url.startswith("https://") \
                and "127.0.0.1" not in self.public_url and "localhost" not in self.public_url:
            p.append(f"PUBLIC_URL must start with https:// (got {self.public_url!r}) — "
                     "the Studio button and OAuth both need an absolute https URL")
        elif not uri.endswith(_CALLBACK_PATH):
            p.append(f"GOOGLE_REDIRECT_URI must end with {_CALLBACK_PATH} (got {uri!r}) "
                     "— the same string must be listed verbatim in Google Cloud")
        elif uri.startswith("http://") and "127.0.0.1" not in uri and "localhost" not in uri:
            p.append(f"PUBLIC_URL is http:// but Telegram Mini-Apps and Google both "
                     f"need https:// for a public host (got {uri!r})")
        # A local Bot API server cannot start without the raw API credentials.
        # Set the URL but forget these two and the whole 2 GB path silently dies.
        if self.uses_local_api and not (self.api_id and self.api_hash):
            p.append("TELEGRAM_API_URL points at a local Bot API server, but API_ID/"
                     "API_HASH are missing — get both from https://my.telegram.org "
                     "(API development tools). The server will not start without them.")
        elif self.api_id and not self.api_hash:
            p.append("API_ID is set but API_HASH is empty — set both or neither.")
        elif self.api_hash and not self.api_id:
            p.append("API_HASH is set but API_ID is empty — set both or neither.")
        # A local server in --local mode returns an absolute PATH, so this
        # container must be able to reach those bytes. Either the volume is
        # shared (same path visible here) or the server's file endpoint is set.
        if self.uses_local_api and not self.telegram_files_url \
                and not os.path.isdir(self.telegram_work_dir):
            p.append(
                f"TELEGRAM_API_URL is a local Bot API server, but this container "
                f"cannot see its files: {self.telegram_work_dir!r} does not exist "
                f"and TELEGRAM_FILES_URL is empty. Either mount the server's volume "
                f"at that path, or run the server with FILE_SERVER=1 and set "
                f"TELEGRAM_FILES_URL=http://<server>:8080")
        return p

    def fernet_key(self) -> bytes:
        """A Fernet key from TOKEN_ENC_KEY, tolerating non-base64 secrets.

        People paste random strings, so an arbitrary secret is folded into a
        deterministic 32-byte key with SHA-256 instead of refusing to boot.
        A real Fernet key passes through untouched.
        """
        raw = self.token_enc_key.encode()
        try:
            if len(base64.urlsafe_b64decode(raw)) == 32:
                return raw
        except Exception:
            pass
        return base64.urlsafe_b64encode(hashlib.sha256(raw).digest())

    def secrets_summary(self) -> str:
        return (f"bot_token={mask(self.bot_token)}  google_client_id={mask(self.google_client_id, 12)}  "
                f"fernet={'set' if self.token_enc_key else 'MISSING'}")


settings = Settings()


# ── Telegram's real download ceiling ────────────────────────────────────────
# The cloud Bot API (api.telegram.org) refuses getFile for anything over 20 MB
# ("Bad Request: file is too big"); a self-hosted local Bot API server
# (TELEGRAM_API_URL) lifts that to 2 GB. A plan promising 2000 MB on the cloud
# API therefore cannot be delivered — the bot can never read the bytes, so the
# job dies at 0 % with an error that looks like our bug, not Telegram's.
CLOUD_DOWNLOAD_MB = 20
LOCAL_DOWNLOAD_MB = 2000


def effective_max_file_mb(cfg, *, telegram_api_url: str = "",
                          requested_mb: float = 0) -> int:
    """The largest file this deployment can actually fetch from Telegram.

    Returns the smaller of the plan's allowance and the transport's real limit,
    so `max_file_mb` shown to a user is always achievable.
    """
    url = (telegram_api_url or getattr(cfg, "telegram_api_url", "") or "").strip().rstrip("/")
    local = url not in ("", "https://api.telegram.org")
    ceiling = LOCAL_DOWNLOAD_MB if local else CLOUD_DOWNLOAD_MB
    want = int(requested_mb or getattr(cfg, "default_max_file_mb", 2000) or 0)
    return max(1, min(want, ceiling))
