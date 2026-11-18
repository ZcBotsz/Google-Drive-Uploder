"""Attach a user's custom thumbnail to Drive metadata (see handlers/thumb.py)."""
from __future__ import annotations

import base64
import logging

from ..db import repo

log = logging.getLogger("driveup.thumbs")


async def apply_thumb(worker, meta: dict, user_id: int) -> dict:
    """Add Drive's `contentHints.thumbnail` when the user has saved one.

    Drive only *uses* this hint for files it cannot preview itself, and only
    accepts a small image — both already enforced on intake. Failure here is
    cosmetic, so it must never fail an upload.
    """
    try:
        blob = await repo.get_user_thumb(worker.db, user_id)
        if blob:
            meta["contentHints"] = {
                "thumbnail": {"image": base64.b64encode(blob).decode(),
                              "mimeType": "image/jpeg"},
            }
    except Exception as e:                      # noqa: BLE001
        log.debug("thumbnail skipped: %s", e)
    return meta
