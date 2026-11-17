#!/usr/bin/env python3
"""
Telegram → Google Drive Uploader Bot — Advanced v2.0 (Render Fixed)
Render-ready: Web (FastAPI) + Bot (Pyrogram) in SINGLE event loop, main thread.
Fixes: "No open ports detected" + bot not replying on Render.
- Web binds FIRST on $PORT so Render health check passes
- Bot + Web share one asyncio loop (no thread signal issues)
- Plugin load verification + message logging for debugging
"""
import os
import sys
import asyncio
import time
import urllib.request
from pyrogram import Client
from config import Config
from bot.core.logger import get_logger
from bot.core import database as db
from bot.helper.queue_manager import queue_manager
from bot.helper.automation import automation_manager

LOGGER = get_logger(__name__)

def keep_alive_pinger():
    """Self-ping /health to prevent Render free tier spin-down"""
    if not Config.KEEP_ALIVE or not Config.WEB_BASE_URL or "localhost" in Config.WEB_BASE_URL:
        return
    url = f"{Config.WEB_BASE_URL}/health"
    LOGGER.info(f"💓 Keep-alive pinger started → {url} every 10 min")
    while True:
        time.sleep(600)
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                LOGGER.info(f"💓 Keep-alive OK: {r.status}")
        except Exception as e:
            LOGGER.warning(f"💓 Keep-alive failed: {e}")

async def main_async():
    # Validate config
    if not Config.validate():
        LOGGER.error("❌ Missing BOT_TOKEN/API_ID/API_HASH. Set them in Render → Environment Variables!")
        LOGGER.warning("⚠️ Web dashboard will run but bot won't connect. Fix env vars and redeploy!")

    db.init_db()
    os.makedirs(Config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(Config.TOKEN_DIR, exist_ok=True)
    os.makedirs(Config.ACCOUNTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(Config.DOWNLOAD_DIR, "web_uploads"), exist_ok=True)
    LOGGER.info(f"📁 DATA_DIR={Config.DATA_DIR} DB={Config.DB_PATH} TOKENS={Config.TOKEN_DIR}")
    LOGGER.info(f"🚀 Starting Telegram Drive Uploader Bot (Advanced v2.0)...")
    LOGGER.info(f"🌐 Web: {Config.WEB_BASE_URL} (port {Config.WEB_PORT})")
    LOGGER.info(f"👑 Admins: {Config.ADMIN_IDS}")

    # --- Start Web Server FIRST (critical for Render port binding) ---
    import uvicorn
    from web.app import app as web_app

    uvicorn_config = uvicorn.Config(
        web_app,
        host=Config.WEB_HOST,
        port=Config.WEB_PORT,
        log_level="info",
        access_log=True,
    )
    server = uvicorn.Server(uvicorn_config)
    # Start web as background task in SAME event loop (main thread)
    web_task = asyncio.create_task(server.serve())
    LOGGER.info(f"🌐 Web server starting on {Config.WEB_HOST}:{Config.WEB_PORT} (Render PORT={os.getenv('PORT','not set')})")

    # Give web 2 seconds to bind port before starting bot (Render port scan)
    await asyncio.sleep(2)
    if server.started:
        LOGGER.info("✅ Web server bound successfully - Render port check will pass")
    else:
        LOGGER.warning("⚠️ Web server not started yet, continuing anyway...")

    # Keep-alive in background (thread is fine for simple HTTP ping)
    if Config.KEEP_ALIVE and "localhost" not in Config.WEB_BASE_URL:
        import threading
        threading.Thread(target=keep_alive_pinger, daemon=True, name="keep-alive").start()

    # If no Telegram config, keep web alive only
    if not Config.BOT_TOKEN or not Config.API_ID or not Config.API_HASH:
        LOGGER.error("❌ Cannot start Telegram bot without BOT_TOKEN/API_ID/API_HASH. Web dashboard is running - check Render Logs.")
        await web_task
        return

    # --- Start Telegram Bot ---
    plugins = dict(root="bot.modules")
    app = Client(
        "telegram-drive-uploader",
        bot_token=Config.BOT_TOKEN,
        api_id=Config.API_ID,
        api_hash=Config.API_HASH,
        plugins=plugins,
        workers=8,
        sleep_threshold=60,
    )

    queue_manager.set_client(app)
    automation_manager.set_client(app)
    # NOTE: Plugins load during app.start() -> initialize() -> load_plugins()
    # So handler verification must happen AFTER 'async with app' (see below).

    async def set_commands():
        try:
            from pyrogram.types import BotCommand
            cmds = [
                BotCommand("start", "Start bot"),
                BotCommand("ping", "Test bot (pong)"),
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

    try:
        async with app:
            # Plugins are now loaded (initialize() called load_plugins())
            try:
                count = 0
                if hasattr(app, 'dispatcher') and hasattr(app.dispatcher, 'handlers'):
                    for group_handlers in app.dispatcher.handlers.values():
                        count += len(group_handlers)
                LOGGER.info(f"🔌 Plugins loaded. Total handlers registered: {count}")
                if count == 0:
                    LOGGER.error("❌ NO handlers! Plugins failed. Check Render Logs for [LOAD] errors above.")
                else:
                    LOGGER.info("✅ Handlers ready - bot will reply to /start, /ping, etc.")
            except Exception as e:
                LOGGER.warning(f"Handler check: {e} (plugins still load via Pyrogram [LOAD] logs above)")

            await set_commands()
            LOGGER.info("✅ Bot is running! Send /start in Telegram")
            LOGGER.info(f"✅ Health: {Config.WEB_BASE_URL}/health")
            LOGGER.info("✅ Listening for Telegram updates... (send /ping to test)")
            # Wait forever - both bot (async with) and web (web_task) run in same loop
            await web_task
    except Exception as e:
        # Bot failed (e.g. invalid token) - keep web alive for debugging, don't crash entire service
        LOGGER.error(f"❌ Telegram bot failed: {e}", exc_info=True)
        LOGGER.error("❌ Check BOT_TOKEN/API_ID/API_HASH in Render env vars. Web dashboard still running for debugging.")
        LOGGER.error(f"❌ Web: {Config.WEB_BASE_URL}/health should still respond")
        await web_task

def main():
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        LOGGER.info("👋 Bot stopped by user")
    except Exception as e:
        LOGGER.error(f"Bot crashed: {e}", exc_info=True)
        raise

if __name__ == "__main__":
    main()
