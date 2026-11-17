from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from bot.core import database as db
from bot.helper.queue_manager import queue_manager
from bot.helper.utils import humanbytes

# Track batch mode per user
batch_mode = set()

@Client.on_message(filters.private & filters.command(["batch"]))
async def batch_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id in batch_mode:
        batch_mode.remove(user_id)
        await message.reply_text("📦 **Batch mode OFF.**", quote=True)
    else:
        batch_mode.add(user_id)
        await message.reply_text(
            "📦 **Batch Mode ON**\n\n"
            "Send as many files/links as you want — they'll be queued.\n"
            "When done, send `/done` to start uploading.\n"
            "Send `/cancel` to clear queue.",
            quote=True
        )

@Client.on_message(filters.private & filters.command(["done", "startupload"]))
async def done_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    batch_mode.discard(user_id)
    queue = db.get_queue(user_id)
    if not queue:
        await message.reply_text("📭 Queue is empty. Send files/links first.", quote=True)
        return
    await message.reply_text(f"🚀 **Starting batch upload:** {len(queue)} items queued...", quote=True)
    queue_manager.set_client(client)
    await queue_manager.process_queue(user_id, message.chat.id)

@Client.on_message(filters.private & filters.command(["queue", "status_queue"]))
async def queue_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    queue = db.get_queue(user_id)
    if not queue:
        await message.reply_text("📭 Queue empty.", quote=True)
        return
    text = f"📦 **Queue ({len(queue)} items)**\n\n"
    for i, item in enumerate(queue, 1):
        text += f"{i}. `{item['source_type']}` — {item['status']} ({item['progress']:.0f}%)\n`{item['source_data'][:60]}`\n\n"
    await message.reply_text(text, quote=True)

@Client.on_message(filters.private & filters.command(["clearqueue"]))
async def clear_queue_cmd(client: Client, message: Message):
    db.clear_queue(message.from_user.id)
    await message.reply_text("🧹 Queue cleared.", quote=True)
