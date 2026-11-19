"""User-isolated, explicitly finished channel collection into Studio drafts."""
from __future__ import annotations

import asyncio
import logging
import time
import re

from aiogram.filters import BaseFilter

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramNetworkError, TelegramRetryAfter, TelegramServerError, TelegramNotFound
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


def _keyboard(app, job_id: str, *, user_id: int, can_finish: bool) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🌐 Open Studio", url=app.studio_url(job_id, user_id))]]
    if can_finish:
        rows.insert(0, [InlineKeyboardButton(text="✅ Finish batch", callback_data=cb("chfinish", job_id))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _get_or_create_batch(app, channel_id: str, owner_id: int) -> tuple[dict, bool]:
    row = await app.db.one("SELECT * FROM channel_batches WHERE channel_id=?", (channel_id,))
    if row and row["state"] == "open":
        if int(row["owner_id"]) != owner_id:
            raise RuntimeError("Open channel batch belongs to a different configured owner")
        if await repo.get_job(app.db, row["job_id"]):
            return row, False

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


async def _say_owner(app, text: str, job_id: str, owner_id: int, *, can_finish: bool = True):
    return await app.bot.send_message(owner_id, text, reply_markup=_keyboard(app, job_id, user_id=owner_id, can_finish=can_finish))


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
        reply_markup=_keyboard(app, row["job_id"], user_id=int(row["owner_id"]), can_finish=not finished))


def _human(n: int) -> str:
    from ...core.access import human_bytes
    return human_bytes(n)


@router.channel_post()
async def collect_channel_post(msg: Message, **data):
    app = data["_app"]
    channel_id = str(msg.chat.id)
    f = file_of(msg)
    if not f:
        return
    async with app.channel_collect_lock:
        binding = await app.db.one("SELECT * FROM channel_bindings WHERE channel_id=?", (channel_id,))
        if binding:
            owner_id = int(binding["user_id"])
            if not await _is_channel_admin(app, channel_id, owner_id):
                await app.db.x("INSERT OR IGNORE INTO channel_verification_failures(channel_id,message_id,owner_id,created_at) VALUES(?,?,?,?)", (channel_id, msg.message_id, owner_id, time.time()))
                now = time.time()
                if now - app.channel_verification_alerts.get(channel_id, 0) >= 60:
                    app.channel_verification_alerts[channel_id] = now
                    try:
                        await app.bot.send_message(owner_id, "⚠️ Channel post(s) were not collected because Telegram could not verify that you and the bot are channel administrators. Check /channel_status, restore access, then repost missing files.")
                    except Exception:
                        log.warning("Could not notify user about channel verification failure")
                return
        elif channel_id == _channel_id(app) and int(app.cfg.owner_id or 0) > 0:
            # Backward compatibility for the original environment-configured owner.
            owner_id = int(app.cfg.owner_id)
            other = await app.db.one("SELECT channel_id FROM channel_bindings WHERE user_id=?", (owner_id,))
            if other:
                return
        else:
            return
        await _collect_locked(app, msg, channel_id, owner_id, f)


async def _reject(app, row, msg, reason: str):
    await app.db.x("INSERT OR REPLACE INTO channel_rejections(channel_id,message_id,job_id,reason,created_at) VALUES(?,?,?,?,?)", (str(msg.chat.id), msg.message_id, row["job_id"], reason, time.time()))
    try:
        await app.bot.send_message(int(row["owner_id"]), f"⚠️ Channel post {msg.message_id} was not collected: " + reason + " Check the channel and send it again after resolving the limit.")
    except Exception:
        log.warning("Unable to notify owner that a channel file was rejected")


async def _collect_locked(app, msg, configured, owner_id, f):
    # Ignore Telegram redelivery of the same post, including after a batch closes.
    if await app.db.one("SELECT id FROM job_items WHERE json_extract(tg, '$.chat_id')=? AND json_extract(tg, '$.msg_id')=? LIMIT 1", (msg.chat.id, msg.message_id)):
        return
    row, created = await _get_or_create_batch(app, configured, owner_id)
    job = await repo.get_job(app.db, row["job_id"])
    if not job or job["status"] != "draft":
        return
    decision = await app.access.gate(owner_id, scope="upload", count_request=False)
    if not decision:
        await _reject(app, row, msg, f"account access is blocked ({decision.reason}).")
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
            sent = await _say_owner(app, text, job["id"], owner_id, can_finish=True)
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
    if not msg.from_user or msg.chat.type != "private":
        return
    owner_id = msg.from_user.id
    row = await app.db.one("SELECT * FROM channel_batches WHERE owner_id=? AND state='open'", (owner_id,))
    if not row:
        return await msg.answer("There is no open channel batch to finish.")
    if await _finish(app, row["job_id"], owner_id):
        return await msg.answer("✅ Batch closed. Review its files, sequence, and folder assignments in Studio. Upload starts only when you start it there.", reply_markup=_keyboard(app, row["job_id"], user_id=owner_id, can_finish=False))
    return await msg.answer("The batch could not be finished. It must be open and contain at least one accepted file.")


@router.callback_query(F.data.startswith("v1:chfinish:"))
async def finish_button(call: CallbackQuery, **data):
    app = data["_app"]
    if not call.from_user or not call.message or call.message.chat.type != "private":
        return await call.answer("Open this in your private bot chat.", show_alert=True)
    owner_id = call.from_user.id
    job_id = (call.data or "").split(":")[-1]
    if await _finish(app, job_id, owner_id):
        await call.answer("Batch closed. Ready for Studio review.")
        return
    await call.answer("This batch is already closed or unavailable.", show_alert=True)


@router.message(Command("channel_status"))
async def channel_status(msg: Message, **data):
    app = data["_app"]
    if not msg.from_user or msg.chat.type != "private":
        return
    owner_id = msg.from_user.id
    async with app.channel_collect_lock:
        row = await app.db.one("SELECT * FROM channel_batches WHERE owner_id=? ORDER BY updated_at DESC LIMIT 1", (owner_id,))
        failures = await app.db.q("SELECT message_id FROM channel_verification_failures WHERE owner_id=? ORDER BY created_at DESC LIMIT 10", (owner_id,))
        fail_count = await app.db.scalar("SELECT COUNT(*) FROM channel_verification_failures WHERE owner_id=?", (owner_id,), 0)
        verification = (f"\nUncollected verification failures (all time): {fail_count}. Recent post IDs: " + ", ".join(str(f["message_id"]) for f in failures) + ". Repost missing files after restoring access.") if failures else ""
        if not row or not await repo.get_job(app.db, row["job_id"]):
            return await msg.answer("There is no channel batch yet. Post files in your connected channel." + verification)
        items = await repo.items_of(app.db, row["job_id"])
        state = "Collecting" if row["state"] == "open" else "Finished"
        rejected = await app.db.q("SELECT message_id,reason FROM channel_rejections WHERE job_id=? ORDER BY message_id DESC LIMIT 10", (row["job_id"],))
        count = await app.db.scalar("SELECT COUNT(*) FROM channel_rejections WHERE job_id=?", (row["job_id"],), 0)
        details = "".join(f"\n• post {r['message_id']}: {esc(r['reason'])}" for r in rejected)
        return await msg.answer(f"📥 {state}: {len(items)} files. Rejected posts: {count} (latest 10 shown).{details}{verification}\nUpload does not start automatically.", reply_markup=_keyboard(app, row["job_id"], user_id=owner_id, can_finish=row["state"] == "open"))


async def _channel_admin_check(app, channel_id: str, user_id: int) -> tuple[bool, str]:
    """Ask Telegram about both accounts; explain failures without bypassing checks."""
    try:
        me = await app.bot.get_me()
        bot_name = "@" + me.username if getattr(me, "username", None) else "this bot"
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, asyncio.TimeoutError):
        return False, "Telegram is temporarily unavailable while identifying the bot. Try again shortly."
    except Exception:
        log.warning("Channel setup: bot identity lookup failed")
        return False, "The service could not identify the bot. Contact the service administrator."
    try:
        chat = await app.bot.get_chat(int(channel_id))
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, asyncio.TimeoutError):
        return False, "Telegram is temporarily unavailable while checking the channel. Try again shortly."
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound):
        return False, f"Telegram cannot find or access this channel for {esc(bot_name)}. Check the numeric channel ID and confirm that exact bot is an administrator of the channel, then try again."
    except Exception:
        log.warning("Channel setup: channel lookup failed")
        return False, "The service could not check the channel. Contact the service administrator."
    if chat.type != "channel":
        return False, "This ID is not a Telegram channel. Use the channel ID, not the linked discussion group ID."
    try:
        bot_member = await app.bot.get_chat_member(int(channel_id), me.id)
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, asyncio.TimeoutError):
        return False, "Telegram is temporarily unavailable while checking the bot's membership. Try again shortly."
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound):
        return False, f"Telegram could not check {esc(bot_name)} in this channel. Confirm that exact bot is a channel administrator and try again."
    except Exception:
        log.warning("Channel setup: bot membership lookup failed")
        return False, "The service could not check the bot's channel membership. Contact the service administrator."
    if bot_member.status not in ("administrator", "creator"):
        return False, f"Telegram reports {esc(bot_name)} is not a channel administrator. Promote that exact bot, then retry."
    try:
        member = await app.bot.get_chat_member(int(channel_id), user_id)
    except (TelegramNetworkError, TelegramServerError, TelegramRetryAfter, asyncio.TimeoutError):
        return False, "Telegram is temporarily unavailable while checking your membership. Try again shortly."
    except (TelegramBadRequest, TelegramForbiddenError, TelegramNotFound):
        return False, "Telegram could not check your channel membership. Use the Telegram account that administers the channel and try again."
    except Exception:
        log.warning("Channel setup: user membership lookup failed")
        return False, "The service could not check your channel membership. Contact the service administrator."
    if member.status not in ("administrator", "creator"):
        return False, "Telegram reports your current account is not an administrator of this channel. Use the account listed in the channel's Administrators, then retry."
    return True, ""


