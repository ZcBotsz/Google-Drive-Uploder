import os
import asyncio
import re
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from config import Config
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.downloader import download_file
from bot.helper.utils import (
    humanbytes, get_id_from_url, is_gdrive_link, is_direct_link, extract_urls,
    get_caption_filename, get_unique_download_path, robust_download, progress_bar, format_time
)
from bot.helper.queue_manager import queue_manager
from bot.core.logger import get_logger

LOGGER = get_logger(__name__)

# Lock to prevent concurrent downloads clashing on .temp files (fixes video.mp4.temp error)
_download_lock = asyncio.Lock()

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
        # Photo: largest size. Use message.id for uniqueness.
        # Caption takes priority for filename (handled by caller via get_caption_filename)
        return message.photo[-1], f"photo_{message.id}.jpg"
    if message.animation:
        return message.animation, message.animation.file_name or "animation.gif"
    if message.sticker:
        return message.sticker, "sticker.webp"
    return None, None

async def _check_auth(message: Message):
    user_id = message.from_user.id
    if db.is_banned(user_id):
        u = db.get_user(user_id)
        reason = u.get("ban_reason", "") if u else ""
        await message.reply_text(f"🚫 **You are banned.**\nReason: `{reason}`\nContact admin if you think this is a mistake.", quote=True)
        return False
    u = db.get_user(user_id)
    if not u or not u.get("is_authorized"):
        if os.path.exists("token.pickle") or Config.USE_SERVICE_ACCOUNTS:
            return True
        await message.reply_text("❌ **Not connected!**\nRun `/connect` to authorize your Google Drive first.", quote=True)
        return False
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
    file_obj, original_name = _get_file_info(message)
    if not file_obj:
        return

    # 🎯 Caption → Filename (e.g. "Lecture 1" caption → "Lecture 1.mp4")
    file_name = get_caption_filename(message, original_name)
    # Apply user's auto-rename pattern if set
    u = db.get_user(user_id)
    if u and u.get("auto_rename"):
        try:
            file_name = re.sub(u["auto_rename"], "", file_name).strip() or file_name
        except:
            pass

    folder_id, folder_name = db.get_default_folder(user_id)
    caption_info = f"\n🏷️ Caption: `{message.caption[:50]}`" if message.caption else ""
    status_msg = await message.reply_text(
        f"⬇️ **Downloading from Telegram...**\n📄 `{file_name}`{caption_info}\n📁 Target: `{folder_name}`",
        quote=True
    )

    # Unique path per message to prevent .temp clashes (fixes video.mp4.temp error)
    dest = get_unique_download_path(Config.DOWNLOAD_DIR, file_name, message.id)
    # For Drive upload, use clean file_name (without _messageid suffix)
    drive_filename = file_name

    total = file_obj.file_size or 0
    start_time = asyncio.get_event_loop().time()

    def progress(current, total_size):
        pass  # Progress shown via upload phase; download progress in logs

    try:
        # Serialize downloads to prevent .temp file clashes (fixes concurrent download crash)
        async with _download_lock:
            await robust_download(message, dest, max_retries=2)
    except Exception as e:
        LOGGER.error(f"Telegram download failed for {file_name}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ **Download failed:**\n`{str(e)[:500]}`\n\n💡 Try again or send via Web Dashboard.")
        return

    # Verify file exists
    if not os.path.exists(dest):
        await status_msg.edit_text(f"❌ **Download failed:** File not found after download.\n`{dest}`")
        return

    # Upload to Drive with beautiful progress bar
    await status_msg.edit_text(f"📤 **Uploading to Google Drive...**\n📄 `{drive_filename}`\n📁 `{folder_name}`\n💾 `{humanbytes(total)}`")
    try:
        helper = get_drive_helper(user_id=user_id)
        upload_progress = {"pct": 0, "done": 0, "total": total}
        last_edit = [0]

        async def progress_updater():
            while True:
                await asyncio.sleep(2)
                try:
                    pct = upload_progress["pct"]
                    elapsed = asyncio.get_event_loop().time() - start_time
                    speed = upload_progress["done"] / elapsed if elapsed > 0 else 0
                    eta = ((total - upload_progress["done"]) / speed) if speed > 0 else 0
                    bar = progress_bar(pct, 15)
                    await status_msg.edit_text(
                        f"📤 **Uploading...**\n📄 `{drive_filename}`\n📁 `{folder_name}`\n"
                        f"{bar}\n⚡ `{humanbytes(speed)}/s` • ⏱️ ETA `{format_time(eta)}`"
                    )
                except:
                    break

        updater_task = asyncio.create_task(progress_updater())

        def up_cb(pct, done, tot):
            upload_progress["pct"] = pct
            upload_progress["done"] = done
            if tot:
                upload_progress["total"] = tot

        result = helper.upload_file(dest, drive_filename, folder_id, up_cb)
        updater_task.cancel()
        file_id = result.get("id")
        link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
        size = os.path.getsize(dest)
        db.add_upload(user_id, drive_filename, size, file_id, link, folder_id, source="telegram")
        await status_msg.edit_text(
            f"✅ **Uploaded Successfully!**\n\n"
            f"📄 [{drive_filename}]({link})\n"
            f"📁 Folder: `{folder_name}`\n"
            f"💾 Size: `{humanbytes(size)}`\n"
            f"🔗 [Open in Drive]({link})"
        )
    except Exception as e:
        LOGGER.error(f"Upload failed for {drive_filename}: {e}", exc_info=True)
        await status_msg.edit_text(f"❌ **Upload failed:**\n`{str(e)[:800]}`")
    finally:
        try:
            if os.path.exists(dest):
                os.remove(dest)
            # Clean stale .temp
            temp = dest + ".temp"
            if os.path.exists(temp):
                os.remove(temp)
        except:
            pass

@Client.on_message(filters.private & filters.text & ~filters.command(["start", "help", "connect", "auth", "revoke", "folders", "setfolder", "clone", "copy", "batch", "done", "cancel", "status", "web", "auto", "search", "list", "delete", "rename", "clear", "ping", "test", "admin", "broadcast", "ban", "unban", "users", "restart", "logs", "health", "settings", "setting", "config", "newfolder", "mkdir", "queue", "status_queue", "clearqueue", "automation"]))
async def handle_link_message(client: Client, message: Message):
    text = message.text.strip()
    urls = extract_urls(text)
    if not urls:
        return
    user_id = message.from_user.id
    if not any(is_direct_link(u) for u in urls):
        return
    if not await _check_auth(message):
        return

    folder_id, folder_name = db.get_default_folder(user_id)

    if len(urls) > 1:
        await message.reply_text(f"📦 **Batch mode:** Queued {len(urls)} links. Processing...")
        for url in urls:
            db.add_to_queue(user_id, "link", url, chat_id=message.chat.id, message_id=message.id)
        queue_manager.set_client(client)
        await queue_manager.process_queue(user_id, message.chat.id)
        return

    url = urls[0]
    if is_gdrive_link(url):
        await handle_gdrive_clone(client, message, url)
        return

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
        LOGGER.error(f"Link upload failed: {e}", exc_info=True)
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
        LOGGER.error(f"Clone failed: {e}", exc_info=True)
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
