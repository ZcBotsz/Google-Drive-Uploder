"""Google OAuth 2.0 (web flow) with PKCE — no passwords, no cookies, ever.

Flow: build_authorize_url → Google → /auth/google/callback?code&state
      → exchange_code → encrypted refresh token in the DB.

Tokens at rest are Fernet-encrypted (TOKEN_ENC_KEY); the access token is kept in
the row with its wall-clock expiry and refreshed proactively 60 s early.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from typing import Optional
from urllib.parse import urlencode

import aiohttp
from cryptography.fernet import Fernet, InvalidToken

from ..config import Settings, settings as default_settings
from .endpoints import endpoints

SCOPES_EXTRA = ["openid", "email", "profile"]


class OAuthError(RuntimeError):
    pass


# ── PKCE + crypto ───────────────────────────────────────────────────────────
def new_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(48)).decode().rstrip("=")


def challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).decode().rstrip("=")


def new_state() -> str:
    return secrets.token_urlsafe(24)


class TokenCipher:
    """Encrypt/decrypt refresh tokens (and session strings) at rest."""

    def __init__(self, key: str) -> None:
        self._f = Fernet(key)

    def enc(self, plain: str) -> str:
        if not plain:
            return ""
        return self._f.encrypt(plain.encode()).decode()

    def dec(self, blob: str) -> str:
        if not blob:
            return ""
        try:
            return self._f.decrypt(blob.encode()).decode()
        except InvalidToken:
            raise OAuthError("TOKEN_ENC_KEY changed — stored tokens can no longer be read; "
                             "reconnect Google Drive") from None


# ── url building ────────────────────────────────────────────────────────────
def build_authorize_url(cfg: Settings, state: str, verifier: str,
                        login_hint: str = "") -> str:
    scopes = [cfg.drive_scope] + SCOPES_EXTRA
    params = {
        "client_id": cfg.google_client_id,
        "redirect_uri": cfg.redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": state,
        "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{endpoints(cfg).authorize}?{urlencode(params)}"


# ── token exchange ──────────────────────────────────────────────────────────
async def exchange_code(cfg: Settings, code: str, verifier: str,
                        session: Optional[aiohttp.ClientSession] = None) -> dict:
    data = {
        "code": code,
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret,
        "redirect_uri": cfg.redirect_uri,
        "grant_type": "authorization_code",
        "code_verifier": verifier,
    }
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(endpoints(cfg).token, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.text()
            if r.status != 200:
                raise OAuthError(f"token exchange failed ({r.status}): {body[:300]}")
            return await _json(r, body)
    finally:
        if own:
            await session.close()


async def refresh_access_token(cfg: Settings, refresh_token: str,
                               session: Optional[aiohttp.ClientSession] = None) -> dict:
    data = {
        "client_id": cfg.google_client_id,
        "client_secret": cfg.google_client_secret,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(endpoints(cfg).token, data=data, timeout=aiohttp.ClientTimeout(total=30)) as r:
            body = await r.text()
            if r.status != 200:
                low = body.lower()
                if "invalid_grant" in low:
                    raise OAuthError("invalid_grant: the user revoked access")
                raise OAuthError(f"refresh failed ({r.status}): {body[:200]}")
            return await _json(r, body)
    finally:
        if own:
            await session.close()


async def revoke(token: str, session: Optional[aiohttp.ClientSession] = None,
                 cfg: Optional[Settings] = None) -> bool:
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.post(endpoints(cfg or default_settings).revoke, data={"token": token},
                                timeout=aiohttp.ClientTimeout(total=20)) as r:
            return r.status == 200
    except Exception:
        return False
    finally:
        if own:
            await session.close()


async def userinfo(access_token: str, session: Optional[aiohttp.ClientSession] = None,
                   cfg: Optional[Settings] = None) -> dict:
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.get(endpoints(cfg or default_settings).userinfo, headers={"Authorization": f"Bearer {access_token}"},
                               timeout=aiohttp.ClientTimeout(total=20)) as r:
            body = await r.text()
            if r.status != 200:
                raise OAuthError(f"userinfo failed ({r.status})")
            return await _json(r, body)
    finally:
        if own:
            await session.close()


async def _json(resp, body: str) -> dict:
    import json
    try:
        return json.loads(body)
    except ValueError:
        raise OAuthError(f"Google returned non-JSON: {body[:200]}") from None


def token_row_patch(tok: dict, now: Optional[float] = None) -> dict:
    """Turn a token endpoint response into DB columns."""
    now = time.time() if now is None else now
    expires_in = float(tok.get("expires_in", 3600))
    return {"access_token": tok.get("access_token", ""),
            "access_expiry": now + expires_in,
            "scope": tok.get("scope", "")}


def expiry_is_fresh(expiry: float, now: Optional[float] = None, margin: int = 60) -> bool:
    """True when the access token can still be used for the next minute."""
    now = time.time() if now is None else now
    return bool(expiry) and (expiry - margin) > now