async def _is_channel_admin(app, channel_id: str, user_id: int) -> bool:
    ok, _ = await _channel_admin_check(app, channel_id, user_id)
    return ok


async def _edit_settings(call: CallbackQuery, app, uid: int) -> None:
    try:
        await call.message.edit_text(await _settings_text(app, uid), reply_markup=_settings_keyboard())
    except TelegramBadRequest as error:
        if "message is not modified" not in str(error).lower():
            raise


def _settings_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="Set channel ID", callback_data="v1:autoset")],
        [InlineKeyboardButton(text="Batch status", callback_data="v1:autostatus"),
         InlineKeyboardButton(text="Finish batch", callback_data="v1:autofinish")],
        [InlineKeyboardButton(text="Discard empty batch", callback_data="v1:autoempty")],
        [InlineKeyboardButton(text="Disconnect channel", callback_data="v1:autounlink")],
        [InlineKeyboardButton(text="Back to settings", callback_data="v1:ui:settings")],
    ])


async def _settings_text(app, uid: int) -> str:
    row = await app.db.one("SELECT * FROM channel_bindings WHERE user_id=?", (uid,))
    channel = row["channel_id"] if row else (_channel_id(app) if uid == int(app.cfg.owner_id or 0) else "")
    channel_text = esc(channel) if channel else "Not connected"
    return (f"⚙️ <b>Channel automation</b>\nChannel: <code>{channel_text}</code>\n\n"
            "Add this bot as a channel administrator. You must also be an administrator. "
            "Set the numeric channel ID, then post your files there. Finish the batch before "
            "reviewing folders and starting it in your private Studio.\n\n"
            "Everyone who can post in your channel can add files to your batch; use a private, trusted channel.")


