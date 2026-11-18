"""Can Drive actually write to the folder this job points at?

With the default `drive.file` scope Google only grants access to files the app
itself created, so a folder the user picked from My Drive fails on the first
upload with a bare 403. Every lookup used to swallow that error, the job started
anyway, and the uploader simply "did not work". Returning the explanation
*before* the job starts — with the one-line cure — is the whole point here.
"""
from __future__ import annotations

from ..core.access import human_bytes
from ..db import repo
from ..drive.errors import DriveError, PermissionDenied
from ..strings import esc


async def destination_problem(app, uid: int, job: dict) -> str:
    """Why Drive will refuse this folder — or "" when it is writable."""
    dest = job.get("destination") or {}
    fid = dest.get("folder_id") or "root"
    if fid == "root":
        return ""
    client = await app.accounts.client(uid)
    try:
        await client.get(fid)
        return ""
    except PermissionDenied:
        if not app.cfg.scope_mode_full:
            return ("⛔ <b>DriveUp cannot write to this folder.</b>\n\n"
                    f"Your connection uses the least-privilege <code>drive.file</code> scope, "
                    f"which only grants folders DriveUp created itself — not "
                    f"<i>{esc(dest.get('path') or fid)}</i>.\n\n"
                    "Pick <b>⌂ My Drive</b> and let DriveUp create the folder, or ask the "
                    "owner to set <code>DRIVE_SCOPE_MODE=full</code> and then send /connect again.")
        return ("⛔ <b>No permission for that folder.</b>\n\n"
                "The Google account you connected cannot write there — pick another folder, "
                "or send /connect and allow the wider scope.")
    except DriveError:
        return ""
    except Exception:                                  # noqa: BLE001
        return ""


async def storage_problem(app, uid: int, job: dict) -> str:
    """Will Drive have room for this job — or "" when it will fit.

    `storageQuotaExceeded` arrives as a 403 *per file*, so a full Drive used to
    fail every item one at a time and leave a job full of red errors with no
    explanation. Checking the free space once, before the job starts, turns a
    wall of failures into one sentence the user can act on.
    """
    try:
        client = await app.accounts.client(uid)
        q = await client.about()
    except Exception:                                   # noqa: BLE001
        return ""      # about() can be restricted; never block on our own doubt
    limit = int(q.get("limit", 0) or 0)
    if limit <= 0:
        return ""      # pooled/Workspace accounts report 0 — unknown, not full
    free = int(q.get("free", -1))
    if free < 0:
        return ""
    need = 0
    try:
        items = await repo.items_of(app.db, job["id"])
        # size lives inside the `tg` blob, not a top-level column — reading
        # i["size"] here silently returned None for every item, so the check
        # always concluded "it fits" and never fired.
        need = sum(int((i.get("tg") or {}).get("size") or 0) for i in items
                   if i.get("state") in ("pending", "uploading"))
    except Exception:                                   # noqa: BLE001
        need = 0
    if need <= free:
        return ""
    return ("💾 <b>Google Drive is full.</b>\n\n"
            f"<b>{human_bytes(free)}</b> free of <b>{human_bytes(limit)}</b>, "
            f"but this job needs <b>{human_bytes(need)}</b>.\n\n"
            "Free up space, or connect a different Google account with /connect "
            "and start again. Nothing was uploaded, so nothing was lost.\n\n"
            "Send /storage any time to check space.")


async def quota_line(app, uid: int) -> str:
    """A one-line storage readout for /storage — used, free, total, and a bar."""
    client = await app.accounts.client(uid)
    q = await client.about()
    limit = int(q.get("limit", 0) or 0)
    usage = int(q.get("usage", 0) or 0)
    email = q.get("email") or "your account"
    if limit <= 0:
        return (f"💾 <b>{esc(email)}</b>\nGoogle does not publish a storage limit "
                "for this account (pooled/Workspace), so it cannot be checked here.")
    free = max(0, limit - usage)
    pct = int(usage * 100 / limit)
    filled = round(pct / 10)
    bar = "█" * filled + "░" * (10 - filled)
    head = "⚠️ <b>Drive is full</b>" if free == 0 else "💾 <b>Drive storage</b>"
    tail = ("\n\nFree some space (or /connect a different account) before uploading."
            if free == 0 else "")
    return (f"{head}\n<b>{esc(email)}</b>\n<code>{bar}</code> {pct}%\n"
            f"Used <b>{human_bytes(usage)}</b> of <b>{human_bytes(limit)}</b> — "
            f"<b>{human_bytes(free)}</b> free.{tail}")
