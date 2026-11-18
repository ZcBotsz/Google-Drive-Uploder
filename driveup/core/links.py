"""Signed Studio links — the only way a browser gets a session.

Why this module exists
----------------------
A Telegram *url* button opens the link in the phone's own browser, where
`window.Telegram.WebApp.initData` is empty. The Studio's `/api/me` therefore
answers 401 and the page shows the "open this from the bot" blocker — the
website looks broken even though the server is fine.

So the bot mints a short-lived, HMAC-signed token, and `/start?t=<token>`
(exposed as `/connect` and `/admin`) redirects to `/`, setting a real session
cookie on the way. The signature covers `user:job:exp` with `SESSION_SECRET`,
so a token can be neither forged nor replayed after it expires.

Admin links additionally carry `:admin` and can only be minted for a user whose
role is owner/admin/support — checked again, server-side, when redeemed.
"""
from __future__ import annotations

import hashlib
import hmac
import time
from typing import Optional, Tuple

# 15 minutes: long enough to switch from Telegram to the browser, short enough
# that a link pasted into a group chat is useless by the time anyone reads it.
LINK_TTL = 15 * 60

ADMIN_ROLES = ("owner", "admin", "support")


def _digest(secret: str, user_id: int, job_id: str, exp: int, admin: bool) -> str:
    msg = f"{int(user_id)}:{job_id or ''}:{int(exp)}:{1 if admin else 0}"
    return hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()[:40]


def mint_token(secret: str, user_id: int, *, job_id: str = "", admin: bool = False,
               ttl: int = LINK_TTL, now: Optional[float] = None) -> str:
    """`<uid>-<exp>-<job>-<sig>[a]` — parseable without a DB round-trip."""
    exp = int((time.time() if now is None else now) + ttl)
    job = (job_id or "").replace("-", "")          # job ids are hex; '-' is our separator
    sig = _digest(secret, user_id, job, exp, admin)
    # The admin marker is `-a`, not a bare `a`. The signature is hex, so it ends
    # in the digit `a` for 1 token in 16 — a bare marker stripped that digit and
    # rejected a fresh, valid link. That produced the intermittent 401s where
    # the Studio or /admin "sometimes just does not open".
    return f"{int(user_id)}-{exp}-{job}-{sig}" + ("-a" if admin else "")


def parse_token(secret: str, token: str, *, now: Optional[float] = None) -> Optional[dict]:
    """Return `{user_id, exp, admin}` for a valid token; None for anything else."""
    if not secret or not token or len(token) > 120:
        return None
    admin = token.endswith("-a")
    body = token[:-2] if admin else token
    parts = body.split("-")
    if len(parts) != 4:
        return None
    uid_s, exp_s, job, sig = parts
    if not (uid_s.isdigit() and exp_s.isdigit()) or len(sig) != 40:
        return None
    exp = int(exp_s)
    stamp = time.time() if now is None else now
    # Both sides are whole seconds: `exp` is minted as int(now)+ttl and `stamp` is
    # floored here, so a token minted with a small ttl is never dead on arrival
    # (mint at T.9 with ttl=1 → exp=T+1, still valid at T+1.99), yet it still
    # expires on the correct second.
    if int(stamp) > exp or exp > stamp + LINK_TTL + 3600:   # expired, or forged far future
        return None
    expected = _digest(secret, int(uid_s), job, exp, admin)
    if not hmac.compare_digest(expected, sig):
        return None
    return {"user_id": int(uid_s), "exp": exp, "admin": admin, "job_id": job}
