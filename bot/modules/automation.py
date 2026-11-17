from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from bot.core import database as db
from bot.helper.utils import get_id_from_url
from bot.helper.gdrive import get_drive_helper
from bot.helper.automation import automation_manager
from config import Config

async def show_automation_menu(client: Client, message, user_id: int):
    autos = db.get_automations(user_id)
    folder_id, folder_name = db.get_default_folder(user_id)
    text = f"""🤖 **Automation** ✨

Auto-upload files from channels/groups → Drive
📁 **Your current folder (synced 🌐↔️🤖):** `{folder_name}`

**Current rules:** {len(autos)}
"""
    for a in autos:
        # Show if rule folder matches current default (synced) or differs
        synced = "✅ Synced" if a['folder_id'] == folder_id else "⚠️ Different folder"
        text += f"\n• Channel `{a['channel_id']}` → 📁 `{a['folder_name']}` ({synced})"
    text += f"""

**How it works:**
• Automation **always uses your current folder** above (synced from Website/Bot)
• Change folder on Website or `/setfolder` → Automation follows automatically!
• Captions become filenames: "Lecture 1" → `Lecture 1.mp4`
• Sequential upload: 1 → 2 → 3... in order

**Commands:**
• `/auto <channel_id>` — add rule (uses current folder)
• `/auto <channel_id> <folder_url>` — add rule with specific folder
• `/auto list` — list rules
• `/auto sync` — update all rules to current folder
• `/auto remove <channel_id>` — remove rule
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 List Rules", callback_data="auto_list"),
         InlineKeyboardButton("🔄 Sync to Current Folder", callback_data="auto_sync")],
        [InlineKeyboardButton("📂 Change Folder (Web)", url=f"{Config.WEB_BASE_URL}/picker?user_id={user_id}")],
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
        folder_id, folder_name = db.get_default_folder(user_id)
        text = f"🤖 **Automation Rules**\n📁 Current folder: `{folder_name}`\n\n"
        for a in autos:
            synced = "✅" if a['folder_id'] == folder_id else "⚠️"
            text += f"{synced} `{a['channel_id']}` → 📁 `{a['folder_name']}`\n"
        text += f"\n💡 Automation uses current folder automatically. Use `/auto sync` to update rule folders."
        await message.reply_text(text, quote=True)
        return
    if arg == "sync":
        folder_id, folder_name = db.get_default_folder(user_id)
        autos = db.get_automations(user_id)
        if not autos:
            await message.reply_text("📭 No automation rules to sync.", quote=True)
            return
        for a in autos:
            db.set_automation(user_id, a['channel_id'], folder_id, folder_name, a.get('auto_share', 1), a.get('rename_pattern', ''))
        await message.reply_text(f"✅ **Synced {len(autos)} rules** to current folder: `{folder_name}`", quote=True)
        return
    if arg == "remove" and len(message.command) >= 3:
        try:
            cid = int(message.command[2])
            db.delete_automation(user_id, cid)
            await message.reply_text(f"✅ Removed automation for `{cid}`", quote=True)
        except:
            await message.reply_text("❌ Invalid channel ID", quote=True)
        return
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
        f"Make sure bot is admin in that channel. New files will auto-upload to Drive!\n"
        f"🏷️ Captions become filenames (e.g. 'Lecture 1' → `Lecture 1.mp4`)\n"
        f"🔄 Folder follows your current selection (Website/Bot synced)",
        quote=True
    )

@Client.on_callback_query(filters.create(lambda _, __, q: q.data in ["auto_list", "auto_sync"]))
async def auto_cb(client: Client, query):
    user_id = query.from_user.id
    if query.data == "auto_list":
        autos = db.get_automations(user_id)
        if not autos:
            await query.message.edit_text("📭 No automation rules.")
        else:
            folder_id, folder_name = db.get_default_folder(user_id)
            text = f"🤖 **Rules** (Current: `{folder_name}`)\n\n"
            for a in autos:
                synced = "✅" if a['folder_id'] == folder_id else "⚠️"
                text += f"{synced} `{a['channel_id']}` → 📁 `{a['folder_name']}`\n"
            await query.message.edit_text(text)
    elif query.data == "auto_sync":
        folder_id, folder_name = db.get_default_folder(user_id)
        autos = db.get_automations(user_id)
        for a in autos:
            db.set_automation(user_id, a['channel_id'], folder_id, folder_name, a.get('auto_share', 1), a.get('rename_pattern', ''))
        await query.message.edit_text(f"✅ **Synced {len(autos)} rules** to `{folder_name}`")
    await query.answer()

@Client.on_message(filters.group | filters.channel)
async def automation_handler(client: Client, message):
    automation_manager.set_client(client)
    await automation_manager.handle_channel_message(message)
