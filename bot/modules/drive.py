from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, Message
from config import Config
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.utils import get_id_from_url, humanbytes
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)

async def show_folder_picker(client: Client, message: Message, user_id: int):
    u = db.get_user(user_id)
    if not u or not u.get("is_authorized"):
        if not __import__("os").path.exists("token.pickle"):
            await message.reply_text("❌ **Not connected!** Run `/connect` first.", quote=True)
            return
    folder_id, folder_name = db.get_default_folder(user_id)
    web_url = f"{Config.WEB_BASE_URL}/picker?user_id={user_id}"
    text = f"""📂 **Choose Upload Folder**

Current: `{folder_name}` (`{folder_id}`)

🌐 **Best way:** Open Web Picker to visually browse your Drive folders, search, create new folder & select destination.

Or use commands:
• `/folders` — list folders here
• `/setfolder <folder_url>` — set by link
• `/setfolder clear` — reset to My Drive
• `/newfolder <name>` — create folder in current location
"""
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Open Web Folder Picker", url=web_url)],
        [InlineKeyboardButton("📋 List Folders Here", callback_data="list_folders_root")],
        [InlineKeyboardButton("🏠 Home", callback_data="back_home")],
    ])
    await message.reply_text(text, reply_markup=keyboard)

@Client.on_message(filters.private & filters.command(["folders", "drive", "picker"]))
async def folders_cmd(client: Client, message: Message):
    await show_folder_picker(client, message, message.from_user.id)

@Client.on_message(filters.private & filters.command(["setfolder"]))
async def setfolder_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        folder_id, folder_name = db.get_default_folder(user_id)
        await message.reply_text(f"**Current folder:** `{folder_name}`\n`{folder_id}`\n\nUse `/setfolder <folder_url>` to change.\nUse `/setfolder clear` to reset.", quote=True)
        return
    arg = message.command[1]
    if arg.lower() == "clear":
        db.set_default_folder(user_id, "root", "My Drive")
        await message.reply_text("✅ **Folder cleared.** Uploads will go to My Drive root.", quote=True)
        return
    file_id = get_id_from_url(arg)
    if file_id == "NotFound":
        await message.reply_text("❌ **Invalid folder URL.**\nPaste a Drive folder link like `https://drive.google.com/drive/folders/xxx`", quote=True)
        return
    try:
        helper = get_drive_helper(user_id=user_id)
        meta = helper.get_file_metadata(file_id)
        if meta.get("mimeType") != "application/vnd.google-apps.folder":
            await message.reply_text("❌ **Not a folder.** Please paste a folder link.", quote=True)
            return
        db.set_default_folder(user_id, file_id, meta.get("name"))
        await message.reply_text(f"✅ **Folder set!**\n\n📁 `{meta.get('name')}`\n🔗 https://drive.google.com/drive/folders/{file_id}\n\nAll future uploads will go here.", quote=True)
    except Exception as e:
        await message.reply_text(f"❌ **Failed:** `{str(e)[:500]}`", quote=True)

@Client.on_message(filters.private & filters.command(["newfolder", "mkdir"]))
async def newfolder_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/newfolder <folder_name>`", quote=True)
        return
    name = " ".join(message.command[1:])
    folder_id, folder_name = db.get_default_folder(user_id)
    try:
        helper = get_drive_helper(user_id=user_id)
        new_id = helper.create_directory(name, folder_id if folder_id != "root" else None)
        await message.reply_text(f"✅ **Folder created!**\n\n📁 `{name}`\n🔗 https://drive.google.com/drive/folders/{new_id}\n\nUse `/setfolder https://drive.google.com/drive/folders/{new_id}` to upload there.", quote=True)
    except Exception as e:
        await message.reply_text(f"❌ **Failed:** `{str(e)[:500]}`", quote=True)

