"""Entry point — bot polling, web server, worker pool and scheduler in ONE loop.

    python -m driveup.main          # or: driveup

Boot order (each step can fail loudly but never half-way):
  1. load + validate config      → exit with a readable list of problems
  2. open SQLite, sweep TTLs     → create the schema on first run
  3. seed the default plans      → free / pro / business
  4. build Drive account manager
  5. build Worker + resume jobs  → interrupted jobs re-queue here
  6. build FastAPI app           → Studio + admin + /health
  7. start uvicorn in-loop       → same loop, so SSE sees live progress
  8. start bot polling           → long-poll, no webhook needed
  9. run the periodic TTL sweep
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from pathlib import Path

from .config import BRAND_NAME, ConfigError, settings as cfg
from .core.access import Access
from .core.progress import ProgressHub
from .core.worker import Worker
from .db import repo
from .db.sql import Database
from .drive.accounts import AccountManager
from .tg.ratelimit import FloodGate

log = logging.getLogger("driveup")

VERSION = "1.0.0"

BANNER = rf"""
  ____       _              _   _ ____
 |  _ \ _ __(_)_   _____   | | | |  _ \
 | | | | '__| \ \ / / _ \  | | | | | | |
 | |_| | |  | |\ V /  __/  | |_| | |_| |
 |____/|_|  |_| \_/ \___|   \___/|____/   {VERSION}
 Telegram → Google Drive · {BRAND_NAME}
"""


def _fatal_if_failed(task: asyncio.Task) -> None:
    """A dead web server means every request is a 502 — fail loudly instead."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is None:
        return
    log.critical("WEB SERVER STOPPED: %s", exc)
    log.critical("Railway/Render will now see a dead container (502) — restarting.")
    os._exit(1)                 # let the platform restart us cleanly


class ServeFailed(RuntimeError):
    """The web server could not start — fatal, and reported as such."""


def setup_logging() -> None:
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)-18s %(message)s",
        datefmt="%H:%M:%S")
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)


async def sweep_loop(db: Database) -> None:
    while True:
        try:
            await asyncio.sleep(300)
            removed = await db.sweep()
            if any(removed.values()):
                log.debug("TTL sweep: %s", removed)
        except asyncio.CancelledError:
            return
        except Exception as e:                             # noqa: BLE001
            log.warning("sweep failed: %s", e)


async def serve(app, port: int, host: str) -> None:
    """Run uvicorn. A failure to bind raises ServeFailed — never a silent exit.

    uvicorn calls `sys.exit(3)` when it cannot bind. Inside a task that is a
    SystemExit nobody re-raises, so the process used to log "web listening"
    and then answer nothing at all (Railway shows that as 502 Bad Gateway).
    """
    import uvicorn
    config = uvicorn.Config(app, host=host, port=port, log_level="warning",
                            access_log=False, lifespan="off", loop="asyncio")
    server = uvicorn.Server(config)
    try:
        await server.serve()
    except SystemExit as e:
        raise ServeFailed(f"could not bind {host}:{port} (is the port already in "
                          f"use, or is $PORT wrong?) — exit code {e.code}") from None
    if not server.started:
        raise ServeFailed(f"uvicorn never started on {host}:{port}")


