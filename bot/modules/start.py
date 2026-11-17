from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db

@Client.on_message(filters.private & filters.command(["start", "help"]))
async def start_handler(client: Client, message: Message):
    user = message.from_user
    # Banned check
    if db.is_banned(user.id):
        u = db.get_user(user.id)
        reason = u.get("ban_reason", "") if u else ""
        await message.reply_text(f"🚫 **You are banned.**\nReason: `{reason}`", quote=True)
        return
    db.upsert_user(user.id, user.username or "", user.first_name or "")
    u = db.get_user(user.id)
    is_auth = u and u.get("is_authorized")
    folder_id, folder_name = db.get_default_folder(user.id)
    is_admin = Config.is_admin(user.id)

    status = "✅ Connected" if is_auth else "❌ Not Connected"
    web_url = f"{Config.WEB_BASE_URL}/dashboard?user_id={user.id}"

    text = f"""👋 **Hi {user.first_name}!** {'👑' if is_admin else ''}

🚀 **Telegram → Google Drive Uploader** <i>Advanced</i>
Upload any file, video, or link directly to your Drive — fast, secure & beautiful.

📊 **Status:** {status}
📁 **Upload Folder:** `{folder_name}`

**How to use:**
1️⃣ Tap **Connect Drive** to authorize your Google account
2️⃣ Send any **file / video / photo** here
3️⃣ Or send a **direct link** / **Google Drive link**
4️⃣ Use **📂 Choose Folder** to pick where to upload (Web UI)

**Quick Commands:**
• `/connect` — Connect Google Drive (OAuth)
• `/folders` — Browse & select upload folder
• `/batch` — Batch upload mode (send many files)
• `/clone <gdrive_url>` — Clone Drive file/folder
• `/auto` — Auto-upload from channel
• `/settings` — Your settings (auto-share, rename)
• `/status` — Your Drive storage & uploads
• `/web` — Open Web Dashboard
{'• `/admin` — Admin panel' if is_admin else ''}

Just send a file now and I'll upload it! ☁️
"""

    buttons = [
        [InlineKeyboardButton("🔗 Connect Drive", callback_data="connect_drive"),
         InlineKeyboardButton("📂 Choose Folder", callback_data="choose_folder")],
        [InlineKeyboardButton("🌐 Web Dashboard", url=web_url),
         InlineKeyboardButton("📊 My Status", callback_data="my_status")],
        [InlineKeyboardButton("🤖 Automation", callback_data="automation_menu"),
         InlineKeyboardButton("⚙️ Settings", callback_data="settings_menu")],
        [InlineKeyboardButton("❓ Help", callback_data="help_menu")],
    ]
    if is_admin:
        buttons.append([InlineKeyboardButton("👑 Admin Panel", callback_data="admin_menu")])
    keyboard = InlineKeyboardMarkup(buttons)
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_callback_query(filters.create(lambda _, __, q: q.data in ["connect_drive", "choose_folder", "my_status", "automation_menu", "help_menu", "back_home", "settings_menu", "admin_menu"]))
async def callback_handler(client: Client, query):
    data = query.data
    user_id = query.from_user.id
    if data == "connect_drive":
        from bot.modules.auth import start_connect
        await start_connect(client, query.message, user_id)
    elif data == "choose_folder":
        from bot.modules.drive import show_folder_picker
        await show_folder_picker(client, query.message, user_id)
    elif data == "my_status":
        from bot.modules.status import show_status
        await show_status(client, query.message, user_id)
    elif data == "automation_menu":
        from bot.modules.automation import show_automation_menu
        await show_automation_menu(client, query.message, user_id)
    elif data == "settings_menu":
        from bot.modules.settings import show_settings
        await show_settings(client, query.message, user_id)
    elif data == "admin_menu":
        from bot.modules.admin import show_admin_panel
        await show_admin_panel(client, query.message, user_id)
    elif data == "help_menu":
        await query.message.edit_text(
            "❓ **Help**\n\n"
            "• Send **any file** → auto uploads to Drive\n"
            "• Send **http link** → downloads & uploads\n"
            "• Send **drive.google.com link** → clones to your Drive\n"
            "• `/setfolder <folder_url>` → set default folder\n"
            "• `/batch` → queue many files, then `/done`\n"
            "• `/auto <channel_id> <folder_url>` → auto-upload channel\n"
            "• `/settings` → auto-share, rename pattern\n"
            "• `/cancel` → cancel current upload\n"
            "• `/revoke` → disconnect Drive\n\n"
            "🌐 Web Dashboard lets you visually pick folders, drag-drop upload & manage everything.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="back_home")]])
        )
    elif data == "back_home":
        await start_handler(client, query.message)
    await query.answer()
