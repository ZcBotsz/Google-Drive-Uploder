"""Phone-first menu maps for the ZC Drive Upload bot."""
from __future__ import annotations

from typing import Iterable

from aiogram.types import InlineKeyboardMarkup as KM

from .keyboards import cb
from .ui.buttons import danger, plain, primary, success

# (command, label, icon); user-facing command buttons only.
USER_MENU: list[tuple[str, str, str]] = [
    ("connect", "Connect Google Drive", "🔗"),
    ("account", "Google Account", "👤"),
    ("disconnect", "Disconnect Drive", "❌"),
    ("storage", "Drive Storage", "📦"),
    ("folders", "Browse Folders", "📁"),
    ("setfolder", "Default Folder", "📂"),
    ("naming", "File Naming", "✏️"),
    ("sequence", "File Order", "🔢"),
    ("queue", "Upload Queue", "⏳"),
    ("history", "Upload History", "📄"),
    ("mystats", "My Stats", "📊"),
    ("plan", "Plan & Usage", "⭐"),
    ("copy", "Copy in Drive", "📁"),
    ("thumb_set", "Set Thumbnail", "🖼"),
    ("thumb", "View Thumbnail", "👁"),
    ("delthumb", "Remove Thumbnail", "❌"),
    ("settings", "Preferences", "⚙️"),
    ("studio", "Upload Studio", "🌐"),
    ("cancel", "Cancel Upload", "⏹"),
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
    ("audit", "Audit Log", "📄"),
    ("system", "System Status", "🛠"),
    ("export", "Export Report", "📥"),
]

ADMIN_GROUPS: dict[str, tuple[str, str, ...]] = {
    "users": ("Users", ("users", "userinfo", "addadmin", "removeadmin")),
    "locks": ("Locks & Plans", ("locks", "lock", "unlock", "addplan", "removeplan", "expiring")),
    "ops": ("Broadcast & Operations", ("broadcast", "pause_all", "resume_all", "maintenance")),
    "system": ("System & Audit", ("stats", "system", "audit", "export")),
}


def _button(cmd: str, label: str, icon: str = "🌐"):
    """Assign variants by action identity, never by guessing from the label."""
    callback = cb("menu", cmd)
    if cmd in {"disconnect", "cancel", "delthumb", "removeplan", "removeadmin", "lock"}:
        return danger(label, icon=icon, callback_data=callback)
    if cmd in {"connect", "studio", "resume_all", "unlock", "addplan"}:
        return success(label, icon=icon, callback_data=callback)
    return primary(label, icon=icon, callback_data=callback)


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


def _nav(label: str, callback: str = "v1:ui:home"):
    return [plain(label, callback_data=callback)]


def _ui(section: str) -> str:
    return cb("ui", section)


def main_menu_kb(*, is_admin: bool = False, is_connected: bool = False,
                 lang: str = "en") -> KM:
    """State-aware home: direct paths to upload, Drive, settings, plan and help."""
    if is_connected:
        rows = [
            [primary("Upload", icon="📤", callback_data=_ui("uploads")),
             primary("Upload Studio", icon="🌐", callback_data=cb("menu", "studio"))],
        ]
    else:
        rows = [[success("Connect Google Drive", icon="🔗", callback_data=cb("menu", "connect"))]]
    rows.extend([
        [primary("My Drive", icon="📁", callback_data=_ui("drive")),
         primary("Settings", icon="⚙️", callback_data=_ui("settings"))],
        [primary("Plan & Usage", icon="⭐", callback_data=cb("menu", "plan")),
         primary("Help", icon="❓", callback_data=cb("menu", "help"))],
        [primary("More tools", icon="☰", callback_data=_ui("more"))],
    ])
    if is_admin:
        rows.append([primary("Admin", icon="🔒", callback_data=_ui("admin"))])
    return KM(inline_keyboard=rows)


