from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from bot.core import database as db
from bot.helper.utils import get_id_from_url
from bot.helper.gdrive import get_drive_helper
from bot.helper.automation import automation_manager

async def show_automation_menu(client: Client, message, user_id: int):
    autos = db.get_automations(user_id)
    folder_id, folder_name = db.get_default_folder(user_id)
    text = f"""🤖 **Automation**

Auto-upload files from a channel/group directly to Drive.

**Current rules:** {len(autos)}
"""
    for a in autos:
        text += f"\n• Channel `{a['channel_id']}` → 📁 `{a['folder_name']}`"
    text += f"""

**How to setup:**
1. Add this bot as admin in your channel/group
2. Run: `/auto <channel_id> <folder_url>`
   Example: `/auto -1001234567890 https://drive.google.com/drive/folders/xxx`
3. Or: `/auto <channel_id>` to use current folder `{folder_name}`

**Commands:**
• `/auto list` — list rules
• `/auto remove <channel_id>` — remove rule
• `/auto <channel_id> <folder_url>` — add rule

Bot will auto-download & upload every new file in that channel!
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List Rules", callback_data="auto_list")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_home")],
    ])
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_message(filters.private & filters.command(["auto", "automation"]))
async def auto_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await show_automation_menu(client, message, user_id)
        return
    arg = message.command[1]
    if arg == "list":
        autos = db.get_automations(user_id)
        if not autos:
            await message.reply_text("📭 No automation rules.", quote=True)
            return
        text = "🤖 **Automation Rules**\n\n"
        for a in autos:
            text += f"• `{a['channel_id']}` → 📁 `{a['folder_name']}`\n"
        await message.reply_text(text, quote=True)
        return
    if arg == "remove" and len(message.command) >= 3:
        try:
            cid = int(message.command[2])
            db.delete_automation(user_id, cid)
            await message.reply_text(f"✅ Removed automation for `{cid}`", quote=True)
        except:
            await message.reply_text("❌ Invalid channel ID", quote=True)
        return
    # Add rule: /auto <channel_id> [folder_url]
    try:
        channel_id = int(arg)
    except:
        await message.reply_text("❌ Invalid channel ID. Example: `/auto -1001234567890`", quote=True)
        return
    folder_id, folder_name = db.get_default_folder(user_id)
    if len(message.command) >= 3:
        fid = get_id_from_url(message.command[2])
        if fid == "NotFound":
            await message.reply_text("❌ Invalid folder URL", quote=True)
            return
        try:
            helper = get_drive_helper(user_id=user_id)
            meta = helper.get_file_metadata(fid)
            folder_id = fid
            folder_name = meta.get("name")
        except Exception as e:
            await message.reply_text(f"❌ Failed: `{str(e)[:400]}`", quote=True)
            return
    db.set_automation(user_id, channel_id, folder_id, folder_name)
    await message.reply_text(
        f"✅ **Automation enabled!**\n\n"
        f"📺 Channel: `{channel_id}`\n"
        f"📁 Folder: `{folder_name}`\n\n"
        f"Make sure bot is admin in that channel. New files will auto-upload to Drive!",
        quote=True
    )

@Client.on_callback_query(filters.create(lambda _, __, q: q.data == "auto_list"))
async def auto_list_cb(client: Client, query):
    user_id = query.from_user.id
    autos = db.get_automations(user_id)
    if not autos:
        await query.message.edit_text("📭 No automation rules.")
    else:
        text = "🤖 **Rules**\n\n"
        for a in autos:
            text += f"• `{a['channel_id']}` → 📁 `{a['folder_name']}`\n"
        await query.message.edit_text(text)
    await query.answer()

# Channel/group message handler for automation
@Client.on_message(filters.group | filters.channel)
async def automation_handler(client: Client, message):
    automation_manager.set_client(client)
    await automation_manager.handle_channel_message(message)
