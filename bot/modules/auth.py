import os
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db
from bot.helper.gdrive import GoogleDriveHelper
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)
# Store flows in memory: user_id -> flow
user_flows = {}

async def start_connect(client: Client, message: Message, user_id: int):
    db.upsert_user(user_id, message.from_user.username or "" if message.from_user else "", message.from_user.first_name or "" if message.from_user else "")
    helper = GoogleDriveHelper(user_id=user_id)
    try:
        auth_url, flow = helper.get_auth_url()
        user_flows[user_id] = flow
        text = f"""🔗 **Connect Google Drive**

Tap the button below to authorize your Google account.
You'll get a code — paste it here in chat.

⚠️ **Security:** Your token is stored only on this bot server, never shared.

👉 **Step 1:** Open link & sign in with your Gmail
👉 **Step 2:** Allow Drive access
👉 **Step 3:** Copy the code & paste here

🌐 Or use Web: `{Config.WEB_BASE_URL}/connect?user_id={user_id}`
"""
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔐 Authorize Google Drive", url=auth_url)],
            [InlineKeyboardButton("🌐 Open Web Connect", url=f"{Config.WEB_BASE_URL}/connect?user_id={user_id}")],
        ])
        await message.reply_text(text, reply_markup=keyboard)
    except Exception as e:
        LOGGER.error(f"Auth URL failed: {e}")
        await message.reply_text(f"❌ **Failed to generate auth link:**\n`{e}`\n\nMake sure `client_secrets.json` is in bot folder. See README → Google Cloud Setup.")

@Client.on_message(filters.private & filters.command(["connect", "auth"]))
async def connect_cmd(client: Client, message: Message):
    await start_connect(client, message, message.from_user.id)

@Client.on_message(filters.private & filters.command(["revoke", "disconnect"]))
async def revoke_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    token_path = f"{Config.TOKEN_DIR}/{user_id}.pickle"
    try:
        if os.path.exists(token_path):
            os.remove(token_path)
        # Update DB
        import sqlite3
        conn = sqlite3.connect("bot.db")
        conn.execute("UPDATE users SET is_authorized=0, token_path='' WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()
        await message.reply_text("✅ **Google Drive disconnected.**\nYour token has been deleted.")
    except Exception as e:
        await message.reply_text(f"❌ Failed: `{e}`")

@Client.on_message(filters.private & filters.text & ~filters.command(["start", "help", "connect", "auth", "revoke", "folders", "setfolder", "clone", "copy", "batch", "done", "cancel", "status", "web", "auto", "search", "list", "delete", "rename"]))
async def handle_auth_code(client: Client, message: Message):
    user_id = message.from_user.id
    if user_id not in user_flows:
        return
    text = message.text.strip()
    # Auth codes are usually 4/xxx or long strings, or full URL
    if len(text) < 10:
        return
    flow = user_flows.pop(user_id)
    status = await message.reply_text("🔄 **Verifying code...**")
    try:
        helper = GoogleDriveHelper(user_id=user_id)
        creds = helper.exchange_code(flow, text)
        db.set_authorized(user_id, helper.token_path)
        # Test drive access
        helper2 = GoogleDriveHelper(user_id=user_id)
        helper2.authorize()
        quota = helper2.get_storage_quota()
        used = int(quota.get("usage", 0) or 0)
        limit = int(quota.get("limit", 0) or 0)
        from bot.helper.utils import humanbytes
        await status.edit_text(f"✅ **Connected Successfully!**\n\n📧 Your Google Drive is now linked.\n💾 Storage: `{humanbytes(used)} / {humanbytes(limit)}`\n\nNow just send any file or link and I'll upload it to Drive! ☁️")
    except Exception as e:
        LOGGER.error(f"Auth exchange failed: {e}")
        await status.edit_text(f"❌ **Invalid code:**\n`{str(e)[:500]}`\n\nPlease run `/connect` again and paste the fresh code.")
