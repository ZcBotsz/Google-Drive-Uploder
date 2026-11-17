import os
import re
import asyncio
from config import Config
from bot.core.logger import get_logger
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.utils import humanbytes, get_caption_filename, get_unique_download_path, robust_download

LOGGER = get_logger(__name__)

class AutomationManager:
    """
    Auto-upload from channels/groups - SEQUENTIAL queue (fixes out-of-order + .temp clashes).
    - Processes one file at a time in FIFO order (Lecture 1, 2, 3...)
    - Uses caption as filename (e.g. "Lecture 1" -> "Lecture 1.mp4")
    - Uses user's CURRENT default folder (synced from website/bot) - fixes folder mismatch
    """
    def __init__(self, client=None):
        self.client = client
        self._queue = asyncio.Queue()
        self._worker_task = None
        self._lock = asyncio.Lock()  # Ensures sequential processing

    def set_client(self, client):
        self.client = client
        # Start worker if not running (will be started in event loop)
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running() and (self._worker_task is None or self._worker_task.done()):
                self._worker_task = asyncio.create_task(self._worker())
                LOGGER.info("🤖 Automation worker started (sequential FIFO)")
        except RuntimeError:
            # No running loop yet - worker will start on first message
            pass

    async def _worker(self):
        """Sequential worker - processes queue one by one in order received."""
        while True:
            try:
                item = await self._queue.get()
                message, rule = item
                try:
                    await self._process_one(message, rule)
                except Exception as e:
                    LOGGER.error(f"Automation worker error: {e}", exc_info=True)
                finally:
                    self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOGGER.error(f"Automation worker loop error: {e}", exc_info=True)
                await asyncio.sleep(1)

    async def handle_channel_message(self, message):
        """Called for every channel/group message - enqueue for sequential processing."""
        if not message.chat:
            return
        channel_id = message.chat.id
        rule = db.get_automation_by_channel(channel_id)
        if not rule:
            return
        # Quick check: has file?
        has_file = any([message.document, message.video, message.audio, message.photo, message.voice, message.animation])
        if not has_file:
            return

        # Ensure worker running
        if self._worker_task is None or self._worker_task.done():
            try:
                self._worker_task = asyncio.create_task(self._worker())
            except RuntimeError:
                pass

        # Enqueue (FIFO - preserves Lecture 1, 2, 3 order)
        await self._queue.put((message, rule))
        qsize = self._queue.qsize()
        LOGGER.info(f"🤖 Automation queued: channel={channel_id} msg={message.id} queue_size={qsize} caption={getattr(message, 'caption', '')[:40]!r}")

    async def _process_one(self, message, rule):
        """Process single automation item (called sequentially by worker)."""
        channel_id = message.chat.id
        user_id = rule["user_id"]

        # 🎯 FOLDER SYNC FIX: Always use user's CURRENT default folder (synced from website/bot)
        # Rule folder is fallback only if default is 'root' and rule has specific folder.
        default_folder_id, default_folder_name = db.get_default_folder(user_id)
        rule_folder_id = rule["folder_id"]
        rule_folder_name = rule["folder_name"]

        # Priority: Default folder (user's latest selection) wins over rule folder
        # This fixes "website selection not reflected in bot" + "automation uses different folder"
        if default_folder_id != "root":
            folder_id, folder_name = default_folder_id, default_folder_name
            folder_source = "default (synced)"
        elif rule_folder_id and rule_folder_id != "root":
            folder_id, folder_name = rule_folder_id, rule_folder_name
            folder_source = "automation rule"
        else:
            folder_id, folder_name = "root", "My Drive"
            folder_source = "root"

        # Get file info
        file_obj = None
        original_name = None
        if message.document:
            file_obj = message.document
            original_name = file_obj.file_name or "document"
        elif message.video:
            file_obj = message.video
            original_name = file_obj.file_name or "video.mp4"
        elif message.audio:
            file_obj = message.audio
            original_name = file_obj.file_name or "audio.mp3"
        elif message.photo:
            file_obj = message.photo[-1] if message.photo else None
            original_name = f"photo_{message.id}.jpg"
        elif message.voice:
            file_obj = message.voice
            original_name = "voice.ogg"
        elif message.animation:
            file_obj = message.animation
            original_name = file_obj.file_name or "animation.gif"
        else:
            return

        if not file_obj:
            return

        # 🎯 Caption → Filename (e.g. "Lecture 1" -> "Lecture 1.mp4")
        file_name = get_caption_filename(message, original_name)

        # Auto-rename pattern (rule + user setting)
        for pattern in [rule.get("rename_pattern"), (db.get_user(user_id) or {}).get("auto_rename")]:
            if pattern:
                try:
                    file_name = re.sub(pattern, "", file_name).strip() or file_name
                except:
                    pass

        LOGGER.info(f"🤖 Automation: {channel_id} -> user {user_id} folder={folder_name} ({folder_source}) file={file_name} caption={getattr(message, 'caption', '')[:50]!r}")

        # Unique path per message (prevents .temp clashes)
        dest = get_unique_download_path(Config.DOWNLOAD_DIR, file_name, message.id)
        drive_filename = file_name

        # Download with lock (sequential) + robust retry
        async with self._lock:
            try:
                await robust_download(message, dest, max_retries=2)
            except Exception as e:
                LOGGER.error(f"Automation download failed for {drive_filename}: {e}", exc_info=True)
                return

            if not os.path.exists(dest):
                LOGGER.error(f"Automation download: file not found after download: {dest}")
                return

            # Upload
            try:
                helper = get_drive_helper(user_id=user_id)
                result = helper.upload_file(dest, drive_filename, folder_id)
                file_id = result.get("id")
                link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
                size = os.path.getsize(dest)
                db.add_upload(user_id, drive_filename, size, file_id, link, folder_id, source="automation")
                LOGGER.info(f"🤖 Automation uploaded: {drive_filename} -> {link} (folder: {folder_name})")
                if self.client:
                    try:
                        caption_preview = f"\n🏷️ `{message.caption[:60]}`" if message.caption else ""
                        await self.client.send_message(
                            user_id,
                            f"🤖 **Auto-uploaded!**\n📄 [{drive_filename}]({link})\n📁 `{folder_name}`{caption_preview}\n📺 From: `{message.chat.title or channel_id}`"
                        )
                    except:
                        pass
            except Exception as e:
                LOGGER.error(f"Automation upload failed for {drive_filename}: {e}", exc_info=True)
            finally:
                try:
                    if os.path.exists(dest):
                        os.remove(dest)
                    temp = dest + ".temp"
                    if os.path.exists(temp):
                        os.remove(temp)
                except:
                    pass

automation_manager = AutomationManager()
