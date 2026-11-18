"""Per-user custom thumbnails for the files we upload to Drive.

From `kalanakt/All-Url-Uploader`: a user sends a photo, and every later upload
carries it as the file's thumbnail. That repo stores a JPEG on disk per user,
which we cannot do well — the container filesystem is wiped on every deploy.

Here the image goes into SQLite (which already lives on the volume) and only a
small `thumb:<user_id>` marker is kept in `users.settings`, so nothing is lost
on redeploy and nothing large is ever rewritten on each message.

Drive's own limit: thumbnails must be ≤2 MB and are only used for files that
have none. We enforce the size on intake and say so.
"""
from __future__ import annotations

import base64
import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import BufferedInputFile, Message

from ...db import repo

log = logging.getLogger("driveup.handlers.thumb")
router = Router(name="thumb")

MAX_THUMB_BYTES = 2 * 1024 * 1024          # Drive's documented ceiling


@router.message(Command("thumb"))
async def cmd_thumb(msg: Message, **data):
    app = data["_app"]
    row = await repo.get_drive_account(app.db, msg.from_user.id)
    if not row:
        return await msg.answer("Connect Drive first — send /connect.")
    blob = await repo.get_user_thumb(app.db, msg.from_user.id)
    if not blob:
        return await msg.answer(
            "No thumbnail saved.\n\nSend me a <b>photo</b> and I will use it as "
            "the thumbnail for your future uploads.")
    await msg.answer_photo(BufferedInputFile(blob, filename="thumb.jpg"),
                           caption="Your current upload thumbnail.")


@router.message(Command("delthumb"))
async def cmd_del_thumb(msg: Message, **data):
    app = data["_app"]
    gone = await repo.set_user_thumb(app.db, msg.from_user.id, b"")
    await msg.answer("🗑 Thumbnail removed." if gone else "No thumbnail saved.")


@router.message(Command("thumb_set"))
async def cmd_thumb_set(msg: Message, **data):
    """Arm the next photo as a thumbnail instead of an upload.

    A bare `F.photo` handler in front of the uploader would quietly turn every
    photo a connected user sends into a thumbnail — their photos would stop
    reaching Drive. So the user asks first, and only the *next* photo is taken.
    """
    app = data["_app"]
    if not await repo.get_drive_account(app.db, msg.from_user.id):
        return await msg.answer("Connect Drive first — send /connect.")
    app.thumb_await.add(msg.from_user.id)
    await msg.answer("📷 Send the photo you want as your thumbnail (under 2 MB).\n"
                     "Send anything else to cancel.")


@router.message(F.photo)
async def on_photo(msg: Message, **data):
    """Only runs when /thumb_set armed this user — see above."""
    app = data["_app"]
    waiting = getattr(app, "thumb_await", None)
    if not waiting or msg.from_user.id not in waiting:
        return                      # not asked for: let the uploader have it
    waiting.discard(msg.from_user.id)
    if not await repo.get_drive_account(app.db, msg.from_user.id):
        return                      # not connected: let the normal flow answer
    photo = msg.photo[-1]
    if (photo.file_size or 0) > MAX_THUMB_BYTES:
        return await msg.answer("That image is over 2 MB — Drive will not accept "
                                "it as a thumbnail. Send a smaller one.")
    try:
        buf = await app.bot.download(photo, destination=None)
        blob = buf.read() if hasattr(buf, "read") else bytes(buf)
    except Exception as e:                                 # noqa: BLE001
        log.debug("thumbnail download failed: %s", e)
        return await msg.answer("Could not read that photo — try again.")
    if len(blob) > MAX_THUMB_BYTES:
        return await msg.answer("That image is over 2 MB — Drive will not accept it.")
    await repo.set_user_thumb(app.db, msg.from_user.id, blob)
    await msg.answer("✅ Thumbnail saved. Send /delthumb to remove it.")


def thumb_for(settings: dict) -> str:
    """Small marker stored in users.settings — keeps the JSON column tiny."""
    return str(settings.get("thumb") or "")


def encode(blob: bytes) -> str:
    return base64.b64encode(blob).decode() if blob else ""
