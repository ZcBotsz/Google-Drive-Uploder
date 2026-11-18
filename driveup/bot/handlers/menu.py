"""Inline navigation and command adapters.

Menus edit the current message so actions feel like a compact app rather than
an expanding stack of command replies. The original typed commands remain
available and continue to call the same handlers.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ...db import repo
from ...strings import sc
from ..menus import (ADMIN_MENU, account_menu_kb, admin_menu_kb, drive_menu_kb,
                     main_menu_kb, more_menu_kb, uploads_menu_kb)
from ..ui.buttons import plain, primary

log = logging.getLogger("driveup.handlers.menu")
router = Router(name="menu")
MAIN_TEXT = (
    f"<b>🚀 {sc('ZC DRIVEUP')} · {sc('MAIN MENU')}</b>\n\n"
    "Your Telegram-to-Drive workspace. Connect once, send files or links, "
    "then choose the folder and upload options.\n\n"
    "Pick a button below to continue."
)
MORE_TEXT = f"<b>{sc('DRIVEUP TOOLS')}</b>\nChoose a section. The menu stays in this message."


async def is_admin_user(app, user_id: int, data: dict) -> bool:
    if app.cfg.owner_id and user_id == app.cfg.owner_id:
        return True
    if data.get("role") in ("owner", "admin", "support"):
        return True
    return await repo.is_admin(app.db, user_id, app.cfg.owner_id)


@router.message(Command("hello"))
async def cmd_hello(msg: Message, **data):
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    from ..ui import emoji as animated
    text = (
        f"👋 <b>Hello — {sc('WELCOME TO ZC DRIVEUP')}</b>\n\n"
        "Send a Telegram file or direct link. I’ll guide you through the destination, "
        "naming and upload — use the menu buttons for the rest."
    )
    text = animated.apply(text, app.cfg, await repo.get_global(app.db))
    await msg.answer(text, reply_markup=main_menu_kb(is_admin=admin))


@router.message(Command("menu"))
async def cmd_menu(msg: Message, **data):
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    from ..ui import emoji as animated
    text = animated.apply(MAIN_TEXT, app.cfg, await repo.get_global(app.db))
    await msg.answer(text, reply_markup=main_menu_kb(is_admin=admin))


@router.callback_query(F.data == "m:noop")
async def cb_noop(call: CallbackQuery, **data):
    await call.answer()


async def _edit_menu(call: CallbackQuery, text: str, markup: InlineKeyboardMarkup):
    try:
        await call.message.edit_text(text, reply_markup=markup)
    except TelegramBadRequest as e:
        if "message is not modified" not in str(e).lower():
            log.debug("menu edit failed: %s", e)


@router.callback_query(F.data == "m:menu")
@router.callback_query(F.data == "ui:home")
async def cb_home(call: CallbackQuery, **data):
    app = data["_app"]
    admin = await is_admin_user(app, call.from_user.id, data)
    await call.answer()
    from ..ui import emoji as animated
    text = animated.apply(MAIN_TEXT, app.cfg, await repo.get_global(app.db))
    await _edit_menu(call, text, main_menu_kb(is_admin=admin))


@router.callback_query(F.data.startswith("ui:"))
async def cb_section(call: CallbackQuery, **data):
    app = data["_app"]
    section = (call.data or "").split(":", 1)[1]
    admin = await is_admin_user(app, call.from_user.id, data)
    screens = {
        "more": (MORE_TEXT, more_menu_kb(is_admin=admin)),
        "drive": (f"<b>{sc('GOOGLE DRIVE')}</b>\nConnect your account, check space, browse folders or change the destination.", drive_menu_kb()),
        "uploads": (f"<b>{sc('UPLOADS & TOOLS')}</b>\nManage your Studio, active jobs, names, ordering and history.", uploads_menu_kb(is_admin=admin)),
        "account": (f"<b>{sc('ACCOUNT & STATS')}</b>\nReview your connected account, usage and Max membership.", account_menu_kb()),
    }
    if section == "admin":
        if not admin:
            return await call.answer("This section is for administrators.", show_alert=True)
        screens["admin"] = (f"<b>{sc('ADMIN CONTROL CENTER')}</b>\nChoose an admin action. Sensitive actions remain owner-checked.", admin_menu_kb())
    screen = screens.get(section)
    if not screen:
        return await call.answer()
    await call.answer()
    from ..ui import emoji as animated
    text = animated.apply(screen[0], app.cfg, await repo.get_global(app.db))
    await _edit_menu(call, text, screen[1])


class _MenuMessageProxy:
    """Make normal handler replies edit the tapped menu message instead."""
    is_menu_proxy = True

    def __init__(self, call: CallbackQuery):
        self._call = call
        self._message = call.message

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def answer(self, text=None, *args, **kwargs):
        if text is None:
            text = " "
        markup = kwargs.pop("reply_markup", None)
        if isinstance(markup, InlineKeyboardMarkup):
            rows = [list(row) for row in markup.inline_keyboard]
        else:
            rows = []
        # Preserve handler-specific buttons and keep navigation available.
        if not any(any(getattr(b, "callback_data", "") == "ui:home" for b in row) for row in rows):
            rows.append([primary("Back to Main Menu", icon="🌐", callback_data="ui:home")])
        if rows:
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
        allowed = {k: kwargs[k] for k in ("parse_mode", "entities", "link_preview_options") if k in kwargs}
        try:
            return await self._message.edit_text(str(text), reply_markup=markup, **allowed)
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return self._message
            raise


@router.callback_query(F.data.startswith("m:"))
async def cb_command(call: CallbackQuery, **data):
    """Run the existing command handler, editing the menu message in place."""
    app = data["_app"]
    cmd = (call.data or "")[2:].strip()
    if not cmd:
        return await call.answer()
    admin_only = {"admin", "stats", "users", "userinfo", "locks", "lock", "unlock",
                  "addplan", "removeplan", "expiring", "addadmin", "removeadmin",
                  "broadcast", "pause_all", "resume_all", "maintenance", "audit",
                  "system", "export"}
    if cmd in admin_only and not await is_admin_user(app, call.from_user.id, data):
        return await call.answer("This action is for administrators.", show_alert=True)
    await call.answer()
    prompts = {"lock": "/lock <user_id> <minutes> <reason>",
               "unlock": "/unlock <user_id>"}
    text = prompts.get(cmd, f"/{cmd}")
    from . import admin as admin_h
    from . import start as start_h
    from . import upload as upload_h
    from . import thumb as thumb_h
    shadow = _MenuMessageProxy(call)
    # Keep a text-shaped message so handlers can read the synthetic command,
    # while answer() is intercepted to edit the original menu message.
    shadow.text = text
    shadow.caption = None
    shadow.entities = None
    shadow.caption_entities = None
    shadow.from_user = call.from_user
    for module in (thumb_h, start_h, upload_h, admin_h):
        for observer in module.router.message.handlers:
            filt = _command_filter(observer)
            if filt and filt == cmd:
                try:
                    return await observer.callback(shadow, **data)
                except Exception as e:  # noqa: BLE001
                    log.exception("menu command %s failed: %s", cmd, e)
                    try:
                        return await shadow.answer(
                            f"<b>Could not open {sc(cmd)}.</b> Please try again or use /{cmd}.")
                    except Exception:
                        return None


def _command_filter(observer):
    for f in getattr(observer, "filters", []) or []:
        commands = getattr(getattr(f, "callback", None), "commands", None)
        if commands:
            return str(list(commands)[0]).lower()
    return ""
