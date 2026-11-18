"""The bridge between a user id and a live, authorised DriveClient.

Responsibilities:
  * decrypt the stored refresh token
  * refresh the access token 60 s before it expires
  * on `invalid_grant` mark the account `needs_reconnect` and raise AuthExpired
  * never leak a token into a log line
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

import aiohttp

from ..config import Settings
from ..db import repo
from ..db.sql import Database
from . import auth
from .client import DriveClient
from .errors import AuthExpired

log = logging.getLogger("driveup.drive")


class AccountManager:
    def __init__(self, db: Database, cfg: Settings,
                 http: Optional[aiohttp.ClientSession] = None) -> None:
        self.db = db
        self.cfg = cfg
        self._http = http
        self._own_http = http is None
        self._locks: dict[int, asyncio.Lock] = {}

    async def http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=300))
            self._own_http = True
        return self._http

    async def close(self) -> None:
        if self._own_http and self._http and not self._http.closed:
            await self._http.close()

    def _lock(self, user_id: int) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    async def token_for(self, user_id: int) -> str:
        """A valid access token, refreshing if needed. Raises AuthExpired."""
        async with self._lock(user_id):
            row = await repo.get_drive_account(self.db, user_id)
            if not row:
                raise AuthExpired("Google Drive is not connected")
            if row.get("needs_reconnect"):
                raise AuthExpired("Google access needs reconnecting")
            if auth.expiry_is_fresh(row.get("access_expiry", 0)):
                return row["access_token"]
            cipher = auth.TokenCipher(self.cfg.fernet_key())  # type: ignore[arg-type]
            try:
                refresh = cipher.dec(row.get("enc_refresh_token", ""))
            except auth.OAuthError as e:
                await repo.mark_needs_reconnect(self.db, user_id, True)
                raise AuthExpired(str(e)) from None
            if not refresh:
                await repo.mark_needs_reconnect(self.db, user_id, True)
                raise AuthExpired("No refresh token stored — reconnect Google Drive")
            http = await self.http()
            try:
                tok = await auth.refresh_access_token(self.cfg, refresh, http)
            except auth.OAuthError as e:
                await repo.mark_needs_reconnect(self.db, user_id, True)
                raise AuthExpired(str(e)) from None
            patch = auth.token_row_patch(tok)
            await self.db.x("UPDATE drive_accounts SET access_token=?, access_expiry=?, "
                            "needs_reconnect=0 WHERE user_id=?",
                            (patch["access_token"], patch["access_expiry"], user_id))
            return patch["access_token"]

    async def client(self, user_id: int) -> DriveClient:
        token = await self.token_for(user_id)
        http = await self.http()

        async def refresher() -> str:
            await self.db.x("UPDATE drive_accounts SET access_expiry=0 WHERE user_id=?", (user_id,))
            return await self.token_for(user_id)

        return DriveClient(http, token, token_refresher=refresher, base=self.cfg.google_api_base)

    async def connect_from_code(self, user_id: int, code: str, verifier: str) -> dict:
        """Exchange the OAuth code, store the encrypted refresh token, return about()."""
        http = await self.http()
        tok = await auth.exchange_code(self.cfg, code, verifier, http)
        access = tok.get("access_token", "")
        if not access:
            raise auth.OAuthError("Google did not return an access token")
        try:
            info = await auth.userinfo(access, http, self.cfg)
        except auth.OAuthError:
            info = {}
        patch = auth.token_row_patch(tok)
        cipher = auth.TokenCipher(self.cfg.fernet_key())  # type: ignore[arg-type]
        enc = cipher.enc(tok.get("refresh_token", ""))
        if not enc:
            # No refresh token on a re-consent: keep the one we already have.
            old = await repo.get_drive_account(self.db, user_id)
            enc = old.get("enc_refresh_token", "") if old else ""
        client = DriveClient(http, access, base=self.cfg.google_api_base)
        try:
            about = await client.about()
            quota = (about.get("limit", 0), about.get("usage", 0))
        except Exception as e:            # about() may fail on a restricted scope
            log.warning("about() after connect failed: %s", e)
            quota = (0, 0)
        await repo.save_drive_account(
            self.db, user_id,
            email=info.get("email", about.get("email", "")),
            google_sub=info.get("sub", ""),
            enc_refresh=enc, access=patch["access_token"],
            expiry=patch["access_expiry"], scope=patch["scope"],
            scope_mode=self.cfg.drive_scope_mode,
            quota_limit=quota[0], quota_used=quota[1])
        await repo.audit(self.db, user_id, "drive.connect",
                         detail={"email": info.get("email", ""), "scope_mode": self.cfg.drive_scope_mode})
        return {"email": info.get("email", ""), "name": info.get("name", ""),
                "limit": quota[0], "usage": quota[1],
                "free": max(0, quota[0] - quota[1]) if quota[0] else -1}

    async def disconnect(self, user_id: int) -> bool:
        row = await repo.get_drive_account(self.db, user_id)
        if not row:
            return False
        cipher = auth.TokenCipher(self.cfg.fernet_key())  # type: ignore[arg-type]
        try:
            refresh = cipher.dec(row.get("enc_refresh_token", ""))
        except auth.OAuthError:
            refresh = ""
        if refresh:
            http = await self.http()
            await auth.revoke(refresh, http, self.cfg)
        await repo.drop_drive_account(self.db, user_id)
        await repo.audit(self.db, user_id, "drive.disconnect")
        return True
