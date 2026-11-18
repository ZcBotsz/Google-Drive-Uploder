"""Web authentication — Telegram `initData`, Login Widget, session cookies.

Rules from the spec (B5, B12) enforced here:
  * `initData` is verified with HMAC-SHA256(secret=HMAC("WebAppData", BOT_TOKEN)),
    `auth_date` must be ≤ 1 h old, comparisons are constant-time.
  * a bare `uid` parameter is never trusted — the only identity is a signed,
    HttpOnly, SameSite=Lax session cookie minted after verification.
  * admin sessions auto-lock after ADMIN_IDLE_MINUTES of inactivity.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from typing import Optional
from urllib.parse import parse_qsl

from fastapi import HTTPException, Request

from ..config import Settings
from ..db import repo
from ..db.sql import Database

log = logging.getLogger("driveup.web.auth")

SESSION_COOKIE = "du_session"
MAX_AUTH_AGE = 3600
SESSION_TTL = 7 * 86400


class AuthError(HTTPException):
    def __init__(self, detail: str = "unauthorised", status_code: int = 401) -> None:
        super().__init__(status_code=status_code, detail=detail)


# ── Telegram signature checks ───────────────────────────────────────────────
def initdata_secret(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def widget_secret(bot_token: str) -> bytes:
    return hashlib.sha256(bot_token.encode()).digest()


def parse_initdata(init_data: str) -> dict:
    if not init_data:
        raise AuthError("initData is empty")
    pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=False))
    if "hash" not in pairs:
        raise AuthError("initData has no hash")
    given = pairs.pop("hash")
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    return {"fields": pairs, "data_check": data_check, "given": given}


def verify_initdata(init_data: str, bot_token: str, *, max_age: int = MAX_AUTH_AGE,
                    now: Optional[float] = None) -> dict:
    """Return the user dict, or raise AuthError. Never returns unverified data."""
    parsed = parse_initdata(init_data)
    expected = hmac.new(initdata_secret(bot_token), parsed["data_check"].encode(),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, parsed["given"]):
        raise AuthError("initData signature is invalid")
    fields = parsed["fields"]
    try:
        auth_date = int(fields.get("auth_date", "0"))
    except ValueError:
        raise AuthError("initData auth_date is malformed") from None
    now = time.time() if now is None else now
    if auth_date <= 0 or now - auth_date > max_age:
        raise AuthError("initData is too old — reopen the Studio")
    if auth_date > now + 300:
        raise AuthError("initData auth_date is in the future")
    import json
    user = json.loads(fields.get("user", "{}") or "{}")
    if not user.get("id"):
        raise AuthError("initData carries no user")
    return {"user": user, "auth_date": auth_date, "query_id": fields.get("query_id", "")}


def verify_widget(query: dict, bot_token: str, *, max_age: int = 86400,
                  now: Optional[float] = None) -> dict:
    """Telegram Login Widget (browser outside Telegram)."""
    given = query.get("hash", "")
    if not given:
        raise AuthError("widget hash missing")
    check = {k: v for k, v in query.items() if k != "hash"}
    data_check = "\n".join(f"{k}={v}" for k, v in sorted(check.items()))
    expected = hmac.new(widget_secret(bot_token), data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, given):
        raise AuthError("widget signature is invalid")
    try:
        auth_date = int(query.get("auth_date", "0"))
    except ValueError:
        raise AuthError("widget auth_date is malformed") from None
    now = time.time() if now is None else now
    if not auth_date or now - auth_date > max_age:
        raise AuthError("widget login is too old")
    user = {"id": int(query.get("id", 0)), "first_name": query.get("first_name", ""),
            "last_name": query.get("last_name", ""), "username": query.get("username", ""),
            "photo_url": query.get("photo_url", "")}
    if not user["id"]:
        raise AuthError("widget login carries no id")
    return {"user": user, "auth_date": auth_date}


# ── sessions ────────────────────────────────────────────────────────────────
async def mint_session(db: Database, user_id: int, ip: str = "", ua: str = "",
                       ttl: int = SESSION_TTL) -> str:
    sid = secrets.token_urlsafe(32)
    await repo.create_session(db, sid, user_id, ttl, ip, ua)
    return sid


def sign_payload(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:32]


def verify_link(secret: str, token: str) -> Optional[dict]:
    """Check a `/start?t=…` token; return its claim or None.

    Thin wrapper so the web layer never has to import the bot package.
    """
    from ..core.links import parse_token
    return parse_token(secret, token)


async def session_user(request: Request) -> dict:
    """FastAPI dependency: the logged-in user row, or 401."""
    state = request.app.state
    sid = request.cookies.get(SESSION_COOKIE) or request.headers.get("X-DU-Session", "")
    if not sid:
        raise AuthError("no session — open the Studio from the bot")
    row = await repo.get_session(state.db, sid)
    if not row:
        raise AuthError("session expired — reopen the Studio")
    await repo.touch_session(state.db, sid, extend=3600 if time.time() - row["last_seen"] > 3600 else 0)
    user = await repo.get_user(state.db, row["user_id"])
    if not user:
        user = await repo.upsert_user(state.db, row["user_id"])
    role = await repo.role_of(state.db, user["user_id"], state.cfg.owner_id)
    user["role"] = role
    user["sid"] = sid
    return user


def admin_unlock_token(secret: str, sid: str, issued: int) -> str:
    payload = f"{sid}:{issued}"
    mac = hmac.new(secret.encode(), ("admin-unlock:" + payload).encode(), hashlib.sha256).hexdigest()
    return f"{issued}.{mac}"


def verify_admin_unlock(secret: str, sid: str, token: str, *, now: Optional[int] = None) -> bool:
    try:
        issued_s, mac = token.split(".", 1)
        issued = int(issued_s)
    except (ValueError, AttributeError):
        return False
    now = int(time.time()) if now is None else now
    if issued > now or now - issued > 600:
        return False
    expected = admin_unlock_token(secret, sid, issued).split(".", 1)[1]
    return hmac.compare_digest(expected, mac)


async def admin_user(request: Request) -> dict:
    """Role check + optional second password factor + idle auto-lock."""
    user = await session_user(request)
    if user["role"] not in ("owner", "admin", "support"):
        raise AuthError("this account is not an admin", status_code=403)
    if request.app.state.cfg.admin_password:
        token = request.cookies.get("du_admin_unlock", "")
        if not verify_admin_unlock(request.app.state.cfg.session_secret, user["sid"], token):
            raise AuthError("admin password required", status_code=401)
    sid = user["sid"]
    row = await repo.get_session(request.app.state.db, sid)
    idle = time.time() - (row["last_seen"] if row else 0)
    if idle > request.app.state.cfg.admin_idle_minutes * 60:
        raise AuthError("admin session locked after inactivity — sign in again", status_code=401)
    return user


def set_session_cookie(response, sid: str, *, secure: bool = True) -> None:
    response.set_cookie(SESSION_COOKIE, sid, max_age=SESSION_TTL, httponly=True,
                        samesite="lax", secure=secure, path="/")


def clear_session_cookie(response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/")