@router.callback_query(F.data == "v1:automation")
async def automation_settings(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    await _edit_settings(call, data["_app"], call.from_user.id)
    await call.answer()


@router.callback_query(F.data == "v1:autoset")
async def automation_set(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    data["_app"].channel_setup_pending.add(call.from_user.id)
    await call.message.answer("Send the numeric channel ID, for example <code>-1001234567890</code>, or forward an original post from the channel. Both you and the bot must be channel administrators. Forwarding must be allowed in the channel. Send /cancel_channel_setup to cancel.")
    await call.answer()


class PendingChannelId(BaseFilter):
    async def __call__(self, message: Message, _app=None, **kwargs) -> bool:
        if not (_app and message.from_user and message.chat.type == "private" and
                message.from_user.id in _app.channel_setup_pending):
            return False
        origin = message.forward_origin
        if origin and origin.type == "channel":
            return True
        return bool(message.text and not message.text.startswith("/"))


@router.message(PendingChannelId())
async def receive_channel_id(msg: Message, **data):
    app = data["_app"]
    uid = msg.from_user.id
    origin = msg.forward_origin
    value = str(origin.chat.id) if origin and origin.type == "channel" else (msg.text or "").strip()
    if not re.fullmatch(r"-100[0-9]{5,17}", value):
        return await msg.answer("Send a numeric channel ID beginning with -100, or forward a post from the channel. You can also send /cancel_channel_setup.")
    if value == _channel_id(app) and uid != int(app.cfg.owner_id or 0):
        return await msg.answer("That channel is reserved by the service. Use your own channel.")
    verified, reason = await _channel_admin_check(app, value, uid)
    if not verified:
        return await msg.answer("⚠️ Channel not connected. " + reason)
    async with app.channel_collect_lock:
        existing = await app.db.one("SELECT user_id FROM channel_bindings WHERE channel_id=?", (value,))
        old_open = await app.db.one("SELECT channel_id FROM channel_batches WHERE owner_id=? AND state='open'", (uid,))
        other_open = await app.db.one("SELECT owner_id FROM channel_batches WHERE channel_id=? AND state='open'", (value,))
        if existing and existing["user_id"] != uid:
            return await msg.answer("This channel is already connected to another account.")
        if (old_open and old_open["channel_id"] != value) or (other_open and other_open["owner_id"] != uid):
            return await msg.answer("An open batch prevents changing this channel. Finish that batch first.")
        await app.db.x("INSERT INTO channel_bindings(user_id,channel_id,created_at) VALUES(?,?,?) ON CONFLICT(user_id) DO UPDATE SET channel_id=excluded.channel_id,created_at=excluded.created_at", (uid, value, time.time()))
    app.channel_setup_pending.discard(uid)
    await msg.answer(f"✅ Channel <code>{esc(value)}</code> connected. Post files there, then finish and review the batch in Studio.", reply_markup=_settings_keyboard())


@router.message(Command("cancel_channel_setup"))
async def cancel_channel_setup(msg: Message, **data):
    if msg.from_user and msg.chat.type == "private":
        data["_app"].channel_setup_pending.discard(msg.from_user.id)
        await msg.answer("Channel setup cancelled.")


@router.callback_query(F.data == "v1:autounlink")
async def automation_unlink(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    app = data["_app"]
    uid = call.from_user.id
    async with app.channel_collect_lock:
        if await app.db.one("SELECT job_id FROM channel_batches WHERE owner_id=? AND state='open'", (uid,)):
            return await call.answer("Finish your open batch before disconnecting.", show_alert=True)
        row = await app.db.one("SELECT channel_id FROM channel_bindings WHERE user_id=?", (uid,))
        if not row and uid == int(app.cfg.owner_id or 0) and _channel_id(app):
            return await call.answer("This channel is set by the service environment. Remove COLLECT_CHANNEL_ID to disable it.", show_alert=True)
        await app.db.x("DELETE FROM channel_bindings WHERE user_id=?", (uid,))
    app.channel_setup_pending.discard(uid)
    await _edit_settings(call, app, uid)
    await call.answer("Channel disconnected.")


@router.callback_query(F.data == "v1:autostatus")
async def automation_status(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    # Callback message's sender is the bot, so use a small proxy to the private caller.
    app = data["_app"]
    uid = call.from_user.id
    row = await app.db.one("SELECT * FROM channel_batches WHERE owner_id=? ORDER BY updated_at DESC LIMIT 1", (uid,))
    if not row:
        await call.message.answer("No batch yet. Post files to your connected channel.")
    else:
        count = await app.db.scalar("SELECT COUNT(*) FROM job_items WHERE job_id=?", (row["job_id"],), 0)
        rejected = await app.db.scalar("SELECT COUNT(*) FROM channel_rejections WHERE job_id=?", (row["job_id"],), 0)
        await call.message.answer(f"📥 {esc(row['state'].title())}: {count} files; {rejected} rejected. Send /channel_status for rejection details.", reply_markup=_keyboard(app, row["job_id"], user_id=uid, can_finish=row["state"] == "open"))
    await call.answer()


@router.callback_query(F.data == "v1:autofinish")
async def automation_finish(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    app = data["_app"]
    uid = call.from_user.id
    row = await app.db.one("SELECT * FROM channel_batches WHERE owner_id=? AND state='open'", (uid,))
    if not row or not await _finish(app, row["job_id"], uid):
        return await call.answer("No open batch with accepted files.", show_alert=True)
    await call.message.answer("✅ Batch finished. Review your folder assignments in Studio before starting.", reply_markup=_keyboard(app, row["job_id"], user_id=uid, can_finish=False))
    await call.answer()


@router.callback_query(F.data == "v1:autoempty")
async def discard_empty_batch(call: CallbackQuery, **data):
    if not call.message or call.message.chat.type != "private":
        return await call.answer("Open settings in your private bot chat.", show_alert=True)
    app = data["_app"]
    uid = call.from_user.id
    async with app.channel_collect_lock:
        row = await app.db.one("SELECT * FROM channel_batches WHERE owner_id=? AND state='open'", (uid,))
        if not row:
            return await call.answer("There is no open batch.", show_alert=True)
        if await app.db.scalar("SELECT COUNT(*) FROM job_items WHERE job_id=?", (row["job_id"],), 0):
            return await call.answer("This batch contains files. Finish it and review in Studio instead.", show_alert=True)
        await repo.update_job(app.db, row["job_id"], status="cancelled")
        await app.db.x("UPDATE channel_batches SET state='finished',updated_at=? WHERE channel_id=? AND job_id=?", (time.time(), row["channel_id"], row["job_id"]))
    await call.answer("Empty batch discarded.")
    await call.message.answer("Empty batch discarded. Future posts create a new batch.")
