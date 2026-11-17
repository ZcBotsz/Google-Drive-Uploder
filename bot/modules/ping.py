from pyrogram import Client, filters
from pyrogram.types import Message
from bot.core.logger import get_logger
import time

LOGGER = get_logger(__name__)
START_TIME = time.time()

@Client.on_message(filters.private & filters.command(["ping", "test"]))
async def ping_handler(client: Client, message: Message):
    """Simple diagnostic handler - no DB/Drive dependency. If this doesn't reply, plugins aren't loaded."""
    uptime = int(time.time() - START_TIME)
    LOGGER.info(f"🏓 /ping from user {message.from_user.id} - replying pong")
    await message.reply_text(f"🏓 **Pong!** Bot is alive ✅\n⏱️ Uptime: `{uptime}s`\n👤 User: `{message.from_user.id}`\n\nBot handlers are working! Now try `/start`.")

@Client.on_message(filters.private & filters.text & ~filters.command(["start", "help", "connect", "auth", "revoke", "folders", "setfolder", "clone", "copy", "batch", "done", "cancel", "status", "web", "auto", "search", "list", "delete", "rename", "clear", "ping", "test", "admin", "broadcast", "ban", "unban", "users", "restart", "logs", "health", "settings", "setting", "config", "newfolder", "mkdir", "queue", "status_queue", "clearqueue", "automation"]))
async def debug_logger(client: Client, message: Message):
    """Log all unmatched text messages for debugging (helps diagnose handler issues)"""
    # Only log if it looks like it should have been handled (contains URL or is long)
    text = message.text or ""
    if "http" in text or "drive.google" in text or len(text) > 20:
        LOGGER.info(f"📩 Unhandled text from {message.from_user.id}: {text[:100]}")
    # Don't reply - let other handlers (auth code, links) handle it
    # This handler runs AFTER specific handlers due to group order? Actually Pyrogram runs in order of registration.
    # To avoid interfering, we just log and return.
    pass
