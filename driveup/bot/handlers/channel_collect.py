"""Owner-only collection from one configured Telegram channel into Studio drafts.

The channel is identified only by COLLECT_CHANNEL_ID. Channel posts are never
accepted from any other channel; private-bot controls are restricted to OWNER_ID.
"""
from __future__ import annotations

import logging
import time

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from ...config import effective_max_file_mb
from ...core import naming as naming_mod
from ...db import repo
from ...strings import esc
from ..keyboards import cb
from .upload import file_of, recompute

log = logging.getLogger("driveup.handlers.channel_collect")
router = Router(name="channel_collect")


def _channel_id(app) -> str:
    return str(app.cfg.collect_channel_id or "").strip()


def _keyboard(app, job_id: str, *, can_finish: bool) -> InlineKeyboardMarkup:
    owner_id = int(app.cfg.owner_id or 0)
    rows = [[InlineKeyboardButton(text="🌐 Open Studio", url=app.studio_url(job_id, owner_id))]]
    if can_finish:
        rows.insert(0, [InlineKeyboardButton(text="✅ Finish batch", callback_data=cb("chfinish", job_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_or_create_batch(app, channel_id: str) -> tuple[dict, bool]:
    row = await app.db.one("SELECT * FROM channel_batches WHERE channel_id=?", (channel_id,))
    if row and row["state"] == "open":
        if int(row["owner_id"]) != int(app.cfg.owner_id or 0):
            raise RuntimeError("Open channel batch belongs to a different configured owner")
        if await repo.get_job(app.db, row["job_id"]):
            return row, False

    owner_id = int(app.cfg.owner_id or 0)
    user = await repo.get_user(app.db, owner_id)
    if not user:
        user = await repo.upsert_user(app.db, owner_id, name="Owner", lang="en")
    prefs = user.get("settings") or {}
    destination = prefs.get("default_folder") or {"folder_id": "root", "path": "My Drive"}
    naming = naming_mod.NamingOptions.from_dict(prefs.get("naming") or {}).to_dict()
    job_id = await repo.create_job(
        app.db, owner_id, destination=destination, naming=naming,
        sequence_mode=prefs.get("sequence_mode", "telegram_order"),
        conflict_policy=prefs.get("conflict", "rename"),
        share_mode=prefs.get("share", "restricted"),
        source={"kind": "telegram_channel", "channel_id": channel_id})
    now = time.time()
    await app.db.x(
        "INSERT INTO channel_batches(channel_id,owner_id,job_id,state,notice_message_id,updated_at) "
        "VALUES(?,?,?,'open',0,?) ON CONFLICT(channel_id) DO UPDATE SET "
        "owner_id=excluded.owner_id,job_id=excluded.job_id,state='open',notice_message_id=0,updated_at=excluded.updated_at",
        (channel_id, owner_id, job_id, now))
    return await app.db.one("SELECT * FROM channel_batches WHERE channel_id=?", (channel_id,)), True


async def _say_owner(app, text: str, job_id: str, *, can_finish: bool = True):
    owner_id = int(app.cfg.owner_id or 0)
    return await app.bot.send_message(owner_id, text, reply_markup=_keyboard(app, job_id, can_finish=can_finish))


async def _refresh_notice(app, row: dict, *, finished: bool = False) -> None:
    mid = int(row.get("notice_message_id") or 0)
    if not mid:
        return
    items = await repo.items_of(app.db, row["job_id"])
    total_bytes = sum(int((item.get("tg") or {}).get("size", 0)) for item in items)
    rejected = await app.db.scalar("SELECT COUNT(*) FROM channel_rejections WHERE job_id=?", (row["job_id"],), 0)
    state = "Batch ready for review" if finished else "Collecting files from your private channel"
    text = (f"📥 <b>{state}</b>\n"
            f"Files collected: <b>{len(items)}</b> · {esc(_human(total_bytes))}\n"
            f"Rejected posts: <b>{rejected}</b> (use /channel_status for details).\n"
            "Open Studio to arrange sequence, folder counts, and review before starting.")
    await app.bot.edit_message_text(
        chat_id=int(row["owner_id"]), message_id=mid, text=text, parse_mode="HTML",
        reply_markup=_keyboard(app, row["job_id"], can_finish=not finished))


def _human(n: int) -> str:
    from ...core.access import human_bytes
    return human_bytes(n)


@router.channel_post()
async def collect_channel_post(msg: Message, **data):
    app = data["_app"]
    configured = _channel_id(app)
    if not configured or str(msg.chat.id) != configured:
        return
    owner_id = int(app.cfg.owner_id or 0)
    if owner_id <= 0:
        log.error("COLLECT_CHANNEL_ID is set but OWNER_ID is not a positive Telegram user id")
        return
    f = file_of(msg)
    if not f:
        return  # Ignore text/service posts; only supported media become queue items.

    async with app.channel_collect_lock:
        await _collect_locked(app, msg, configured, owner_id, f)


async def _reject(app, row, msg, reason: str):
    await app.db.x("INSERT OR REPLACE INTO channel_rejections(channel_id,message_id,job_id,reason,created_at) VALUES(?,?,?,?,?)", (str(msg.chat.id), msg.message_id, row["job_id"], reason, time.time()))
    try:
        await app.bot.send_message(int(app.cfg.owner_id), f"⚠️ Channel post {msg.message_id} was not collected: " + reason + " Check the channel and send it again after resolving the limit.")
    except Exception:
        log.warning("Unable to notify owner that a channel file was rejected")


async def _collect_locked(app, msg, configured, owner_id, f):
    # Ignore Telegram redelivery of the same post, including after a batch closes.
    if await app.db.one("SELECT id FROM job_items WHERE json_extract(tg, '$.chat_id')=? AND json_extract(tg, '$.msg_id')=? LIMIT 1", (msg.chat.id, msg.message_id)):
        return
    row, created = await _get_or_create_batch(app, configured)
    job = await repo.get_job(app.db, row["job_id"])
    if not job or job["status"] != "draft":
        return
    items = await repo.items_of(app.db, job["id"])
    plan = await repo.effective_plan(app.db, owner_id)
    max_files = int(plan.get("max_files_job") or 5000)
    if len(items) >= max_files:
        await _reject(app, row, msg, f"your plan allows {max_files} files per job. Finish this batch first.")
        return
    cap_mb = effective_max_file_mb(app.cfg, requested_mb=plan.get("max_file_mb", 2000))
    if f["size"] > cap_mb * 1024 ** 2:
        await _reject(app, row, msg, f"the file exceeds the effective Telegram limit of {cap_mb} MB.")
        return
    if f["size"] <= 0:
        await _reject(app, row, msg, "the file size is zero or unknown.")
        return
    unique_id = f["unique"]
    if any((item.get("tg") or {}).get("msg_id") == msg.message_id and (item.get("tg") or {}).get("chat_id") == msg.chat.id for item in items):
        return
    daily_cap = int(float(plan.get("daily_gb") or 0) * 1024 ** 3)
    used = await repo.quota_used(app.db, owner_id)
    if daily_cap and int(used.get("bytes", 0)) + sum(int((item.get("tg") or {}).get("size", 0)) for item in items) + f["size"] > daily_cap:
        await _reject(app, row, msg, "the batch exceeds your remaining daily quota.")
        return

    await repo.add_item(
        app.db, job["id"], file_id=f["file_id"], file_unique_id=unique_id,
        msg_id=msg.message_id, size=f["size"], mime=f["mime"], name=f["name"],
        chat_id=msg.chat.id, seq_index=msg.message_id, caption=(msg.caption or ""))
    await repo.remember_file(app.db, file_unique_id=unique_id, chat_id=msg.chat.id,
                             message_id=msg.message_id, file_id=f["file_id"],
                             name=f["name"], size=f["size"], mime=f["mime"])
    items = await repo.items_of(app.db, job["id"])
    await repo.recompute_totals(app.db, job["id"])
    await app.db.x("UPDATE channel_batches SET updated_at=? WHERE channel_id=?", (time.time(), configured))

    if created:
        await app.log_event("#ChannelBatchDraft", {"user": owner_id, "job": job["id"]})
    if not row.get("notice_message_id"):
        total_bytes = sum(int((item.get("tg") or {}).get("size", 0)) for item in items)
        text = ("📥 <b>Private channel batch started</b>\n"
                f"Files collected: <b>{len(items)}</b> · {_human(total_bytes)}\n"
                "Keep posting files to the configured channel. When finished, tap <b>Finish batch</b> or send <code>/finish</code> to me. Then open Studio to review and start.")
        try:
            sent = await _say_owner(app, text, job["id"], can_finish=True)
            await app.db.x("UPDATE channel_batches SET notice_message_id=?,updated_at=? WHERE channel_id=?",
                           (int(sent.message_id), time.time(), configured))
            await repo.update_job(app.db, job["id"], status_message={"chat_id": owner_id, "message_id": sent.message_id})
        except Exception as e:  # user must start the bot once before it can DM them
            log.warning("could not notify owner for private channel collection: %s", e)


async def _finish(app, job_id: str, owner_id: int) -> bool:
    async with app.channel_collect_lock:
        return await _finish_locked(app, job_id, owner_id)


async def _finish_locked(app, job_id: str, owner_id: int) -> bool:
    row = await app.db.one("SELECT * FROM channel_batches WHERE job_id=? AND owner_id=?", (job_id, owner_id))
    if not row or row["state"] != "open":
        return False
    job = await repo.get_job(app.db, job_id)
    if not job or job["status"] != "draft":
        return False
    items = await repo.items_of(app.db, job_id)
    if not items:
        return False
    await recompute(app, job, items, naming_mod.NamingOptions.from_dict(job.get("naming") or {}))
    await repo.recompute_totals(app.db, job_id)
    await app.db.x("UPDATE channel_batches SET state='finished',updated_at=? WHERE channel_id=? AND job_id=?",
                   (time.time(), row["channel_id"], job_id))
    await app.log_event("#ChannelBatchFinished", {"user": owner_id, "job": job_id, "files": len(items)})
    try:
        await _refresh_notice(app, row, finished=True)
    except Exception:
        log.warning("Could not update channel batch notice")
    return True


@router.message(Command("finish"))
async def finish_command(msg: Message, **data):
    app = data["_app"]
    owner_id = int(app.cfg.owner_id or 0)
    if not _channel_id(app):
        return await msg.answer("Private channel collection is not enabled on this service.")
    if not msg.from_user or msg.from_user.id != owner_id or msg.chat.type != "private":
        return  # Do not expose controls or leak collection details to other users.
    row = await app.db.one("SELECT * FROM channel_batches WHERE channel_id=? AND owner_id=? AND state='open'",
                           (_channel_id(app), owner_id))
    if not row:
        return await msg.answer("There is no open channel batch to finish.")
    if await _finish(app, row["job_id"], owner_id):
        return await msg.answer("✅ Batch closed. Review its files, sequence, and folder assignments in Studio. Upload starts only when you start it there.", reply_markup=_keyboard(app, row["job_id"], can_finish=False))
    return await msg.answer("The batch could not be finished. It must be open and contain at least one accepted file.")


@router.callback_query(F.data.startswith("v1:chfinish:"))
async def finish_button(call: CallbackQuery, **data):
    app = data["_app"]
    owner_id = int(app.cfg.owner_id or 0)
    if not call.from_user or call.from_user.id != owner_id:
        return await call.answer("Only the configured owner can finish this batch.", show_alert=True)
    job_id = (call.data or "").split(":")[-1]
    if await _finish(app, job_id, owner_id):
        await call.answer("Batch closed. Ready for Studio review.")
        return
    await call.answer("This batch is already closed or unavailable.", show_alert=True)


@router.message(Command("channel_status"))
async def channel_status(msg: Message, **data):
    app = data["_app"]
    owner_id = int(app.cfg.owner_id or 0)
    if not msg.from_user or msg.from_user.id != owner_id or msg.chat.type != "private":
        return
    if not _channel_id(app):
        return await msg.answer("Channel collection is not configured.")
    async with app.channel_collect_lock:
        row = await app.db.one("SELECT * FROM channel_batches WHERE channel_id=? AND owner_id=?", (_channel_id(app), owner_id))
        if not row or not await repo.get_job(app.db, row["job_id"]):
            return await msg.answer("There is no channel batch yet. Post files in the configured channel.")
        items = await repo.items_of(app.db, row["job_id"])
        state = "Collecting" if row["state"] == "open" else "Finished"
        rejected = await app.db.q("SELECT message_id,reason FROM channel_rejections WHERE job_id=? ORDER BY message_id DESC LIMIT 10", (row["job_id"],))
        count = await app.db.scalar("SELECT COUNT(*) FROM channel_rejections WHERE job_id=?", (row["job_id"],), 0)
        details = "".join(f"\n• post {r['message_id']}: {esc(r['reason'])}" for r in rejected)
        return await msg.answer(f"📥 {state}: {len(items)} files. Rejected posts: {count} (latest 10 shown).{details}\nUpload does not start automatically.", reply_markup=_keyboard(app, row["job_id"], can_finish=row["state"] == "open"))
