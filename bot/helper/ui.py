"""
FTM-style UI: Colorful buttons, emojis, animated emoji support.
Telegram custom emojis: Use <tg-emoji emoji-id="...">...</tg-emoji> in HTML parse mode.
Set CUSTOM_EMOJIS env var as JSON: {"upload":"5368324170671202286", ...}
Fallback to Unicode emojis if not set.
"""
import os
import json

# Load custom emoji IDs from env (FTM JSON style)
# Example: CUSTOM_EMOJIS='{"upload":"5368324170671202286","folder":"5368324170671202287"}'
try:
    CUSTOM_EMOJIS = json.loads(os.getenv("CUSTOM_EMOJIS", "{}"))
except:
    CUSTOM_EMOJIS = {}

def emoji(key: str, fallback: str) -> str:
    """Return custom emoji HTML if configured, else Unicode fallback."""
    eid = CUSTOM_EMOJIS.get(key)
    if eid:
        # Telegram custom emoji format (HTML)
        return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'
    return fallback

# FTM-style colorful button labels (Unicode emojis - always work)
# Organized by category for easy navigation
BTN = {
    # Main menu
    "connect": "🔗 Connect Drive",
    "folder": "📂 Choose Folder",
    "dashboard": "🌐 Web Dashboard",
    "status": "📊 My Status",
    "automation": "🤖 Automation",
    "settings": "⚙️ Settings",
    "help": "❓ Help",
    "admin": "👑 Admin Panel",
    "home": "🏠 Home",
    "back": "⬅️ Back",
    # Actions
    "upload": "📤 Upload",
    "batch": "📦 Batch Upload",
    "clone": "📋 Clone Drive",
    "search": "🔍 Search Drive",
    "list": "📁 Browse Drive",
    "newfolder": "➕ New Folder",
    "cancel": "🛑 Cancel",
    "sync": "🔄 Sync Folder",
    "contact": "👨‍💻 Contact Admin",
    "support": "💬 Support",
    "features": "✨ Features",
    "plan": "💎 My Plan",
}

def main_menu_keyboard(web_url: str, is_admin: bool = False):
    """FTM-style main menu with colorful grid buttons."""
    from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    buttons = [
        [InlineKeyboardButton(BTN["connect"], callback_data="connect_drive"),
         InlineKeyboardButton(BTN["folder"], callback_data="choose_folder")],
        [InlineKeyboardButton(BTN["dashboard"], url=web_url),
         InlineKeyboardButton(BTN["status"], callback_data="my_status")],
        [InlineKeyboardButton(BTN["automation"], callback_data="automation_menu"),
         InlineKeyboardButton(BTN["settings"], callback_data="settings_menu")],
        [InlineKeyboardButton(BTN["help"], callback_data="help_menu"),
         InlineKeyboardButton(BTN["contact"], url="https://t.me/")],  # Replace with admin username
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton(BTN["admin"], callback_data="admin_menu")])
    return InlineKeyboardMarkup(buttons)

def welcome_text(first_name: str, is_connected: bool, folder_name: str, is_admin: bool = False, plan: str = "Free"):
    """FTM-style welcome with features + plan."""
    status_emoji = "🟢" if is_connected else "🔴"
    status_text = "Connected" if is_connected else "Not Connected"
    admin_line = "\n👑 **Admin**" if is_admin else ""
    return f"""👋 **Hi {first_name}!**{admin_line}

🚀 **Telegram → Google Drive Uploader** ✨
Upload any file, video, or link directly to your Drive — fast, secure & beautiful.

{status_emoji} **Status:** {status_text}
📁 **Folder:** `{folder_name}`
💎 **Plan:** `{plan}`

**✨ Features:**
• 📤 Single + 📦 Batch upload
• 🤖 Auto-upload from channels
• 📋 Clone Drive files/folders
• 🏷️ Caption → Filename (Lecture 1, 2...)
• 🌐 Web Folder Picker + Dashboard
• 🔄 Website ↔️ Bot folder sync

**Quick Start:**
1️⃣ Tap **Connect Drive** → Authorize Gmail
2️⃣ Tap **Choose Folder** → Pick destination
3️⃣ Send file/link → Get Drive link! ☁️

Just send a file now! 📎
"""
