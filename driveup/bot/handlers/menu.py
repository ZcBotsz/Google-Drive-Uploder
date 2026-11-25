"""Inline app navigation and explicit adapters to existing message handlers."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message

from ...config import BRAND_NAME
from ...db import repo
from ...strings import esc, sc
from ..keyboards import cb
from ..menus import (ADMIN_GROUPS, account_menu_kb, admin_group_kb, admin_menu_kb,
                     drive_menu_kb, main_menu_kb, more_menu_kb, settings_menu_kb,
                     uploads_menu_kb)
from ..ui.buttons import plain, primary

log = logging.getLogger("driveup.handlers.menu")
router = Router(name="menu")


async def home_text(app, user_id: int) -> tuple[str, bool]:
    acc = await repo.get_drive_account(app.db, user_id)
    plan = await repo.effective_plan(app.db, user_id)
    glob = await repo.get_global(app.db)
    connected = bool(acc and not acc.get("needs_reconnect"))
    drive_line = (f"🔗 <b>{sc('DRIVE')}:</b> {esc(acc.get('email') or sc('CONNECTED'))}"
                  if connected else f"🔌 <b>{sc('DRIVE')}:</b> {sc('NOT CONNECTED')}")
    price = glob.get("plan_price_text") or app.cfg.plan_price_text
    support = glob.get("support_contact") or app.cfg.support_contact
    title = glob.get("brand_name") or BRAND_NAME
    text = (
        f"🚀 <b>{sc(title)}</b>\n"
        f"<i>{sc('YOUR DRIVE · YOUR FILES · YOUR CONTROL')}</i>\n"
        "────────────────────────\n"
        f"{drive_line}\n"
        f"⭐ <b>{sc('PLAN')}:</b> {esc(plan.get('name') or plan.get('id'))}"
        f" · {esc(price)}\n\n"
        f"{sc('SEND FILES OR SUPPORTED LINKS HERE, OR OPEN STUDIO TO UPLOAD IN YOUR BROWSER.')}\n"
        f"💬 {sc('SUPPORT')}: {esc(support)}"
    )
    return text, connected


async def is_admin_user(app, user_id: int, data: dict) -> bool:
    if app.cfg.owner_id and user_id == app.cfg.owner_id:
        return True
    if data.get("role") in ("owner", "admin", "support"):
        return True
    return await repo.is_admin(app.db, user_id, app.cfg.owner_id)


def _menu_markup(*, is_admin: bool, connected: bool) -> InlineKeyboardMarkup:
    return main_menu_kb(is_admin=is_admin, is_connected=connected)


def _remember(app, call: CallbackQuery, section: str) -> None:
    table = getattr(app, "menu_context", None)
    if table is None:
        table = app.menu_context = {}
    table[(call.message.chat.id, call.message.message_id)] = section


@router.message(Command("hello"))
async def cmd_hello(msg: Message, **data):
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    from ..ui import emoji as animated
    text, connected = await home_text(app, msg.from_user.id)
    text = animated.apply(text, app.cfg, await repo.get_global(app.db))
    await msg.answer(text, reply_markup=_menu_markup(is_admin=admin, connected=connected))


@router.message(Command("menu"))
async def cmd_menu(msg: Message, **data):
    app = data["_app"]
    admin = await is_admin_user(app, msg.from_user.id, data)
    from ..ui import emoji as animated
    text, connected = await home_text(app, msg.from_user.id)
    text = animated.apply(text, app.cfg, await repo.get_global(app.db))
    await msg.answer(text, reply_markup=_menu_markup(is_admin=admin, connected=connected))


@router.callback_query(F.data == "m:noop")
@router.callback_query(F.data == "v1:noop")
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
@router.callback_query(F.data == "v1:ui:home")
async def cb_home(call: CallbackQuery, **data):
    app = data["_app"]
    admin = await is_admin_user(app, call.from_user.id, data)
    text, connected = await home_text(app, call.from_user.id)
    from ..ui import emoji as animated
    text = animated.apply(text, app.cfg, await repo.get_global(app.db))
    await call.answer()
    await _edit_menu(call, text, _menu_markup(is_admin=admin, connected=connected))
    _remember(app, call, "home")


async def _admin_allowed(call: CallbackQuery, app, data: dict) -> bool:
    if await is_admin_user(app, call.from_user.id, data):
        return True
    await call.answer("This section is for administrators.", show_alert=True)
    return False


@router.callback_query(F.data.startswith("ui:"))
@router.callback_query(F.data.startswith("v1:ui:"))
async def cb_section(call: CallbackQuery, **data):
    app = data["_app"]
    raw = call.data or ""
    section = raw.split(":")[-1]
    admin = await is_admin_user(app, call.from_user.id, data)
    if section == "settings":
        user = await repo.get_user(app.db, call.from_user.id)
        st = (user or {}).get("settings", {})
        text = (f"⚙️ <b>{sc('SETTINGS')}</b>\n<i>{sc('REVIEW OR CHANGE A PREFERENCE')}</i>\n"
                "────────────────────────\n"
                f"✏️ {sc('NAMING')}: <code>{esc((st.get('naming') or {}).get('template', '{original}.{ext}'))}</code>\n"
                f"🔢 {sc('ORDER')}: {esc(st.get('sequence_mode', 'telegram_order'))}\n"
                f"📁 {sc('DEFAULT FOLDER')}: {esc((st.get('default_folder') or {}).get('path', 'My Drive'))}")
        screen = (text, settings_menu_kb(st))
    else:
        screens = {
            "more": (f"<b>{sc('DRIVEUP TOOLS')}</b>\n{sc('CHOOSE A SECTION')}", more_menu_kb(is_admin=admin)),
            "drive": (f"📁 <b>{sc('MY DRIVE')}</b>\n<i>{sc('ACCOUNT, STORAGE, FOLDERS AND DESTINATION')}</i>", drive_menu_kb()),
            "uploads": (f"📤 <b>{sc('UPLOAD')}</b>\n<i>{sc('SEND A FILE OR USE THE WEB STUDIO')}</i>", uploads_menu_kb(is_admin=admin)),
            "account": (f"⭐ <b>{sc('PLAN & USAGE')}</b>\n<i>{sc('ACCOUNT DETAILS, QUOTA AND STATS')}</i>", account_menu_kb()),
            "admin": (f"🔒 <b>{sc('ADMIN')}</b>\n<i>{sc('PROTECTED SERVICE CONTROLS')}</i>", admin_menu_kb()),
        }
        group_key = section.removeprefix("admin_")
        if section.startswith("admin_") and group_key in ADMIN_GROUPS:
            if not await _admin_allowed(call, app, data):
                return
            group_title = ADMIN_GROUPS[group_key][0]
            screens[section] = (f"🔒 <b>{sc(group_title)}</b>\n<i>{sc('ADMIN ACTIONS · ACCESS CHECKED')}</i>",
                                admin_group_kb(group_key))
        if section == "admin" and not admin:
            return await _admin_allowed(call, app, data)
        screen = screens.get(section)
    if not screen:
        return await call.answer()
    await call.answer()
    from ..ui import emoji as animated
    text = animated.apply(screen[0], app.cfg, await repo.get_global(app.db))
    await _edit_menu(call, text, screen[1])
    _remember(app, call, section)


class _MenuMessageProxy:
    """Adapt a classic handler reply into an in-place screen with parent navigation."""
    is_menu_proxy = True

    def __init__(self, call: CallbackQuery, parent: str = "home"):
        self._call = call
        self._message = call.message
        self._parent = parent

    def __getattr__(self, name):
        return getattr(self._message, name)

    async def answer(self, text=None, *args, **kwargs):
        if text is None:
            text = " "
        original = kwargs.pop("reply_markup", None)
        rows = [list(row) for row in original.inline_keyboard] if isinstance(original, InlineKeyboardMarkup) else []
        if self._parent != "home":
            parent_callback = cb("ui", self._parent)
            rows.append([plain("⬅ Back", callback_data=parent_callback),
                         plain("⌂ Home", callback_data="v1:ui:home")])
        else:
            rows.append([plain("⌂ Home", callback_data="v1:ui:home")])
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        allowed = {k: kwargs[k] for k in ("parse_mode", "entities", "link_preview_options") if k in kwargs}
        try:
            result = await self._message.edit_text(str(text), reply_markup=markup, **allowed)
            return result
        except TelegramBadRequest as e:
            if "message is not modified" in str(e).lower():
                return self._message
            raise


# This public registry avoids introspecting aiogram's private filter internals.
def _command_registry():
    from . import admin as A, start as S, upload as U, thumb as T
    return {
        "admin": A.cmd_admin, "stats": A.cmd_stats, "users": A.cmd_users,
        "userinfo": A.cmd_userinfo, "lock": A.cmd_lock, "unlock": A.cmd_unlock,
        "locks": A.cmd_locks, "addplan": A.cmd_addplan, "removeplan": A.cmd_removeplan,
        "expiring": A.cmd_expiring, "pause_all": A.cmd_pause_all,
        "resume_all": A.cmd_resume_all, "maintenance": A.cmd_maintenance,
        "broadcast": A.cmd_broadcast, "audit": A.cmd_audit, "system": A.cmd_system,
        "addadmin": A.cmd_addadmin, "removeadmin": A.cmd_removeadmin,
        "copy": S.cmd_copy, "protect": S.cmd_protect, "storage": S.cmd_storage, "help": S.cmd_help,
        "connect": S.cmd_connect, "disconnect": S.cmd_disconnect,
        "account": S.cmd_account, "folders": S.cmd_folders,
        "setfolder": S.cmd_setfolder, "naming": S.cmd_naming,
        "sequence": S.cmd_sequence, "settings": S.cmd_settings,
        "studio": S.cmd_studio, "export": S.cmd_export,
        "queue": U.cmd_queue, "cancel": U.cmd_cancel, "history": U.cmd_history,
        "mystats": U.cmd_mystats, "plan": U.cmd_plan,
        "thumb": T.cmd_thumb, "thumb_set": T.cmd_thumb_set, "delthumb": T.cmd_del_thumb,
    }


@router.callback_query(F.data.startswith("m:"))
@router.callback_query(F.data.startswith("v1:menu:"))
async def cb_command(call: CallbackQuery, **data):
    app = data["_app"]
    raw = call.data or ""
    cmd = raw[2:].strip() if raw.startswith("m:") else raw.split(":", 2)[-1].strip()
    admin_only = {"admin", "stats", "users", "userinfo", "locks", "lock", "unlock",
                  "addplan", "removeplan", "expiring", "addadmin", "removeadmin",
                  "broadcast", "pause_all", "resume_all", "maintenance", "audit",
                  "system", "export"}
    if cmd in admin_only and not await is_admin_user(app, call.from_user.id, data):
        return await call.answer("This action is for administrators.", show_alert=True)
    handler = _command_registry().get(cmd)
    if not handler:
        return await call.answer("That action is not available.", show_alert=True)
    await call.answer()
    context = getattr(app, "menu_context", {}).get((call.message.chat.id, call.message.message_id), "home")
    shadow = _MenuMessageProxy(call, parent=context)
    usage = {
        "userinfo": "/userinfo <user id or @username>",
        "lock": "/lock <user id> <minutes> <reason>",
        "unlock": "/unlock <user id>",
        "addplan": "/addplan <user id> <days> [max]",
        "removeplan": "/removeplan <user id>",
        "addadmin": "/addadmin <user id>",
        "removeadmin": "/removeadmin <user id>",
        "broadcast": "/broadcast <message>",
    }
    shadow.text = usage.get(cmd, f"/{cmd}")
    shadow.caption = None
    shadow.entities = None
    shadow.caption_entities = None
    shadow.from_user = call.from_user
    try:
        return await handler(shadow, **data)
    except Exception as e:  # noqa: BLE001
        log.exception("menu command %s failed: %s", cmd, e)
        try:
            return await shadow.answer(f"<b>{sc('ACTION UNAVAILABLE')}</b>\n{esc(str(e)[:160])}")
        except Exception:
            return None
