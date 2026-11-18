"""Google endpoint URLs in one place.

Production values are the real Google APIs. `GOOGLE_API_BASE` overrides the
host for the test suite (which runs a fake Drive that speaks the real resumable
protocol) and for anyone who needs to point at a proxy.
"""
from __future__ import annotations

import re

from dataclasses import dataclass

from ..config import Settings

GOOGLE_HOST = "https://www.googleapis.com"
OAUTH_HOST = "https://oauth2.googleapis.com"
ACCOUNTS_HOST = "https://accounts.google.com"


@dataclass(frozen=True)
class Endpoints:
    authorize: str
    token: str
    revoke: str
    userinfo: str
    about: str
    files: str
    upload: str
    drives: str


def endpoints(cfg: Settings) -> Endpoints:
    base = (getattr(cfg, "google_api_base", "") or "").rstrip("/")
    if base:
        return Endpoints(
            authorize=f"{base}/o/oauth2/v2/auth",
            token=f"{base}/token",
            revoke=f"{base}/revoke",
            userinfo=f"{base}/openidconnect/v1/userinfo",
            about=f"{base}/drive/v3/about",
            files=f"{base}/drive/v3/files",
            upload=f"{base}/upload/drive/v3/files",
            drives=f"{base}/drive/v3/drives",
        )
    return Endpoints(
        authorize=f"{ACCOUNTS_HOST}/o/oauth2/v2/auth",
        token=f"{OAUTH_HOST}/token",
        revoke=f"{OAUTH_HOST}/revoke",
        userinfo="https://openidconnect.googleapis.com/v1/userinfo",
        about=f"{GOOGLE_HOST}/drive/v3/about",
        files=f"{GOOGLE_HOST}/drive/v3/files",
        upload=f"{GOOGLE_HOST}/upload/drive/v3/files",
        drives=f"{GOOGLE_HOST}/drive/v3/drives",
    )


_FOLDER_RE = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_ID_RE = re.compile(r"[?&]id=([A-Za-z0-9_-]{10,})")


def parse_folder_id(text: str) -> str:
    """Accept a Drive URL or a bare id. Anything unusable falls back to root."""
    text = (text or "").strip()
    if not text or text in ("root", "my drive", "mydrive"):
        return "root"
    for rx in (_FOLDER_RE, _ID_RE):
        m = rx.search(text)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", text):
        return text
    return "root"
