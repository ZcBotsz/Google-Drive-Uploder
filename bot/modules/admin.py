import os
import sys
import asyncio
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db
from bot.helper.utils import humanbytes
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)

def _is_admin(user_id: int):
    return Config.is_admin(user_id)

async def show_admin_panel(client: Client, message: Message, user_id: int):
    if not _is_admin(user_id):
        await message.reply_text("❌ **Admin only.**", quote=True)
        return
    stats = db.get_global_stats()
    text = f"""👑 **Admin Panel**

👥 Total Users: `{stats['users']}`
🔗 Drive Connected: `{stats['connected']}`
📦 Total Files Uploaded: `{stats['total_files']}`
💾 Total Size: `{humanbytes(stats['total_bytes'])}`
🤖 Active Automations: `{stats['automations']}`

**Commands:**
• `/broadcast <text>` — Broadcast to all users
• `/ban <user_id> [reason]` — Ban user
• `/unban <user_id>` — Unban user
• `/users` — List recent users
• `/restart` — Restart bot (Render will restart)
• `/logs` — Show recent logs
• `/health` — Bot health check
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Global Stats", callback_data="admin_stats"),
         InlineKeyboardButton("👥 Users", callback_data="admin_users")],
        [InlineKeyboardButton("🌐 Web Admin", url=f"{Config.WEB_BASE_URL}/admin?user_id={user_id}")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_home")],
    ])
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_message(filters.private & filters.command(["admin", "panel"]))
async def admin_cmd(client: Client, message: Message):
    await show_admin_panel(client, message, message.from_user.id)

@Client.on_message(filters.private & filters.command(["broadcast"]))
async def broadcast_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    text = message.text.split(None, 1)
    if len(text) < 2:
        await message.reply_text("**Usage:** `/broadcast <message>`\nReply to a message with `/broadcast` to forward it.", quote=True)
        return
    broadcast_text = text[1]
    # If replying to message, forward content
    if message.reply_to_message:
        broadcast_text = message.reply_to_message.text or message.reply_to_message.caption or broadcast_text
    users = db.get_all_users(limit=5000)
    sent = failed = 0
    status = await message.reply_text(f"📢 **Broadcasting to {len(users)} users...**", quote=True)
    for u in users:
        try:
            await client.send_message(u["user_id"], f"📢 **Broadcast from Admin**\n\n{broadcast_text}")
            sent += 1
        except Exception as e:
            failed += 1
            LOGGER.warning(f"Broadcast failed to {u['user_id']}: {e}")
        await asyncio.sleep(0.05)  # flood control
    db.log_broadcast(message.from_user.id, broadcast_text, sent, failed)
    await status.edit_text(f"✅ **Broadcast done!**\nSent: `{sent}`\nFailed: `{failed}`")

@Client.on_message(filters.private & filters.command(["ban"]))
async def ban_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    parts = message.command
    if len(parts) < 2:
        await message.reply_text("**Usage:** `/ban <user_id> [reason]`", quote=True)
        return
    try:
        target = int(parts[1])
    except:
        await message.reply_text("❌ Invalid user ID", quote=True)
        return
    reason = " ".join(parts[2:]) if len(parts) > 2 else "No reason given"
    db.ban_user(target, reason)
    try:
        await client.send_message(target, f"🚫 **You have been banned.**\nReason: `{reason}`\nContact admin if you think this is a mistake.")
    except:
        pass
    await message.reply_text(f"🚫 **Banned** `{target}`\nReason: `{reason}`", quote=True)

@Client.on_message(filters.private & filters.command(["unban"]))
async def unban_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/unban <user_id>`", quote=True)
        return
    try:
        target = int(message.command[1])
    except:
        await message.reply_text("❌ Invalid user ID", quote=True)
        return
    db.unban_user(target)
    await message.reply_text(f"✅ **Unbanned** `{target}`", quote=True)

@Client.on_message(filters.private & filters.command(["users"]))
async def users_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    users = db.get_all_users(limit=30)
    if not users:
        await message.reply_text("📭 No users yet.", quote=True)
        return
    text = f"👥 **Users ({len(users)})**\n\n"
    for u in users:
        banned = "🚫" if u.get("is_banned") else "✅"
        connected = "🔗" if u.get("is_authorized") else "❌"
        text += f"{banned} {connected} `{u['user_id']}` — {u.get('first_name') or ''} (@{u.get('username') or '-'})\n"
    await message.reply_text(text, quote=True)

@Client.on_message(filters.private & filters.command(["restart"]))
async def restart_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    await message.reply_text("🔄 **Restarting bot...**\nRender will restart the service in a few seconds.", quote=True)
    await asyncio.sleep(2)
    os.execv(sys.executable, [sys.executable] + sys.argv)

@Client.on_message(filters.private & filters.command(["logs"]))
async def logs_cmd(client: Client, message: Message):
    if not _is_admin(message.from_user.id):
        await message.reply_text("❌ Admin only.", quote=True)
        return
    # Show last 30 lines from stdout? We can't easily capture, so show system info
    import platform
    text = f"""📋 **System Info**

🐍 Python: `{platform.python_version()}`
💻 Platform: `{platform.system()} {platform.release()}`
📁 DATA_DIR: `{Config.DATA_DIR}`
🌐 WEB: `{Config.WEB_BASE_URL}:{Config.WEB_PORT}`
🗄️ DB: `{Config.DB_PATH}`
🔑 Admins: `{Config.ADMIN_IDS}`
📦 Max Concurrent: `{Config.MAX_CONCURRENT_UPLOADS}`

Check Render Dashboard → Logs for full logs.
"""
    await message.reply_text(text, quote=True)

@Client.on_message(filters.private & filters.command(["health"]))
async def health_cmd(client: Client, message: Message):
    stats = db.get_global_stats()
    await message.reply_text(f"💚 **Bot Healthy**\n\nUsers: `{stats['users']}`\nFiles: `{stats['total_files']}`\nWeb: `{Config.WEB_BASE_URL}/health`", quote=True)

@Client.on_callback_query(filters.create(lambda _, __, q: q.data in ["admin_stats", "admin_users"]))
async def admin_cb(client: Client, query):
    if not _is_admin(query.from_user.id):
        await query.answer("Admin only", show_alert=True)
        return
    if query.data == "admin_stats":
        stats = db.get_global_stats()
        await query.message.edit_text(f"📊 **Global Stats**\n\n👥 Users: `{stats['users']}`\n🔗 Connected: `{stats['connected']}`\n📦 Files: `{stats['total_files']}`\n💾 Size: `{humanbytes(stats['total_bytes'])}`\n🤖 Automations: `{stats['automations']}`")
    elif query.data == "admin_users":
        users = db.get_all_users(limit=20)
        text = "👥 **Recent Users**\n\n"
        for u in users:
            text += f"• `{u['user_id']}` — {u.get('first_name') or ''} {'🚫' if u.get('is_banned') else ''}\n"
        await query.message.edit_text(text)
    await query.answer()
