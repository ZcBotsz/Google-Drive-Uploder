"""Compact, phone-first command menus.

The main screen is intentionally limited to three choices. Secondary sections
expose the existing commands without requiring users to remember syntax.
"""
from __future__ import annotations

from typing import Iterable

from aiogram.types import InlineKeyboardMarkup as KM

from .ui.buttons import danger, plain, primary, success

# (command, label, icon). The icon is Telegram's animated button icon only;
# labels contain no decorative static emoji fallback.
USER_MENU: list[tuple[str, str, str]] = [
    ("connect", "Connect Drive", "🔗"),
    ("account", "Google Account", "👤"),
    ("disconnect", "Disconnect Drive", "❌"),
    ("storage", "Drive Storage", "📦"),
    ("folders", "Browse Folders", "📁"),
    ("setfolder", "Default Folder", "📂"),
    ("naming", "File Naming", "✏️"),
    ("sequence", "File Order", "🔢"),
    ("queue", "Upload Queue", "⏳"),
    ("history", "Upload History", "📊"),
    ("mystats", "My Stats", "📊"),
    ("plan", "Max Plan", "⭐"),
    ("copy", "Copy in Drive", "📁"),
    ("thumb_set", "Set Thumbnail", "🖼"),
    ("thumb", "View Thumbnail", "👁"),
    ("delthumb", "Remove Thumbnail", "❌"),
    ("settings", "Preferences", "⚙️"),
    ("studio", "Upload Studio", "🌐"),
    ("export", "Export Report", "📊"),
    ("cancel", "Cancel Upload", "❌"),
    ("help", "How It Works", "❓"),
]

ADMIN_MENU: list[tuple[str, str, str]] = [
    ("admin", "Admin Panel", "🔒"),
    ("stats", "Service Stats", "📊"),
    ("users", "Manage Users", "👤"),
    ("userinfo", "User Details", "🔎"),
    ("locks", "Active Locks", "🔒"),
    ("lock", "Lock User", "🔒"),
    ("unlock", "Unlock User", "✅"),
    ("addplan", "Grant Max Plan", "⭐"),
    ("removeplan", "Remove Plan", "❌"),
    ("expiring", "Expiring Plans", "⏳"),
    ("addadmin", "Add Admin", "👤"),
    ("removeadmin", "Remove Admin", "❌"),
    ("broadcast", "Broadcast", "📣"),
    ("pause_all", "Pause All Jobs", "⏸"),
    ("resume_all", "Resume All Jobs", "✅"),
    ("maintenance", "Maintenance", "⚙️"),
    ("audit", "Audit Log", "📊"),
    ("system", "System Status", "⚙️"),
]


def _button(cmd: str, label: str, icon: str = "🌐"):
    """Use custom emoji icons only; if Telegram cannot render one, text stays clean."""
    if cmd in {"disconnect", "cancel", "delthumb", "removeplan", "removeadmin", "lock"}:
        return danger(label, icon=icon, callback_data=f"m:{cmd}")
    if cmd in {"connect", "studio", "resume_all", "unlock", "addplan"}:
        return success(label, icon=icon, callback_data=f"m:{cmd}")
    return primary(label, icon=icon, callback_data=f"m:{cmd}")


def _rows(entries: Iterable[tuple[str, str, str]], per_row: int = 2) -> list[list]:
    out: list[list] = []
    row = []
    for cmd, label, icon in entries:
        row.append(_button(cmd, label, icon))
        if len(row) == per_row:
            out.append(row)
            row = []
    if row:
        out.append(row)
    return out


def _nav(label: str, callback: str = "ui:home"):
    return [primary(label, icon="🌐", callback_data=callback)]


def main_menu_kb(*, is_admin: bool = False, lang: str = "en") -> KM:
    """Two essential actions plus one discovery button; owner tools are nested."""
    rows = [
        [success("Connect Drive", icon="🔗", callback_data="m:connect"),
         primary("Upload Studio", icon="🌐", callback_data="m:studio")],
        [primary("More", icon="📊", callback_data="ui:more")],
    ]
    if is_admin:
        rows.append([primary("Admin Tools", icon="⚙️", callback_data="ui:admin")])
    return KM(inline_keyboard=rows)


def more_menu_kb(*, is_admin: bool = False) -> KM:
    rows = [
        [primary("Google Drive", icon="📁", callback_data="ui:drive"),
         primary("Uploads & Tools", icon="📤", callback_data="ui:uploads")],
        [primary("Account & Stats", icon="👤", callback_data="ui:account"),
         success("Max Plan", icon="⭐", callback_data="m:plan")],
        [primary("How It Works", icon="❓", callback_data="m:help")],
        [plain("Contact Admin", url="https://t.me/ZCQXx")],
        _nav("Back to Main Menu"),
    ]
    if is_admin:
        rows.insert(3, [primary("Admin Tools", icon="⚙️", callback_data="ui:admin")])
    return KM(inline_keyboard=rows)


def drive_menu_kb() -> KM:
    entries = [e for e in USER_MENU if e[0] in {
        "connect", "account", "storage", "folders", "setfolder", "copy", "disconnect"}]
    return KM(inline_keyboard=_rows(entries, 2) + [_nav("Back to More", "ui:more")])


def uploads_menu_kb(*, is_admin: bool = False) -> KM:
    commands = {"studio", "queue", "history", "naming", "sequence", "cancel",
                "thumb_set", "thumb", "delthumb"}
    if is_admin:
        commands.add("export")
    entries = [e for e in USER_MENU if e[0] in commands]
    return KM(inline_keyboard=_rows(entries, 2) + [_nav("Back to More", "ui:more")])


def account_menu_kb() -> KM:
    entries = [e for e in USER_MENU if e[0] in {"account", "mystats", "plan", "settings"}]
    return KM(inline_keyboard=_rows(entries, 2) + [_nav("Back to More", "ui:more")])


def admin_menu_kb() -> KM:
    return KM(inline_keyboard=_rows(ADMIN_MENU, 2) + [_nav("Back to Main Menu")])


def quick_kb(*, is_admin: bool = False) -> KM:
    """Compatibility name used by /start: the same compact main screen."""
    return main_menu_kb(is_admin=is_admin)


def telegram_command_list() -> list:
    """Bot command metadata, retaining typed-command compatibility."""
    from aiogram.types import BotCommand
    out = [BotCommand(command="start", description="Open the ZC DriveUp menu"),
           BotCommand(command="menu", description="Open the button menu"),
           BotCommand(command="help", description="How to use DriveUp")]
    for cmd, label, _icon in USER_MENU:
        if cmd in {"help"}:
            continue
        out.append(BotCommand(command=cmd, description=label[:40]))
    return out[:100]
