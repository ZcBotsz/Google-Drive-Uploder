"""`m:<command>` buttons — run the real handler, do not reimplement it.

There is exactly one implementation of every command. A button press turns
`m:storage` into the text "/storage" and calls the same handler a typed message
would, so the two can never disagree about what a command does.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

from ...db import repo
from ..menus import main_menu_kb, quick_kb

log = logging.getLogger("driveup.handlers.menu")
router = Router(name="menu")

MENU_HEADER = ("<b>📋 All commands</b>\n"
               "Tap any button to open a command.")


async def is_admin_user(app, user_id: int, data: dict) -> bool:
    if app.cfg.owner_id and user_id == app.cfg.owner_id:
        return True
    if data.get("role") in ("owner", "admin", "support"):
        return True
    return await repo.is_admin(app.db, user_id, app.cfg.owner_id)


@router.message(Command("hello"))
async def cmd_hello(msg: Message, **data):
    """A small greeting that also opens the menu."""
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    name = (msg.from_user.first_name or "there").strip()
    await msg.answer(
        f"👋 <b>Hello, {name}!</b>\n\n"
        "I'm your Google Drive uploader.\n"
        "📤 Send a file or 🔗 paste a direct link to upload it to Drive.\n\n"
        "<i>Choose a command below.</i>", 
        reply_markup=quick_kb(is_admin=admin))
    if await app_has_error_log(app):
        # logged once per session, not per message
        log.debug("menu opened with recent errors in the log")


async def app_has_error_log(app) -> bool:
    try:
        return bool(await repo.recent_errors(app.db, limit=1))
    except Exception:                                    # noqa: BLE001
        return False


@router.message(Command("menu"))
async def cmd_menu(msg: Message, **data):
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    await msg.answer(MENU_HEADER, reply_markup=main_menu_kb(is_admin=admin))


@router.callback_query(F.data == "m:noop")
async def cb_noop(call: CallbackQuery, **data):
    await call.answer()


@router.callback_query(F.data == "m:menu")
async def cb_menu(call: CallbackQuery, **data):
    app = data["_app"]
    admin = await is_admin_user(app, call.from_user.id, data)
    await call.answer()
    try:
        await call.message.edit_text(MENU_HEADER, reply_markup=main_menu_kb(is_admin=admin))
    except Exception:                                    # noqa: BLE001 — unchanged text
        await app.say(call.message.chat.id, MENU_HEADER,
                      reply_markup=main_menu_kb(is_admin=admin))


@router.callback_query(F.data.startswith("m:"))
async def cb_command(call: CallbackQuery, **data):
    """Turn the tap into the same message a typed command would produce."""
    app = data["_app"]
    cmd = (call.data or "")[2:].strip()
    if not cmd:
        return await call.answer()

    admin_only = {"admin", "stats", "users", "userinfo", "locks", "lock", "unlock",
                  "addplan", "removeplan", "expiring", "addadmin", "removeadmin",
                  "broadcast", "pause_all", "resume_all", "maintenance", "audit",
                  "system"}
    if cmd in admin_only and not await is_admin_user(app, call.from_user.id, data):
        return await call.answer("Sirf admin ke liye.", show_alert=True)

    await call.answer()
    # A command that needs an argument opens its own prompt; the plain text is
    # what the user would have typed, so the handler answers exactly as usual.
    prompts = {"lock": "/lock <user_id> <minutes> <reason>",
               "unlock": "/unlock <user_id>"}
    text = prompts.get(cmd, f"/{cmd}")

    # Feed a synthetic message through the real handlers. `feed_update` would
    # re-enter the middleware and re-count quota, so the handler is called
    # directly with the same data the middleware already built for this update.
    from . import admin as admin_h
    from . import start as start_h
    from . import upload as upload_h
    from . import thumb as thumb_h

    shadow = _shadow_message(call, text)
    for module in (thumb_h, start_h, upload_h, admin_h):
        for observer in module.router.message.handlers:
            filt = _command_filter(observer)
            if filt and filt == cmd:
                try:
                    return await observer.callback(shadow, **data)
                except Exception as e:                   # noqa: BLE001
                    log.exception("menu command %s failed: %s", cmd, e)
                    return await app.say(call.message.chat.id,
                                         f"⚠️ <code>{cmd}</code> failed: {e}")


def _command_filter(observer):
    """The single command a message handler is bound to, or ''."""
    for f in getattr(observer, "filters", []) or []:
        commands = getattr(getattr(f, "callback", None), "commands", None)
        if commands:
            first = str(list(commands)[0]).lower()
            return first
    return ""


def _shadow_message(call: CallbackQuery, text: str) -> Message:
    """A Message that looks like the user typed `text` in this chat.

    aiogram's models are frozen, so the fields are replaced through
    `model_copy(update=...)` — assigning to them raises. Chat, user and message
    id stay real; only the body is synthesised, and the caption fields are
    cleared so a message never carries both text and caption.
    """
    return call.message.model_copy(update={
        "text": text,
        "caption": None,
        "entities": None,
        "caption_entities": None,
        "from_user": call.from_user,
    })
