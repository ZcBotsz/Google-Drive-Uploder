from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.utils import humanbytes

async def show_status(client: Client, message, user_id: int):
    u = db.get_user(user_id)
    stats = db.get_stats(user_id)
    recent = db.get_recent_uploads(user_id, limit=5)
    folder_id, folder_name = db.get_default_folder(user_id)

    text = f"""📊 **Your Status**

👤 User: `{user_id}`
🔗 Drive: {'✅ Connected' if u and u.get('is_authorized') else '❌ Not Connected'}
📁 Upload Folder: `{folder_name}`

📦 **Uploads:** {stats['total_files']} files
💾 **Total Size:** {humanbytes(stats['total_bytes'])}

"""
    try:
        helper = get_drive_helper(user_id=user_id)
        quota = helper.get_storage_quota()
        used = int(quota.get("usage", 0) or 0)
        limit = int(quota.get("limit", 0) or 0)
        pct = (used / limit * 100) if limit else 0
        text += f"☁️ **Drive Storage:** {humanbytes(used)} / {humanbytes(limit)} ({pct:.1f}%)\n"
        # Progress bar
        bar_len = 15
        filled = int(bar_len * pct / 100)
        text += f"{'█'*filled}{'░'*(bar_len-filled)} {pct:.1f}%\n\n"
    except Exception as e:
        text += f"⚠️ Drive quota unavailable: `{str(e)[:200]}`\n\n"

    if recent:
        text += "🕐 **Recent Uploads:**\n"
        for r in recent:
            text += f"• [{r['file_name']}]({r['drive_link']}) — {humanbytes(r['file_size'])}\n"
    else:
        text += "🕐 No uploads yet. Send a file!"

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Web Dashboard", url=f"{Config.WEB_BASE_URL}/dashboard?user_id={user_id}")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_home")],
    ])
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_message(filters.private & filters.command(["status", "stats", "me"]))
async def status_cmd(client: Client, message: Message):
    await show_status(client, message, message.from_user.id)

@Client.on_message(filters.private & filters.command(["web", "dashboard"]))
async def web_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    await message.reply_text(
        f"🌐 **Web Dashboard**\n\n"
        f"Open: {Config.WEB_BASE_URL}/dashboard?user_id={user_id}\n\n"
        f"📂 Folder Picker: {Config.WEB_BASE_URL}/picker?user_id={user_id}\n"
        f"🔗 Connect: {Config.WEB_BASE_URL}/connect?user_id={user_id}",
        quote=True
    )