@Client.on_message(filters.private & filters.command(["list", "ls"]))
async def list_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    folder_id, folder_name = db.get_default_folder(user_id)
    if len(message.command) > 1:
        fid = get_id_from_url(message.command[1])
        if fid != "NotFound":
            folder_id = fid
    try:
        helper = get_drive_helper(user_id=user_id)
        files = helper.list_files(folder_id, page_size=30)
        if not files:
            await message.reply_text(f"📁 **{folder_name}** is empty.", quote=True)
            return
        text = f"📁 **{folder_name}** (`{folder_id}`)\n\n"
        for f in files[:30]:
            icon = "📁" if f["mimeType"] == "application/vnd.google-apps.folder" else "📄"
            size = humanbytes(int(f.get("size", 0) or 0)) if f.get("size") else ""
            text += f"{icon} [{f['name']}]({f.get('webViewLink', '#')}) {size}\n"
        text += f"\n🌐 Full browser: {Config.WEB_BASE_URL}/picker?user_id={user_id}"
        await message.reply_text(text, quote=True)
    except Exception as e:
        await message.reply_text(f"❌ **Failed:** `{str(e)[:500]}`", quote=True)

@Client.on_message(filters.private & filters.command(["search"]))
async def search_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/search <query>`", quote=True)
        return
    query = " ".join(message.command[1:])
    try:
        helper = get_drive_helper(user_id=user_id)
        results = helper.search_files(query)
        if not results:
            await message.reply_text(f"🔍 No results for `{query}`", quote=True)
            return
        text = f"🔍 **Results for `{query}`** ({len(results)})\n\n"
        for f in results[:20]:
            icon = "📁" if f["mimeType"] == "application/vnd.google-apps.folder" else "📄"
            text += f"{icon} [{f['name']}]({f.get('webViewLink', '#')})\n"
        await message.reply_text(text, quote=True)
    except Exception as e:
        await message.reply_text(f"❌ **Failed:** `{str(e)[:500]}`", quote=True)

@Client.on_message(filters.private & filters.command(["delete", "rm"]))
async def delete_cmd(client: Client, message: Message):
    user_id = message.from_user.id
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/delete <file_or_folder_url>`", quote=True)
        return
    fid = get_id_from_url(message.command[1])
    if fid == "NotFound":
        await message.reply_text("❌ Invalid URL", quote=True)
        return
    try:
        helper = get_drive_helper(user_id=user_id)
        helper.delete_file(fid)
        await message.reply_text(f"🗑️ **Deleted** `{fid}`", quote=True)
    except Exception as e:
        await message.reply_text(f"❌ **Failed:** `{str(e)[:500]}`", quote=True)

@Client.on_callback_query(filters.create(lambda _, __, q: q.data.startswith("list_folders") or q.data.startswith("set_folder_")))
async def folder_callback(client: Client, query):
    user_id = query.from_user.id
    data = query.data
    if data == "list_folders_root":
        try:
            helper = get_drive_helper(user_id=user_id)
            folders = helper.list_folders("root")
            if not folders:
                await query.message.edit_text("📁 No folders in My Drive root.\n\nUse Web Picker to create/select.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🌐 Web Picker", url=f"{Config.WEB_BASE_URL}/picker?user_id={user_id}")]]))
                return
            buttons = []
            for f in folders[:20]:
                buttons.append([InlineKeyboardButton(f"📁 {f['name'][:30]}", callback_data=f"set_folder_{f['id']}")])
            buttons.append([InlineKeyboardButton("🌐 Open Web Picker", url=f"{Config.WEB_BASE_URL}/picker?user_id={user_id}")])
            await query.message.edit_text("📂 **Select Folder**\nTap to set as upload destination:", reply_markup=InlineKeyboardMarkup(buttons))
        except Exception as e:
            await query.message.edit_text(f"❌ Failed: `{str(e)[:400]}`")
    elif data.startswith("set_folder_"):
        fid = data.replace("set_folder_", "")
        try:
            helper = get_drive_helper(user_id=user_id)
            meta = helper.get_file_metadata(fid)
            db.set_default_folder(user_id, fid, meta.get("name"))
            await query.message.edit_text(f"✅ **Folder set!**\n\n📁 `{meta.get('name')}`\nAll uploads will go here now.")
        except Exception as e:
            await query.message.edit_text(f"❌ Failed: `{str(e)[:400]}`")
    await query.answer()
