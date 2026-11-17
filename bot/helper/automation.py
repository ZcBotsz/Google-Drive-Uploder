import os
import re
import asyncio
from config import Config
from bot.core.logger import get_logger
from bot.core import database as db
from bot.helper.gdrive import get_drive_helper
from bot.helper.utils import humanbytes

LOGGER = get_logger(__name__)

class AutomationManager:
    """Auto-upload from channels/groups - like WZML-X RSS / MLTB auto-upload"""
    def __init__(self, client=None):
        self.client = client

    def set_client(self, client):
        self.client = client

    async def handle_channel_message(self, message):
        """Called for every channel/group message - check if automation rule matches"""
        if not message.chat:
            return
        channel_id = message.chat.id
        rule = db.get_automation_by_channel(channel_id)
        if not rule:
            return
        # Check if message has file
        file_obj = None
        file_name = None
        if message.document:
            file_obj = message.document
            file_name = file_obj.file_name or "document"
        elif message.video:
            file_obj = message.video
            file_name = file_obj.file_name or "video.mp4"
        elif message.audio:
            file_obj = message.audio
            file_name = file_obj.file_name or "audio.mp3"
        elif message.photo:
            file_obj = message.photo[-1] if message.photo else None
            file_name = f"photo_{message.id}.jpg"
        elif message.voice:
            file_obj = message.voice
            file_name = "voice.ogg"
        else:
            return

        if not file_obj:
            return

        user_id = rule["user_id"]
        folder_id = rule["folder_id"]
        folder_name = rule["folder_name"]

        # Auto-rename pattern
        if rule.get("rename_pattern"):
            try:
                file_name = re.sub(rule["rename_pattern"], "", file_name)
            except:
                pass

        LOGGER.info(f"Automation: {channel_id} -> user {user_id} folder {folder_id} file {file_name}")

        # Download (Pyrogram 2.0.106 uses file_name, not file_path)
        os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
        dest = os.path.join(Config.DOWNLOAD_DIR, file_name)
        try:
            await message.download(file_name=dest)
        except Exception as e:
            LOGGER.error(f"Automation download failed: {e}", exc_info=True)
            return

        # Upload
        try:
            helper = get_drive_helper(user_id=user_id)
            result = helper.upload_file(dest, file_name, folder_id)
            file_id = result.get("id")
            link = result.get("webViewLink") or f"https://drive.google.com/file/d/{file_id}/view"
            size = os.path.getsize(dest)
            db.add_upload(user_id, file_name, size, file_id, link, folder_id, source="automation")
            LOGGER.info(f"Automation uploaded: {link}")
            # Notify user
            if self.client:
                try:
                    await self.client.send_message(user_id, f"🤖 **Auto-uploaded!**\n📄 [{file_name}]({link})\n📁 `{folder_name}`\n📺 From: `{message.chat.title or channel_id}`")
                except:
                    pass
        except Exception as e:
            LOGGER.error(f"Automation upload failed: {e}")
        finally:
            try:
                os.remove(dest)
            except:
                pass

automation_manager = AutomationManager()
