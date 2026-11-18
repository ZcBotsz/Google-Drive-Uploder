"""Every command, as a button, in one place.

The complaint was specific and repeated: "mujhe button chahiye", "pure commands
button mein aane chahiye". A command a user has to remember how to type is a
command they do not use, so the whole surface is reachable by tapping:

    /start  → the menu (every command below, grouped, 2–3 per row)
    /hello  → a short greeting that opens the same menu

Callback data is `m:<command>`. One handler in handlers/menu.py maps that back to
the command text and re-runs the real handler, so the button and the typed
command can never drift apart — there is one implementation, not two.
"""
from __future__ import annotations

from typing import Iterable

from aiogram.types import InlineKeyboardButton as B
from aiogram.types import InlineKeyboardMarkup as KM

# (command, emoji, label). Order is the order a new user needs things.
USER_MENU: list[tuple[str, str, str]] = [
    ("connect", "🔗", "Connect Drive"),
    ("account", "👤", "My account"),
    ("disconnect", "🚪", "Disconnect"),
    ("storage", "💾", "Drive space"),
    ("folders", "📂", "Folders"),
    ("setfolder", "🎯", "Pick folder"),
    ("naming", "✏️", "Naming"),
    ("sequence", "🔢", "Order"),
    ("queue", "⏳", "Queue"),
    ("history", "🕘", "History"),
    ("mystats", "📊", "My stats"),
    ("plan", "💎", "Plans"),
    ("copy", "📑", "Copy in Drive"),
    ("thumb_set", "🖼", "Set thumbnail"),
    ("thumb", "👁", "View thumbnail"),
    ("delthumb", "🗑", "Remove thumbnail"),
    ("settings", "⚙️", "Settings"),
    ("studio", "🌐", "Open Studio"),
    ("export", "📤", "Export links"),
    ("cancel", "❌", "Cancel job"),
    ("help", "❓", "Help"),
    ("hello", "👋", "Hello"),
]

# Owner/admin only — the same rows are hidden from ordinary users.
ADMIN_MENU: list[tuple[str, str, str]] = [
    ("admin", "🛡", "Admin panel"),
    ("stats", "📈", "Live stats"),
    ("users", "👥", "Users"),
    ("userinfo", "🔍", "User info"),
    ("queue", "📋", "All jobs"),
    ("locks", "🔒", "Locks"),
    ("lock", "🔒", "Lock user"),
    ("unlock", "🔓", "Unlock user"),
    ("addplan", "➕", "Add plan"),
    ("removeplan", "➖", "Remove plan"),
    ("expiring", "⏰", "Expiring"),
    ("addadmin", "➕", "Add admin"),
    ("removeadmin", "➖", "Remove admin"),
    ("broadcast", "📢", "Broadcast"),
    ("pause_all", "⏸", "Pause all"),
    ("resume_all", "▶️", "Resume all"),
    ("maintenance", "🔧", "Maintenance"),
    ("audit", "🧾", "Audit log"),
    ("system", "🖥", "System"),
]


def _rows(entries: Iterable[tuple[str, str, str]], per_row: int = 2) -> list[list[B]]:
    row: list[B] = []
    out: list[list[B]] = []
    for cmd, emoji, label in entries:
        row.append(B(text=f"{emoji} {label}", callback_data=f"m:{cmd}"))
        if len(row) == per_row:
            out.append(row)
            row = []
    if row:
        out.append(row)
    return out


def main_menu_kb(*, is_admin: bool = False, lang: str = "en") -> KM:
    """The whole command surface as buttons."""
    rows = _rows(USER_MENU, per_row=2)
    if is_admin:
        rows.append([B(text="━━━ 🛡 ADMIN ━━━", callback_data="m:noop")])
        rows.extend(_rows(ADMIN_MENU, per_row=2))
    return KM(inline_keyboard=rows)


def quick_kb(*, is_admin: bool = False) -> KM:
    """The four things people actually do, before the long list."""
    rows = [
        [B(text="🔗 Connect Drive", callback_data="m:connect"),
         B(text="💾 Drive space", callback_data="m:storage")],
        [B(text="⏳ Queue", callback_data="m:queue"),
         B(text="📊 My stats", callback_data="m:mystats")],
        [B(text="📋 All commands", callback_data="m:menu")],
    ]
    if is_admin:
        rows.insert(2, [B(text="🛡 Admin panel", callback_data="m:admin")])
    return KM(inline_keyboard=rows)


def telegram_command_list() -> list:
    """The ☰ list, built from the same table the buttons use.

    Telegram allows 100 commands; we have fewer, so everything fits.
    """
    from aiogram.types import BotCommand

    out = [BotCommand(command="start", description="Menu — all commands as buttons"),
           BotCommand(command="hello", description="Say hello")]
    for cmd, _emoji, label in USER_MENU:
        if cmd in ("hello",):
            continue
        out.append(BotCommand(command=cmd, description=label[:40]))
    return out[:100]
