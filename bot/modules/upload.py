import os
import asyncio
from pyrogram import Client, filters
from pyrogram.types import Message
from config import Config
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.downloader import download_file
from bot.helper.utils import humanbytes, get_id_from_url, is_gdrive_link, is_direct_link, extract_urls
from bot.helper.queue_manager import queue_manager
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)

def _get_file_info(message: Message):
    if message.document:
        return message.document, message.document.file_name or "document"
    if message.video:
        return message.video, message.video.file_name or "video.mp4"
    if message.audio:
        return message.audio, message.audio.file_name or "audio.mp3"
    if message.voice:
        return message.voice, "voice.ogg"
    if message.photo:
        return message.photo[-1], f"photo_{message.id}.jpg"
    if message.animation:
        return message.animation, message.animation.file_name or "animation.gif"
    if message.sticker:
        return message.sticker, "sticker.webp"
    return None, None

async def _check_auth(message: Message):
    user_id = message.from_user.id
    # Banned check (advanced)
    if db.is_banned(user_id):
        u = db.get_user(user_id)
        reason = u.get("ban_reason", "") if u else ""
        await message.reply_text(f"🚫 **You are banned.**\nReason: `{reason}`\nContact admin if you think this is a mistake.", quote=True)
        return False
    u = db.get_user(user_id)
    if not u or not u.get("is_authorized"):
        # Try owner token fallback - if owner token exists, allow
        if os.path.exists("token.pickle") or Config.USE_SERVICE_ACCOUNTS:
            return True
        await message.reply_text("❌ **Not connected!**\nRun `/connect` to authorize your Google Drive first.", quote=True)
        return False
    # File size check (advanced)
    file_obj, _ = _get_file_info(message)
    if file_obj and file_obj.file_size:
        size_mb = file_obj.file_size / (1024*1024)
        if size_mb > Config.MAX_FILE_SIZE_MB:
            await message.reply_text(f"❌ **File too large:** `{humanbytes(file_obj.file_size)}`\nMax allowed: `{Config.MAX_FILE_SIZE_MB} MB`", quote=True)
            return False
    return True

@Client.on_message(filters.private & (filters.document | filters.video | filters.audio | filters.photo | filters.voice | filters.animation))
async def handle_telegram_file(client: Client, message: Message):
    if not await _check_auth(message):
        return
    user_id = message.from_user.id
    file_obj, file_name = _get_file_info(message)
    if not file_obj:
        return

    # Check batch mode - if user is in batch mode, queue it
    # For now, direct upload (single) + queue if multiple rapid files
    folder_id, folder_name = db.get_default_folder(user_id)
    status_msg = await message.reply_text(f"⬇️ **Downloading from Telegram...**\n📄 `{file_name}`\n📁 Target: `{folder_name}`", quote=True)

    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    dest = os.path.join(Config.DOWNLOAD_DIR, file_name)
    # Avoid overwrite
    base, ext = os.path.splitext(dest)
    counter = 1
    while os.path.exists(dest):
        dest = f"{base}_{counter}{ext}"
        counter += 1

    downloaded = 0
    total = file_obj.file_size or 0
    last_edit = 0

    def progress(current, total_size):
        nonlocal downloaded, last_edit
        downloaded = current
        # Throttle edits
        import time
        now = time.time()
        if now - last_edit < 2:
            return
        last_edit = now
        pct = (current / total_size * 100) if total_size else 0
        # Can't await in sync callback, so just update DB queue
        pass

    try:
        await message.download(file_path=dest, progress=progress)
    except Exception as e:
        await status_msg.edit_text(f"❌ **Download failed:** `{e}`")
        return

    # Upload to Drive
    await status_msg.edit_text(f"📤 **Uploading to Google Drive...**\n📄 `{file_name}`\n📁 `{folder_name}`\n💾 `{humanbytes(total)}`")
    try:
        helper = get_drive_helper(user_id=user_id)
        # Progress callback for upload - update message periodically via async task
        upload_progress = {"pct": 0}
        async def progress_updater():
            while True:
                await asyncio.sleep(2)
                try:
                    await status_msg.edit_text(f"📤 **Uploading...**\n📄 `{file_name}`\n📁 `{folder_name}`\nProgress: `{upload_progress['pct']:.1f}%`")
                except:
                    break

        updater_task = asyncio.create_task(progress_updater())

        def up_cb(pct, done, tot):
            upload_progress["pct"] = pct

        result = helper.upload_file(dest, file_name, folder_id, up_cb)
        updater_task.cancel()
        file_id = result.get("id")
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
        size = os.path.getsize(dest)
        db.add_upload(user_id, file_name, size, file_id, link, folder_id, source="telegram")
        await status_msg.edit_text(f"✅ **Uploaded Successfully!**\n\n📄 [{file_name}]({link})\n📁 Folder: `{folder_name}`\n💾 Size: `{humanbytes(size)}`\n🔗 [Open in Drive]({link})")
    except Exception as e:
        LOGGER.error(f"Upload failed: {e}")
        await status_msg.edit_text(f"❌ **Upload failed:**\n`{str(e)[:800]}`")
    finally:
        try:
            os.remove(dest)
        except:
            pass

