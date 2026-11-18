#!/usr/bin/env python3
"""Local preview server for the DriveUp Studio + admin panel.

NOT part of the product: it wires the app to a fake Google Drive and a fake
Telegram Bot API so the browser can be exercised without real credentials.

    python3 scripts/dev_server.py            # http://127.0.0.1:8099
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn                                    # noqa: E402
from aiogram import Bot                           # noqa: E402

from driveup.config import Settings               # noqa: E402
from driveup.core.access import Access            # noqa: E402
from driveup.core.naming import NamingOptions     # noqa: E402
from driveup.core.progress import ProgressHub     # noqa: E402
from driveup.core.worker import Worker            # noqa: E402
from driveup.db import repo                       # noqa: E402
from driveup.db.sql import Database               # noqa: E402
from driveup.drive.accounts import AccountManager  # noqa: E402
from driveup.drive.auth import TokenCipher        # noqa: E402
from driveup.tests.fake_drive import FakeDrive    # noqa: E402
from driveup.tests.fake_tg import FakeSession     # noqa: E402
from driveup.web.app import create_app            # noqa: E402

PORT = int(os.environ.get("PORT", "8099"))
TOKEN = "123456:STUDIOTEST"
USER = 555


async def main() -> None:
    tmp = tempfile.mkdtemp(prefix="driveup-dev-")
    fd = FakeDrive()
    base = await fd.start()

    cfg = Settings()
    cfg.bot_token = TOKEN
    cfg.owner_id = 1
    cfg.google_client_id = "dev-client"
    cfg.google_client_secret = "dev-secret"
    cfg.token_enc_key = "dev-key-please-change-me-0123456789ab"
    cfg.session_secret = "dev-session-secret-0123456789abcdef"
    cfg.public_url = f"http://127.0.0.1:{PORT}"
    cfg.google_api_base = base
    cfg.db_path = f"{tmp}/s.db"
    cfg.temp_dir = f"{tmp}/tmp"
    cfg.download_dir = f"{tmp}/dl"
    cfg.upload_chunk_mb = 1
    cfg.e2e_fixtures = True          # enables POST /__fixture/job for the suites
    os.makedirs(cfg.temp_dir, exist_ok=True)
    os.makedirs(cfg.download_dir, exist_ok=True)

    db = Database(cfg.db_path)
    await db.connect()
    await repo.seed_plans(db, cfg)

    bot = Bot(token=TOKEN, session=FakeSession())
    accounts = AccountManager(db, cfg)
    hub = ProgressHub()
    access = Access(db, cfg, bot=bot)
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)

    # a normal user with a connected (fake) Drive
    for uid, name, uname in ((USER, "Studio", "studio"), (1, "Owner", "owner")):
        await repo.upsert_user(db, uid, name, uname)
        await repo.save_drive_account(
            db, uid, email=f"{uname}@example.com",
            enc_refresh=TokenCipher(cfg.fernet_key()).enc("1//refresh"),
            access="ya29.dev", expiry=9e12, scope="drive.file", scope_mode="file")

    # two folders so the destination tree has something to expand
    courses = fd.add_folder("Courses")
    fd.add_folder("Module 1", courses)

    app = create_app(db=db, cfg=cfg, bot=bot, worker=worker, accounts=accounts,
                     access=access, hub=hub, version="dev")

    # one draft job, 3 files, so the wizard opens with content
    opt = NamingOptions(template="{original}.{ext}")
    job = await repo.create_job(db, USER,
                                destination={"folder_id": "root", "path": "My Drive"},
                                naming=opt.to_dict(), sequence_mode="manual")
    for i, name in enumerate(("lecture 10.mp4", "lecture 2.mp4", "lecture 1.mp4")):
        data = (b"DRIVEUP-DEV-" + name.encode()) * 8_000
        fid = f"dev{job[:6]}{i}"
        bot.session.add_file(name, data, file_id=fid)
        await repo.add_item(db, job, file_id=fid, file_unique_id=f"u{fid}",
                            msg_id=100 + i, size=len(data), mime="video/mp4",
                            name=name, chat_id=USER)
    from driveup.bot.handlers.upload import recompute

    class _App:
        def __init__(self, database):
            self.db = database

    await recompute(_App(db), await repo.get_job(db, job),
                    await repo.items_of(db, job), opt)

    await worker.start()
    print(f"READY job={job} courses={courses} port={PORT}", flush=True)

    server = uvicorn.Server(uvicorn.Config(app, host="0.0.0.0", port=PORT,
                                           log_level="warning", lifespan="off"))
    try:
        await server.serve()
    finally:
        await worker.stop()
        await accounts.close()
        await db.close()
        await fd.stop()


if __name__ == "__main__":
    asyncio.run(main())
