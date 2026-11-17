from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db

async def show_settings(client: Client, message: Message, user_id: int):
    u = db.get_user(user_id)
    if not u:
        db.upsert_user(user_id, message.from_user.username or "" if message.from_user else "", message.from_user.first_name or "" if message.from_user else "")
        u = db.get_user(user_id)
    folder_id, folder_name = db.get_default_folder(user_id)
    auto_share = bool(u.get("auto_share", 1)) if u else True
    auto_rename = u.get("auto_rename", "") if u else ""

    text = f"""⚙️ **Your Settings**

📁 **Upload Folder:** `{folder_name}`
🔗 **Folder ID:** `{folder_id}`
🔗 **Drive:** {'✅ Connected' if u and u.get('is_authorized') else '❌ Not Connected'}
📤 **Auto-Share (Anyone with link):** {'✅ ON' if auto_share else '❌ OFF'}
🏷️ **Auto-Rename Pattern:** `{auto_rename or 'None'}`

**Change:**
• Tap buttons below or use commands
• `/setfolder <url>` — change folder
• `/rename <pattern>` — set rename regex (e.g. `\\[.*?\\]`)
• `/rename clear` — clear rename
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📂 Change Folder (Web)", url=f"{Config.WEB_BASE_URL}/picker?user_id={user_id}")],
        [InlineKeyboardButton(f"📤 Auto-Share: {'ON' if auto_share else 'OFF'}", callback_data="toggle_autoshare")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_home")],
    ])
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_message(filters.private & filters.command(["settings", "setting", "config"]))
async def settings_cmd(client: Client, message: Message):
    await show_settings(client, message, message.from_user.id)

@Client.on_message(filters.private & filters.command(["rename"]))
async def rename_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        u = db.get_user(user_id)
        pattern = u.get("auto_rename", "") if u else ""
        await message.reply_text(f"🏷️ **Current rename pattern:** `{pattern or 'None'}`\n\n**Usage:** `/rename <regex>`\nExample: `/rename \\[.*?\\]` removes [tags]\n`/rename clear` to remove.", quote=True)
        return
    arg = " ".join(message.command[1:])
    if arg.lower() == "clear":
        db.set_auto_rename(user_id, "")
        await message.reply_text("✅ Rename pattern cleared.", quote=True)
    else:
        # Validate regex
        import re
        try:
            re.compile(arg)
        except re.error as e:
            await message.reply_text(f"❌ Invalid regex: `{e}`", quote=True)
            return
        db.set_auto_rename(user_id, arg)
        await message.reply_text(f"✅ **Rename pattern set:** `{arg}`\nFiles will be renamed on upload.", quote=True)

@Client.on_callback_query(filters.create(lambda _, __, q: q.data == "toggle_autoshare"))
async def toggle_autoshare(client: Client, query):
    user_id = query.from_user.id
    u = db.get_user(user_id)
    current = bool(u.get("auto_share", 1)) if u else True
    new_val = not current
    db.set_auto_share(user_id, new_val)
    await query.message.edit_text(f"📤 **Auto-Share:** {'✅ ON' if new_val else '❌ OFF'}\n\n{'Uploaded files will be public (anyone with link can view).' if new_val else 'Uploaded files will be private (only you can view).'}")
    await query.answer("Updated!")