@Client.on_message(filters.private & filters.text & ~filters.command(["start", "help", "connect", "auth", "revoke", "folders", "setfolder", "clone", "copy", "batch", "done", "cancel", "status", "web", "auto", "search", "list", "delete", "rename", "clear"]))
async def handle_link_message(client: Client, message: Message):
    text = message.text.strip()
    urls = extract_urls(text)
    if not urls:
        return
    # If waiting for auth code, skip (handled in auth.py)
    user_id = message.from_user.id
    # Check if this looks like auth code (no http)
    if not any(is_direct_link(u) for u in urls):
        return
    if not await _check_auth(message):
        return

    folder_id, folder_name = db.get_default_folder(user_id)

    # Single vs Batch: if multiple URLs, queue them
    if len(urls) > 1:
        await message.reply_text(f"📦 **Batch mode:** Queued {len(urls)} links. Processing...")
        for url in urls:
            db.add_to_queue(user_id, "link", url, chat_id=message.chat.id, message_id=message.id)
        queue_manager.set_client(client)
        await queue_manager.process_queue(user_id, message.chat.id)
        return

    url = urls[0]
    # Google Drive link -> clone
    if is_gdrive_link(url):
        await handle_gdrive_clone(client, message, url)
        return

    # Direct link -> download & upload
    status_msg = await message.reply_text(f"⬇️ **Downloading...**\n`{url[:100]}`", quote=True)
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    local_path = None
    try:
        def dl_cb(pct, done, total):
            pass
        local_path = await download_file(url, Config.DOWNLOAD_DIR, dl_cb)
        file_name = os.path.basename(local_path)
        await status_msg.edit_text(f"📤 **Uploading to Drive...**\n📄 `{file_name}`\n📁 `{folder_name}`")
        helper = get_drive_helper(user_id=user_id)
        result = helper.upload_file(local_path, file_name, folder_id)
        file_id = result.get("id")
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
        size = os.path.getsize(local_path)
        db.add_upload(user_id, file_name, size, file_id, link, folder_id, source="link")
        await status_msg.edit_text(f"✅ **Uploaded!**\n\n📄 [{file_name}]({link})\n📁 Folder: `{folder_name}`\n💾 Size: `{humanbytes(size)}`")
    except Exception as e:
        LOGGER.error(f"Link upload failed: {e}")
        await status_msg.edit_text(f"❌ **Failed:**\n`{str(e)[:800]}`")
    finally:
        if local_path and os.path.exists(local_path):
            try:
                os.remove(local_path)
            except:
                pass

async def handle_gdrive_clone(client: Client, message: Message, url: str):
    user_id = message.from_user.id
    folder_id, folder_name = db.get_default_folder(user_id)
    file_id = get_id_from_url(url)
    if file_id == "NotFound":
        await message.reply_text("❌ **Invalid Drive URL**", quote=True)
        return
    status_msg = await message.reply_text(f"📋 **Cloning from Drive...**\n`{file_id}`\n📁 To: `{folder_name}`", quote=True)
    try:
        helper = get_drive_helper(user_id=user_id)
        meta = helper.get_file_metadata(file_id)
        if meta.get("mimeType") == "application/vnd.google-apps.folder":
            new_id, total = helper.clone_folder(file_id, folder_id)
            link = f"https://drive.google.com/drive/folders/{new_id}"
            name = meta.get("name")
            await status_msg.edit_text(f"✅ **Folder Cloned!**\n\n📁 [{name}]({link})\n📄 Files: `{total}`\n📁 To: `{folder_name}`")
        else:
            result = helper.copy_file(file_id, folder_id)
            link = result.get("webViewLink") or f"https://drive.google.com/file/d/{result.get('id')}/view"
            name = result.get("name")
            size = int(meta.get("size", 0) or 0)
            db.add_upload(user_id, name, size, result.get("id"), link, folder_id, source="gdrive_clone")
            await status_msg.edit_text(f"✅ **Cloned!**\n\n📄 [{name}]({link})\n📁 To: `{folder_name}`\n💾 Size: `{humanbytes(size)}`")
    except Exception as e:
        LOGGER.error(f"Clone failed: {e}")
        await status_msg.edit_text(f"❌ **Clone failed:**\n`{str(e)[:800]}`")

@Client.on_message(filters.private & filters.command(["clone", "copy"]))
async def clone_cmd(client: Client, message: Message):
    if len(message.command) < 2:
        await message.reply_text("**Usage:** `/clone <gdrive_url>`\nExample: `/clone https://drive.google.com/file/d/xxx/view`", quote=True)
        return
    if not await _check_auth(message):
        return
    await handle_gdrive_clone(client, message, message.command[1])

@Client.on_message(filters.private & filters.command(["cancel"]))
async def cancel_cmd(client: Client, message: Message):
    queue_manager.request_cancel(message.from_user.id)
    db.clear_queue(message.from_user.id)
    await message.reply_text("🛑 **Cancelled.** All queued uploads stopped.", quote=True)
