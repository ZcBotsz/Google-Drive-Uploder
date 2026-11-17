#!/usr/bin/env python3
"""
Telegram → Google Drive Uploader Bot — Advanced v2.0
Render-ready: Web service on $PORT + Bot polling in single process.
Features: OAuth per-user, single + batch, automation, clone, admin, settings, web folder picker.
"""
import os
import sys
import asyncio
import threading
import time
import urllib.request
from pyrogram import Client
from config import Config
from bot.core.logger import get_logger
from bot.core import database as db
from bot.helper.queue_manager import queue_manager
from bot.helper.automation import automation_manager

LOGGER = get_logger(__name__)

def start_web():
    """Run FastAPI web dashboard in background thread (Render web service)"""
    try:
        from web.app import run_web
        LOGGER.info(f"🌐 Starting Web Dashboard on {Config.WEB_HOST}:{Config.WEB_PORT} (Render PORT={os.getenv('PORT','not set')})")
        run_web()
    except Exception as e:
        LOGGER.error(f"Web server failed: {e}", exc_info=True)

def keep_alive_pinger():
    """Self-ping /health to prevent Render free tier spin-down (optional, needs WEB_BASE_URL)"""
    if not Config.KEEP_ALIVE or not Config.WEB_BASE_URL or "localhost" in Config.WEB_BASE_URL:
        return
    url = f"{Config.WEB_BASE_URL}/health"
    LOGGER.info(f"💓 Keep-alive pinger started → {url} every 10 min")
    while True:
        time.sleep(600)  # 10 min
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                LOGGER.info(f"💓 Keep-alive OK: {r.status}")
        except Exception as e:
            LOGGER.warning(f"💓 Keep-alive failed: {e}")

def main():
    # Validate config - fail fast with clear Render logs
    if not Config.validate():
        LOGGER.error("❌ Missing BOT_TOKEN/API_ID/API_HASH. Set them in Render → Environment Variables and redeploy.")
        # Still start web so /health works and user sees logs? No - exit so Render shows crash clearly.
        # For debugging, start web briefly then exit.
        # sys.exit(1)
        LOGGER.warning("⚠️ Continuing without valid Telegram config - web dashboard will run but bot won't connect. Fix env vars!")

    db.init_db()
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(Config.TOKEN_DIR, exist_ok=True)
    os.makedirs(Config.ACCOUNTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(Config.DOWNLOAD_DIR, "web_uploads"), exist_ok=True)
    LOGGER.info(f"📁 DATA_DIR={Config.DATA_DIR} DB={Config.DB_PATH} TOKENS={Config.TOKEN_DIR}")

    # Start web dashboard in background (required for Render Web Service health check)
    web_thread = threading.Thread(target=start_web, daemon=True, name="web-server")
    web_thread.start()

    # Keep-alive for Render free tier
    if Config.KEEP_ALIVE:
        ka_thread = threading.Thread(target=keep_alive_pinger, daemon=True, name="keep-alive")
        ka_thread.start()

    plugins = dict(root="bot.modules")
    LOGGER.info("🚀 Starting Telegram Drive Uploader Bot (Advanced v2.0)...")
    LOGGER.info(f"🌐 Web: {Config.WEB_BASE_URL} (port {Config.WEB_PORT})")
    LOGGER.info(f"👑 Admins: {Config.ADMIN_IDS}")

    if not Config.BOT_TOKEN or not Config.API_ID or not Config.API_HASH:
        LOGGER.error("❌ Cannot start Telegram bot without BOT_TOKEN/API_ID/API_HASH. Web dashboard is running - check Render Logs.")
        # Keep web alive so health check passes, but log error
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass
        return

    app = Client(
        "telegram-drive-uploader",
        bot_token=Config.BOT_TOKEN,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        plugins=plugins,
        workers=8,
        # Flood wait handling
        sleep_threshold=60,
    )

    queue_manager.set_client(app)
    automation_manager.set_client(app)

    async def set_commands():
        try:
            from pyrogram.types import BotCommand
            cmds = [
                BotCommand("start", "Start bot"),
                BotCommand("connect", "Connect Google Drive"),
                BotCommand("folders", "Choose upload folder (Web)"),
                BotCommand("setfolder", "Set folder by URL"),
                BotCommand("newfolder", "Create new folder"),
                BotCommand("list", "List Drive folder"),
                BotCommand("search", "Search Drive"),
                BotCommand("clone", "Clone Drive file/folder"),
                BotCommand("batch", "Batch upload mode"),
                BotCommand("done", "Start batch upload"),
                BotCommand("cancel", "Cancel uploads"),
                BotCommand("auto", "Automation (channel auto-upload)"),
                BotCommand("settings", "Your settings"),
                BotCommand("status", "Drive storage & stats"),
                BotCommand("web", "Web dashboard links"),
                BotCommand("revoke", "Disconnect Drive"),
                BotCommand("help", "Help"),
            ]
            if Config.ADMIN_IDS:
                cmds.append(BotCommand("admin", "Admin panel"))
            await app.set_bot_commands(cmds)
            LOGGER.info("✅ Bot commands set")
        except Exception as e:
            LOGGER.warning(f"Set commands failed: {e}")

    async def run():
        async with app:
            await set_commands()
            LOGGER.info("✅ Bot is running! Send /start in Telegram")
            LOGGER.info(f"✅ Health: {Config.WEB_BASE_URL}/health")
            await asyncio.Event().wait()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        LOGGER.info("👋 Bot stopped by user")
    except Exception as e:
        LOGGER.error(f"Bot crashed: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
