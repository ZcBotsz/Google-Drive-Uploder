import asyncio
import os
from collections import defaultdict
from config import Config
from bot.core.logger import get_logger
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.downloader import download_file
from bot.helper.utils import humanbytes, get_id_from_url, is_gdrive_link

LOGGER = get_logger(__name__)

class QueueManager:
    """Batch queue manager - like WZML-X task queue"""
    def __init__(self, client=None):
        self.client = client
        self.active_tasks = {}  # user_id -> asyncio.Task
        self.cancel_flags = {}  # user_id -> bool
        self.semaphore = asyncio.Semaphore(Config.MAX_CONCURRENT_UPLOADS)

    def set_client(self, client):
        self.client = client

    def request_cancel(self, user_id: int):
        self.cancel_flags[user_id] = True

    def is_cancelled(self, user_id: int):
        return self.cancel_flags.get(user_id, False)

    async def process_queue(self, user_id: int, chat_id: int):
        """Process all queued items for user sequentially"""
        if user_id in self.active_tasks and not self.active_tasks[user_id].done():
            return
        self.cancel_flags[user_id] = False

        async def _run():
            queue = db.get_queue(user_id)
            if not queue:
                return
            for item in queue:
                if self.is_cancelled(user_id):
                    db.update_queue(item["id"], status="cancelled")
                    continue
                if item["status"] in ("completed", "failed", "cancelled"):
                    continue
                try:
                    await self._process_item(item, user_id, chat_id)
                except Exception as e:
                    LOGGER.error(f"Queue item {item['id']} failed: {e}")
                    db.update_queue(item["id"], status="failed")
            # Clear completed queue
            db.clear_queue(user_id)

        task = asyncio.create_task(_run())
        self.active_tasks[user_id] = task
        return task

    async def _process_item(self, item, user_id, chat_id):
        qid = item["id"]
        source_type = item["source_type"]
        source_data = item["source_data"]
        db.update_queue(qid, status="downloading", progress=0)

        # Get folder
        folder_id, folder_name = db.get_default_folder(user_id)
        user = db.get_user(user_id)
        if not user or not user.get("is_authorized"):
            # Try owner token / SA fallback
            pass

        try:
            helper = get_drive_helper(user_id=user_id)
        except Exception as e:
            LOGGER.error(f"Drive auth failed for {user_id}: {e}")
            if self.client:
                await self.client.send_message(chat_id, f"❌ **Drive not connected!**\nRun `/connect` to authorize your Google Drive.\n`{e}`")
            db.update_queue(qid, status="failed")
            return

        local_path = None
        try:
            if source_type == "link":
                # Direct link download
                if self.client:
                    status_msg = await self.client.send_message(chat_id, f"⬇️ **Downloading...**\n`{source_data[:80]}`")
                else:
                    status_msg = None

                def dl_progress(pct, done, total):
                    db.update_queue(qid, progress=pct)

                local_path = await download_file(source_data, Config.DOWNLOAD_DIR, dl_progress)
                if self.is_cancelled(user_id):
                    raise ValueError("Cancelled")

                # Upload
                db.update_queue(qid, status="uploading", progress=0)
                if status_msg:
                    await status_msg.edit_text(f"📤 **Uploading to Drive...**\n📁 `{folder_name}`\n`{os.path.basename(local_path)}`")

                def up_progress(pct, done, total):
                    db.update_queue(qid, progress=pct)

                result = helper.upload_file(local_path, os.path.basename(local_path), folder_id, up_progress, lambda: self.is_cancelled(user_id))
                file_id = result.get("id")
                link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
                size = os.path.getsize(local_path) if os.path.exists(local_path) else 0
                db.add_upload(user_id, os.path.basename(local_path), size, file_id, link, folder_id, source="link")
                db.update_queue(qid, status="completed", progress=100)
                if status_msg:
                    await status_msg.edit_text(f"✅ **Uploaded!**\n📄 [{os.path.basename(local_path)}]({link})\n📁 Folder: `{folder_name}`\n💾 Size: `{humanbytes(size)}`")
                try:
                    os.remove(local_path)
                except:
                    pass

            elif source_type == "gdrive":
                file_id = get_id_from_url(source_data)
                if file_id == "NotFound":
                    raise ValueError("Invalid Drive URL")
                if self.client:
                    status_msg = await self.client.send_message(chat_id, f"📋 **Cloning from Drive...**\n`{file_id}`")
                else:
                    status_msg = None
                meta = helper.get_file_metadata(file_id)
                if meta.get("mimeType") == "application/vnd.google-apps.folder":
                    new_id, total = helper.clone_folder(file_id, folder_id)
                    link = f"https://drive.google.com/drive/folders/{new_id}"
                    name = meta.get("name")
                else:
                    result = helper.copy_file(file_id, folder_id)
                    link = result.get("webViewLink") or f"https://drive.google.com/file/d/{result.get('id')}/view"
                    name = result.get("name")
                db.add_upload(user_id, name, int(meta.get("size", 0) or 0), file_id, link, folder_id, source="gdrive_clone")
                db.update_queue(qid, status="completed", progress=100)
                if status_msg:
                    await status_msg.edit_text(f"✅ **Cloned!**\n📄 [{name}]({link})\n📁 Folder: `{folder_name}`")

            elif source_type == "telegram":
                # Telegram file already downloaded by handler - source_data is local path
                local_path = source_data
                if not os.path.exists(local_path):
                    raise FileNotFoundError("Telegram file not found")
                db.update_queue(qid, status="uploading", progress=0)
                def up_progress2(pct, done, total):
                    db.update_queue(qid, progress=pct)
                result = helper.upload_file(local_path, os.path.basename(local_path), folder_id, up_progress2, lambda: self.is_cancelled(user_id))
                file_id = result.get("id")
                link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
                size = os.path.getsize(local_path)
                db.add_upload(user_id, os.path.basename(local_path), size, file_id, link, folder_id, source="telegram")
                db.update_queue(qid, status="completed", progress=100)
                if self.client:
                    await self.client.send_message(chat_id, f"✅ **Uploaded!**\n📄 [{os.path.basename(local_path)}]({link})\n📁 Folder: `{folder_name}`\n💾 Size: `{humanbytes(size)}`")
                try:
                    os.remove(local_path)
                except:
                    pass

        except Exception as e:
            LOGGER.error(f"Process item failed: {e}")
            db.update_queue(qid, status="failed")
            if self.client:
                await self.client.send_message(chat_id, f"❌ **Failed:** `{str(e)[:500]}`")
            if local_path and os.path.exists(local_path):
                try:
                    os.remove(local_path)
                except:
                    pass

queue_manager = QueueManager()