async def run() -> int:
    print(BANNER, flush=True)
    setup_logging()

    problems = cfg.validate()
    if problems:
        log.error("Configuration is not usable — fix these and restart:")
        for p in problems:
            log.error("  · %s", p)
        return 2
    log.info("config OK — %s", cfg.secrets_summary())
    log.info("scope mode: %s · chunk: %s MB · upload mode: %s",
             cfg.drive_scope_mode, cfg.chunk_bytes // 1024 // 1024, cfg.upload_mode)

    Path(cfg.temp_dir).mkdir(parents=True, exist_ok=True)
    Path(cfg.download_dir).mkdir(parents=True, exist_ok=True)

    db = Database(cfg.db_path)
    await db.connect()
    await db.sweep()
    await repo.seed_plans(db, cfg)
    log.info("database ready at %s", cfg.db_path)

    accounts = AccountManager(db, cfg)

    from .bot.client import BotApp, build_bot, build_dispatcher
    from .web.app import create_app

    bot = build_bot(cfg)
    hub = ProgressHub()
    access = Access(db, cfg, bot=bot)
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)
    worker.paused_global = False
    fg = FloodGate()

    app = BotApp(bot=bot, dp=None, db=db, cfg=cfg, access=access, accounts=accounts,
                 worker=worker, hub=hub, fg=fg)
    app.fg = fg
    dp = build_dispatcher(app)
    app.dp = dp

    # every handler gets the app through the middleware data dict
    @dp.message.middleware()
    @dp.callback_query.middleware()
    async def inject_app(handler, event, data):        # noqa: ANN001
        data["_app"] = app
        return await handler(event, data)

    web = create_app(db=db, cfg=cfg, bot=bot, worker=worker, accounts=accounts,
                     access=access, hub=hub, version=VERSION,
                     log_event=app.log_event, lock_card=app.lock_card)

    await worker.start()
    sweeper = asyncio.create_task(sweep_loop(db), name="ttl-sweep")
    server = asyncio.create_task(serve(web, cfg.port, cfg.host), name="uvicorn")
    server.add_done_callback(_fatal_if_failed)
    log.info("web listening on %s:%s (public: %s)", cfg.host, cfg.port,
             cfg.public_url or "(not set)")
    log.info("health: %s/health", cfg.public_url or f"http://127.0.0.1:{cfg.port}")

    # Signals are wired up *before* anything that talks to the network: a
    # platform deploy sends SIGTERM the moment the port opens, and a blocked
    # get_me() must not swallow it (that produced exit code 143).
    stop = asyncio.Event()

    def _stop(*_a):                                        # noqa: ANN001
        log.info("shutdown signal received")
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, _stop)

    # get_me() is a network call: a cold start or a flaky link must NOT take
    # the server down. The web side is already up and polling retries itself.
    me = None
    for attempt in range(1, 4):
        try:
            me = await bot.get_me()
            break
        except Exception as e:                              # noqa: BLE001
            log.warning("get_me failed (%s/3): %s", attempt, e)
            await asyncio.sleep(2 * attempt)
    if me:
        log.info("bot @%s online — /start to begin", me.username)
        # Put every command in Telegram's own ☰ list, so the typed menu and the
        # button menu show the same set. Cosmetic: never stop the bot over it.
        try:
            from .bot.menus import telegram_command_list
            await bot.set_my_commands(telegram_command_list())
            log.info("command list published to Telegram")
        except Exception as e:                              # noqa: BLE001
            log.warning("could not publish the command list: %s", e)
    else:
        log.warning("Telegram did not answer get_me — starting anyway; "
                    "polling will retry in the background")
    app.started_at = time.time()

    poll = asyncio.create_task(dp.start_polling(bot, handle_signals=False), name="polling")
    watcher = asyncio.create_task(_watch_conflicts(bot, poll, cfg), name="conflict-watch")
    try:
        await stop.wait()
    finally:
        log.info("shutting down — persisting state")
        watcher.cancel()
        poll.cancel()
        await worker.stop()
        sweeper.cancel()
        with contextlib.suppress(Exception):
            await dp.storage.close()
        with contextlib.suppress(Exception):
            await bot.session.close()
        await accounts.close()
        with contextlib.suppress(Exception):
            await db.close()
        log.info("bye")
    return 0


async def _watch_conflicts(bot, poll_task: asyncio.Task, cfg) -> None:
    """Warn loudly when a second instance is polling the same bot token.

    Telegram's `getUpdates` is exclusive: when two processes poll one token,
    both get HTTP 409 `Conflict` and updates are delivered to neither, so the
    bot looks alive in the logs but silently ignores every message. aiogram
    logs the error and retries forever, which is the worst possible UX — so we
    say what is actually wrong, once, in plain language.
    """
    from aiogram.exceptions import TelegramConflictError

    warned = False
    while not poll_task.done():
        await asyncio.sleep(2)
        if warned:
            continue
        # aiogram surfaces the 409 through the polling task's exception, and
        # logs it via the `aiogram.dispatcher` logger. Detect via the bot's
        # session hook: any Conflict seen during the last poll cycle counts.
        try:
            if getattr(bot.session, "conflict_seen", False):
                warned = True
                log.error(
                    "BOT TOKEN IS IN USE BY ANOTHER PROCESS (Telegram 409 Conflict). "
                    "Only one instance may poll a token. Fix: Railway/Render → "
                    "Replicas = 1, stop any other deployment, and make sure no "
                    "other machine is running this bot with the same BOT_TOKEN. "
                    "Updates will be dropped until the duplicate is stopped.")
                bot.session.conflict_seen = False       # report once per burst
                with contextlib.suppress(Exception):
                    await bot.send_message(
                        cfg.owner_id,
                        "⚠️ <b>Two bot instances are running</b>\n\n"
                        "Telegram refused to deliver updates (<code>409 Conflict</code>) "
                        "because another process is polling the same token.\n\n"
                        "Fix: set <b>Replicas = 1</b> and stop any other deployment "
                        "using this token. Then send /start again.",
                        parse_mode="HTML")
        except Exception:                                  # noqa: BLE001
            pass


def main() -> int:
    try:
        return asyncio.run(run())
    except ConfigError as e:
        print(f"configuration error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