def more_menu_kb(*, is_admin: bool = False) -> KM:
    """Compatibility screen for older saved menu messages."""
    rows = [
        [primary("Google Drive", icon="📁", callback_data=_ui("drive")),
         primary("Uploads & Tools", icon="📤", callback_data=_ui("uploads"))],
        [primary("Account & Stats", icon="👤", callback_data=_ui("account")),
         primary("Settings", icon="⚙️", callback_data=_ui("settings"))],
        [primary("Plan & Usage", icon="⭐", callback_data=cb("menu", "plan")),
         primary("How It Works", icon="❓", callback_data=cb("menu", "help"))],
        [plain("Contact Support", url="https://t.me/ZCQXx")],
        _nav("⌂ Home"),
    ]
    if is_admin:
        rows.insert(3, [primary("Admin", icon="🔒", callback_data=_ui("admin"))])
    return KM(inline_keyboard=rows)


def drive_menu_kb() -> KM:
    entries = [e for e in USER_MENU if e[0] in {
        "connect", "account", "storage", "folders", "setfolder", "copy", "disconnect"}]
    return KM(inline_keyboard=_rows(entries, 2) + [_nav("⌂ Home")])


def uploads_menu_kb(*, is_admin: bool = False) -> KM:
    commands = {"studio", "queue", "history", "cancel", "thumb_set", "thumb", "delthumb"}
    entries = [e for e in USER_MENU if e[0] in commands]
    rows = _rows(entries, 2)
    rows.append([primary("File Naming", icon="✏️", callback_data=cb("menu", "naming")),
                 primary("File Order", icon="🔢", callback_data=cb("menu", "sequence"))])
    rows.append(_nav("⌂ Home"))
    return KM(inline_keyboard=rows)


def account_menu_kb() -> KM:
    entries = [e for e in USER_MENU if e[0] in {"account", "mystats", "plan"}]
    return KM(inline_keyboard=_rows(entries, 2) + [
        [primary("Settings", icon="⚙️", callback_data=_ui("settings"))],
        _nav("⌂ Home")])


def settings_menu_kb(settings: dict | None = None) -> KM:
    st = settings or {}
    rows = [
        [primary(f"Naming › {st.get('naming', {}).get('template', 'Original name')[:18]}",
                 icon="✏️", callback_data=cb("menu", "naming"))],
        [primary(f"File order › {st.get('sequence_mode', 'Telegram order')}",
                 icon="🔢", callback_data=cb("menu", "sequence"))],
        [primary("Default folder › Change", icon="📁", callback_data=cb("menu", "setfolder"))],
        [primary("Preferences & toggles", icon="⚙️", callback_data=cb("menu", "settings"))],
        [primary("Channel automation", icon="📥", callback_data="v1:automation")],
        _nav("⌂ Home"),
    ]
    return KM(inline_keyboard=rows)


def admin_menu_kb() -> KM:
    rows = [
        [primary("Users", icon="👤", callback_data=_ui("admin_users")),
         primary("Locks & Plans", icon="🔒", callback_data=_ui("admin_locks"))],
        [primary("Broadcast & Operations", icon="📣", callback_data=_ui("admin_ops")),
         primary("System & Audit", icon="🛠", callback_data=_ui("admin_system"))],
        [primary("Admin Panel", icon="🔐", callback_data=cb("menu", "admin"))],
        _nav("⌂ Home"),
    ]
    return KM(inline_keyboard=rows)


def admin_group_kb(group: str) -> KM:
    title, commands = ADMIN_GROUPS[group]
    items = {cmd: (cmd, label, icon) for cmd, label, icon in ADMIN_MENU}
    entries = [items[c] for c in commands]
    return KM(inline_keyboard=_rows(entries, 2) + [_nav("⬅ Admin", _ui("admin")),
                                                    _nav("⌂ Home")])


def quick_kb(*, is_admin: bool = False, is_connected: bool = False,
             lang: str = "en") -> KM:
    return main_menu_kb(is_admin=is_admin, is_connected=is_connected, lang=lang)


def telegram_command_list() -> list:
    """Expose a short, curated BotFather menu; all other actions remain tappable."""
    from aiogram.types import BotCommand
    return [
        BotCommand(command="start", description="Open the ZC Drive Upload menu"),
        BotCommand(command="menu", description="Open the button menu"),
        BotCommand(command="connect", description="Connect Google Drive"),
        BotCommand(command="queue", description="View active uploads"),
        BotCommand(command="history", description="View recent uploads"),
        BotCommand(command="settings", description="Upload preferences"),
        BotCommand(command="plan", description="Plan and usage"),
        BotCommand(command="help", description="How to use Drive Upload"),
    ]
