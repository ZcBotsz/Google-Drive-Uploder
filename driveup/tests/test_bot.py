"""Bot behaviour, driven through a real aiogram Dispatcher.

Every update below travels the production path: FakeSession → Bot → Dispatcher →
GateMiddleware → handler → repo/worker, and the assertion reads what Telegram
would have received. No handler is called directly, so routing, filters,
middleware and keyboards are all covered.
"""
from __future__ import annotations

import json
import os
import tempfile
import time

import pytest
import pytest_asyncio

from driveup.bot.client import BotApp, build_bot, build_dispatcher
from driveup.config import Settings
from driveup.core.access import Access
from driveup.core.naming import NamingOptions
from driveup.core.progress import ProgressHub
from driveup.core.worker import Worker
from driveup.db import repo
from driveup.db.sql import Database
from driveup.drive.accounts import AccountManager
from driveup.drive.auth import TokenCipher
from driveup.strings import t
from driveup.tests import fake_tg
from driveup.tests.fake_drive import FakeDrive
from driveup.tg.ratelimit import FloodGate

MB = 1024 * 1024
ADMIN = 1
USER = 42


@pytest_asyncio.fixture
async def botenv():
    tmp = tempfile.mkdtemp()
    fd = FakeDrive()
    base = await fd.start()
    cfg = Settings()
    cfg.bot_token = "123456:TESTTOKEN"
    cfg.owner_id = ADMIN
    cfg.google_client_id = "cid"
    cfg.google_client_secret = "csec"
    cfg.token_enc_key = "k" * 40
    cfg.session_secret = "s"
    cfg.db_path = os.path.join(tmp, "b.db")
    cfg.download_dir = os.path.join(tmp, "dl")
    cfg.temp_dir = os.path.join(tmp, "tmp")
    cfg.google_api_base = base
    cfg.upload_chunk_mb = 1
    cfg.public_url = "http://testserver"
    cfg.timezone = "Asia/Kolkata"
    db = Database(cfg.db_path)
    await db.connect()
    await repo.seed_plans(db, cfg)
    bot = build_bot(cfg)
    bot.session = fake_tg.FakeSession()          # the whole Bot API, in-process
    accounts = AccountManager(db, cfg)
    hub = ProgressHub()
    access = Access(db, cfg, bot=bot)
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)
    app = BotApp(bot=bot, dp=None, db=db, cfg=cfg, access=access, accounts=accounts,
                 worker=worker, hub=hub, fg=FloodGate())
    dp = build_dispatcher(app)
    app.dp = dp
    yield {"app": app, "dp": dp, "db": db, "cfg": cfg, "bot": bot, "sess": bot.session,
           "fd": fd, "worker": worker, "accounts": accounts, "tmp": tmp, "base": base}
    await worker.stop()
    await accounts.close()
    await db.close()
    await fd.stop()


async def feed(env, update) -> None:
    await env["dp"].feed_update(env["bot"], update)


def said(env) -> str:
    return "\n".join(env["sess"].texts())


async def connect_drive(env, uid: int = USER) -> None:
    await repo.save_drive_account(
        env["db"], uid, email="me@example.com",
        enc_refresh=TokenCipher(env["cfg"].fernet_key()).enc("1//refresh"),
        access="ya29.fake", expiry=9e12, scope="s", scope_mode="file")


# ── commands ───────────────────────────────────────────────────────────────
async def test_start_welcomes_and_stores_the_user(botenv):
    await feed(botenv, fake_tg.message("/start", uid=USER))
    assert "DriveUp" in said(botenv) or "welcome" in said(botenv).lower()
    assert await repo.get_user(botenv["db"], USER)


async def test_help_lists_every_command(botenv):
    await feed(botenv, fake_tg.message("/help", uid=USER))
    text = said(botenv)
    for cmd in ("/connect", "/queue", "/studio", "/naming", "/sequence", "/mystats"):
        assert cmd in text


async def test_admin_help_hides_nothing_from_admins(botenv):
    await feed(botenv, fake_tg.message("/help", uid=ADMIN))
    assert "/broadcast" in said(botenv) and "/maintenance" in said(botenv)


async def test_plain_help_hides_admin_commands(botenv):
    await feed(botenv, fake_tg.message("/help", uid=USER))
    assert "/broadcast" not in said(botenv)


async def test_connect_produces_an_oauth_url(botenv):
    await feed(botenv, fake_tg.message("/connect", uid=USER))
    text = said(botenv)
    assert "Google" in text
    btn = botenv["sess"].last()["reply_markup"]["inline_keyboard"][0][0]
    assert "/o/oauth2/v2/auth" in btn["url"] or "oauth2" in btn["url"]
    assert "access_type=offline" in btn["url"] and "code_challenge" in btn["url"]
    row = await botenv["db"].one("SELECT COUNT(*) c FROM oauth_states")
    assert row["c"] == 1


async def test_account_reports_not_connected_then_connected(botenv):
    await feed(botenv, fake_tg.message("/account", uid=USER))
    assert "connect" in said(botenv).lower()
    await connect_drive(botenv)
    await feed(botenv, fake_tg.message("/account", uid=USER))
    assert "me@example.com" in said(botenv)


async def test_disconnect_removes_the_token(botenv):
    await connect_drive(botenv)
    await feed(botenv, fake_tg.message("/disconnect", uid=USER))
    assert await repo.get_drive_account(botenv["db"], USER) is None


async def test_folders_lists_favourites(botenv):
    await repo.add_favourite(botenv["db"], USER, "fld1234567890", "Course Videos")
    await feed(botenv, fake_tg.message("/folders", uid=USER))
    assert "Course Videos" in said(botenv) or "folders" in said(botenv).lower()


async def test_setfolder_accepts_a_link(botenv):
    await connect_drive(botenv)
    fid = botenv["fd"].add_folder("Course Videos")
    await feed(botenv, fake_tg.message(
        f"/setfolder https://drive.google.com/drive/folders/{fid}", uid=USER))
    u = await repo.get_user(botenv["db"], USER)
    assert u["settings"]["default_folder"]["folder_id"] == fid
    assert u["settings"]["default_folder"]["path"].endswith("Course Videos")


async def test_naming_show_and_set(botenv):
    await feed(botenv, fake_tg.message("/naming", uid=USER))
    assert "{original}" in said(botenv)
    await feed(botenv, fake_tg.message("/naming {seq:03d} - {original}.{ext}", uid=USER))
    u = await repo.get_user(botenv["db"], USER)
    assert u["settings"]["naming"]["template"] == "{seq:03d} - {original}.{ext}"


async def test_sequence_show_and_set(botenv):
    await feed(botenv, fake_tg.message("/sequence name_natural", uid=USER))
    u = await repo.get_user(botenv["db"], USER)
    assert u["settings"]["sequence_mode"] == "name_natural"
    await feed(botenv, fake_tg.message("/sequence nonsense_mode", uid=USER))
    assert "telegram_order" in said(botenv) or "one of" in said(botenv)


async def test_settings_language_toggle(botenv):
    await feed(botenv, fake_tg.message("/settings", uid=USER))
    assert "Language" in said(botenv) or "language" in said(botenv).lower()
    await feed(botenv, fake_tg.callback("v1:set:lang", uid=USER))
    u = await repo.get_user(botenv["db"], USER)
    assert u["lang"] in ("en", "hinglish")


async def test_plan_and_mystats(botenv):
    await feed(botenv, fake_tg.message("/plan", uid=USER))
    assert "Free" in said(botenv) or "free" in said(botenv).lower()
    await feed(botenv, fake_tg.message("/mystats", uid=USER))
    assert "Jobs" in said(botenv) or "stats" in said(botenv).lower()


async def test_queue_and_history_are_friendly_when_empty(botenv):
    await feed(botenv, fake_tg.message("/queue", uid=USER))
    await feed(botenv, fake_tg.message("/history", uid=USER))
    text = said(botenv).lower()
    assert "no jobs" in text or "no history" in text or "khaali" in text


async def test_cancel_with_nothing_running(botenv):
    await feed(botenv, fake_tg.message("/cancel", uid=USER))
    assert "nothing" in said(botenv).lower() or "nahi" in said(botenv).lower()


async def test_studio_links_to_the_web_app(botenv):
    await feed(botenv, fake_tg.message("/studio", uid=USER))
    buttons = [b for row in botenv["sess"].last()["reply_markup"]["inline_keyboard"] for b in row]
    urls = [b.get("url", "") for b in buttons]
    studio = [u for u in urls if u]
    assert studio, "no URL button on the /studio reply"
    # a browser link must carry a signed token, or the page 401s on open
    assert any(u.startswith("http://testserver/start?t=") for u in studio), urls


async def test_unknown_command_is_ignored_quietly(botenv):
    await feed(botenv, fake_tg.message("/nonsense", uid=USER))
    # no handler matched: nothing crashes, and /help still works afterwards
    await feed(botenv, fake_tg.message("/help", uid=USER))
    assert "/connect" in said(botenv)


# ── media intake ───────────────────────────────────────────────────────────
def make_video_update(env, name: str, size: int, mid: int = 10, uid: int = USER):
    data = b"v" * size
    fid = env["sess"].add_file(name, data)
    payload = {
        "update_id": mid,
        "message": {
            "message_id": mid, "date": int(time.time()),
            "chat": {"id": uid, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Tester"},
            "video": {"file_id": fid, "file_unique_id": "u" + fid,
                      "width": 640, "height": 360, "duration": 30,
                      "file_name": name, "mime_type": "video/mp4",
                      "file_size": size},
        },
    }
    from aiogram.types import Update
    return Update.model_validate(payload)


async def test_a_video_creates_a_job_card(botenv):
    await feed(botenv, make_video_update(botenv, "lecture1.mp4", 5000))
    text = said(botenv)
    assert "job" in text.lower() or "📂" in text or "📦" in text
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    assert job and job["status"] == "draft"
    items = await repo.items_of(botenv["db"], job["id"])
    assert len(items) == 1 and items[0]["tg"]["name"] == "lecture1.mp4"
    # the card carries the six buttons from B1
    kb = botenv["sess"].last()["reply_markup"]["inline_keyboard"]
    labels = " ".join(b["text"] for row in kb for b in row)
    for word in ("folder", "Naming", "Sequence", "Start", "Upload Studio", "Cancel"):
        assert word.lower() in labels.lower()


async def test_the_same_file_twice_is_deduped(botenv):
    upd = make_video_update(botenv, "dup.mp4", 4000, mid=11)
    await feed(botenv, upd)
    await feed(botenv, upd)
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    items = await repo.items_of(botenv["db"], job["id"])
    assert len(items) == 1


async def test_second_file_appends_to_the_same_draft(botenv):
    await feed(botenv, make_video_update(botenv, "a.mp4", 3000, mid=12))
    await feed(botenv, make_video_update(botenv, "b.mp4", 3000, mid=13))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    assert len(await repo.items_of(botenv["db"], job["id"])) == 2


async def test_oversized_file_is_refused_with_the_plan_hint(botenv):
    await repo.upsert_plan(botenv["db"], "free", name="Free", price_text="—",
                           duration_days=0, max_file_mb=1, daily_gb=100, concurrent=1,
                           max_files_job=10, features=[])
    await feed(botenv, make_video_update(botenv, "huge.mp4", 5 * MB, mid=14))
    text = said(botenv).lower()
    assert "too big" in text or "limit" in text or "badi" in text


async def test_plan_cannot_promise_more_than_telegram_allows(botenv):
    """A 2000 MB plan on the cloud API is a lie — getFile refuses > 20 MB.

    The bot must refuse the file at intake with a limit the user can believe,
    instead of accepting the job and dying at 0 % mid-upload.
    """
    cfg = botenv["cfg"]
    cfg.telegram_api_url = "https://api.telegram.org"
    await repo.upsert_plan(botenv["db"], "free", name="Free", price_text="—",
                           duration_days=0, max_file_mb=2000, daily_gb=100,
                           concurrent=1, max_files_job=10, features=[])
    await feed(botenv, make_video_update(botenv, "big.mp4", 30 * MB, mid=16))
    text = said(botenv)
    assert "20 MB" in text, f"limit shown was not the real one: {text[:200]}"
    assert "2000 MB" not in text, "the bot advertised an impossible limit"


async def test_folder_callback_updates_the_destination(botenv):
    await feed(botenv, make_video_update(botenv, "c.mp4", 2000, mid=15))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await feed(botenv, fake_tg.callback(f"v1:setfav:root", uid=USER))
    fresh = await repo.get_job(botenv["db"], job["id"])
    assert fresh["destination"]["folder_id"] == "root"


async def test_naming_callback_sets_a_preset_template(botenv):
    await feed(botenv, make_video_update(botenv, "d.mp4", 2000, mid=16))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await feed(botenv, fake_tg.callback(f"v1:naming:{job['id']}", uid=USER))
    await feed(botenv, fake_tg.callback(f"v1:ntpl1:{job['id']}", uid=USER))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    assert "seq" in json.dumps(job["naming"]).lower()


async def test_sequence_mode_callback(botenv):
    await feed(botenv, make_video_update(botenv, "e.mp4", 2000, mid=17))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await feed(botenv, fake_tg.callback(f"v1:seqmode:size_asc:{job['id']}", uid=USER))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    assert job["sequence_mode"] == "size_asc"


async def test_foreign_callback_is_refused(botenv):
    await feed(botenv, make_video_update(botenv, "f.mp4", 2000, mid=18, uid=USER))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await feed(botenv, fake_tg.callback(f"v1:start:{job['id']}", uid=999))
    assert any("another account" in (c["params"].get("text") or "")
               for c in botenv["sess"].calls if c["method"] == "AnswerCallbackQuery") \
        or "another account" in said(botenv)


async def test_full_start_to_done(botenv):
    await connect_drive(botenv)
    await feed(botenv, make_video_update(botenv, "one.mp4", 40_000, mid=20))
    await feed(botenv, make_video_update(botenv, "two.mp4", 40_000, mid=21))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await repo.update_job(botenv["db"], job["id"],
                          naming=NamingOptions(template="{seq:03d} - {original}.{ext}").to_dict())
    await feed(botenv, fake_tg.callback(f"v1:start:{job['id']}", uid=USER))
    for _ in range(400):
        fresh = await repo.get_job(botenv["db"], job["id"])
        if fresh["status"] in ("done", "failed", "cancelled"):
            break
        await _sleep(0.05)
    fresh = await repo.get_job(botenv["db"], job["id"])
    print("STATUS:", fresh["status"], "| errors:", [i["error"] for i in await repo.items_of(botenv["db"], job["id"])])
    assert fresh["status"] == "done"
    names = [i["final_name"] for i in await repo.items_of(botenv["db"], job["id"])]
    assert names == ["001 - one.mp4", "002 - two.mp4"]
    assert len(botenv["fd"].uploaded) == 2
    assert "drive.google.com" in said(botenv)


async def _sleep(s):
    import asyncio
    await asyncio.sleep(s)


# ── access control through the dispatcher ─────────────────────────────────
async def test_lock_screen_replaces_handlers(botenv):
    await repo.lock_user(botenv["db"], USER, reason="spam", by_id=ADMIN, minutes=30)
    await feed(botenv, fake_tg.message("/start", uid=USER))
    text = said(botenv)
    assert "locked" in text.lower() or "🔒" in text


async def test_maintenance_blocks_start_but_not_the_owner(botenv):
    await repo.set_global(botenv["db"], {"maintenance": True, "maintenance_msg": "back at 6"})
    await feed(botenv, fake_tg.message("/start", uid=USER))
    assert "maintenance" in said(botenv).lower() or "🛠" in said(botenv)
    before = len(botenv["sess"].calls)
    await feed(botenv, fake_tg.message("/start", uid=ADMIN))
    assert len(botenv["sess"].calls) > before


async def test_force_sub_screen_and_i_joined(botenv):
    await repo.set_global(botenv["db"], {"force_sub_channels": "@mychannel"})

    async def left(*a, **k):
        return type("M", (), {"status": "left"})()
    botenv["sess"].member_status = "left"
    await feed(botenv, fake_tg.message("/start", uid=USER))
    assert "@mychannel" in said(botenv)
    # now the member joins and taps the button
    botenv["sess"].member_status = "member"
    await feed(botenv, fake_tg.callback("v1:subok", uid=USER))
    answers = [str(c["params"].get("text", "")) for c in botenv["sess"].calls
               if c["method"] == "AnswerCallbackQuery"]
    assert any("✅" in a for a in answers), answers
    assert "DriveUp" in said(botenv)          # the welcome follows the join


async def test_feature_gate_blocks_a_disabled_feature(botenv):
    await repo.set_global(botenv["db"], {"features": {"web_studio": False}})
    await feed(botenv, fake_tg.message("/studio", uid=USER))
    text = said(botenv).lower()
    assert "premium" in text or "🔒" in text or "feature" in text


async def test_a_user_cannot_see_another_job_through_callbacks(botenv):
    await feed(botenv, make_video_update(botenv, "g.mp4", 1000, mid=30, uid=USER))
    job = await botenv["db"].one("SELECT * FROM jobs WHERE user_id=?", (USER,))
    await feed(botenv, fake_tg.callback(f"v1:drop:{job['id']}", uid=777))
    fresh = await repo.get_job(botenv["db"], job["id"])
    assert fresh["status"] == "draft"          # untouched


# ── admin commands ────────────────────────────────────────────────────────
async def test_non_admin_cannot_use_admin_commands(botenv):
    await feed(botenv, fake_tg.message("/stats", uid=USER))
    text = said(botenv).lower()
    assert "admin" in text or "🔒" in text


async def test_admin_stats_and_users(botenv):
    await feed(botenv, fake_tg.message("/stats", uid=ADMIN))
    assert "users" in said(botenv).lower() or "👥" in said(botenv)
    await feed(botenv, fake_tg.message("/users", uid=ADMIN))
    assert "42" in said(botenv) or "user" in said(botenv).lower()


async def test_admin_lock_and_unlock_a_user(botenv):
    await repo.upsert_user(botenv["db"], 555, "Bad Actor", "bad")
    await feed(botenv, fake_tg.message("/lock 555 30 spam", uid=ADMIN))
    assert await repo.get_lock(botenv["db"], 555)
    await feed(botenv, fake_tg.message("/unlock 555", uid=ADMIN))
    assert await repo.get_lock(botenv["db"], 555) is None
    audit = await botenv["db"].all("SELECT action FROM audit ORDER BY id DESC LIMIT 2")
    assert {"user.lock", "user.unlock"} <= {a["action"] for a in audit}


async def test_admin_maintenance_toggle(botenv):
    await feed(botenv, fake_tg.message("/maintenance on Back at 6pm", uid=ADMIN))
    g = await repo.get_global(botenv["db"])
    assert g["maintenance"] is True and "6pm" in g["maintenance_msg"]
    await feed(botenv, fake_tg.message("/maintenance off", uid=ADMIN))
    assert (await repo.get_global(botenv["db"]))["maintenance"] is False


async def test_admin_broadcast_needs_confirmation(botenv):
    await feed(botenv, fake_tg.message("/broadcast Hello everyone", uid=ADMIN))
    kb = botenv["sess"].last().get("reply_markup") or {}
    assert "send to" in said(botenv).lower()
    flat = [b for row in kb.get("inline_keyboard", []) for b in row]
    assert any("confirm" in b["text"].lower() for b in flat)


async def test_admin_system_and_audit_and_export(botenv):
    await feed(botenv, fake_tg.message("/system", uid=ADMIN))
    text = said(botenv).lower()
    assert "uptime" in text or "cpu" in text or "python" in text
    await feed(botenv, fake_tg.message("/audit", uid=ADMIN))
    assert "audit" in said(botenv).lower() or "no entries" in said(botenv).lower()
    await feed(botenv, fake_tg.message("/export", uid=ADMIN))
    assert any(c["method"] == "SendDocument" for c in botenv["sess"].calls)


async def test_admin_addplan_removeplan(botenv):
    await repo.upsert_user(botenv["db"], 700, "Buyer")
    await feed(botenv, fake_tg.message("/addplan 700 30 gold", uid=ADMIN))
    plan = await repo.effective_plan(botenv["db"], 700)
    assert plan["id"] == "gold" or plan["id"] == "free"   # unknown id falls back
    await feed(botenv, fake_tg.message("/addplan 700 30 pro", uid=ADMIN))
    assert (await repo.effective_plan(botenv["db"], 700))["id"] == "pro"
    await feed(botenv, fake_tg.message("/removeplan 700", uid=ADMIN))
    assert (await repo.effective_plan(botenv["db"], 700))["id"] == "free"


async def test_admin_pause_all_resume_all(botenv):
    await feed(botenv, fake_tg.message("/pause_all", uid=ADMIN))
    assert (await repo.get_global(botenv["db"])).get("uploads_paused") is True
    await feed(botenv, fake_tg.message("/resume_all", uid=ADMIN))
    assert (await repo.get_global(botenv["db"])).get("uploads_paused") is False


async def test_lock_notification_goes_to_the_lock_channel(botenv):
    await botenv["cfg"].__dict__.update({}) if False else None
    botenv["cfg"].lock_channel_id = -1001234567890
    await repo.upsert_user(botenv["db"], 556, "Rude", "rude")
    await feed(botenv, fake_tg.message("/lock 556 10 abuse", uid=ADMIN))
    assert any(c["params"].get("chat_id") == -1001234567890
               for c in botenv["sess"].calls if c["method"] == "SendMessage")


# ── Telegram 409 Conflict (two pollers on one token) must be detectable ────
async def test_conflict_session_flags_a_409():
    """Telegram's 'terminated by other getUpdates request' must be noticed.

    Two processes polling one token make Telegram reject BOTH; aiogram retries
    silently, so without this the bot looks online while dropping every
    message. `ConflictAwareSession` records the 409 for main.py's watchdog.
    """
    from aiohttp import web
    from aiogram import Bot
    from aiogram.client.default import DefaultBotProperties
    from aiogram.client.telegram import TelegramAPIServer
    from driveup.bot.client import ConflictAwareSession

    async def get_updates(request):
        return web.json_response(
            {"ok": False, "error_code": 409,
             "description": "Conflict: terminated by other getUpdates request"},
            status=409)

    app = web.Application()
    app.router.add_post("/bot{token}/{method}", get_updates)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 8499)
    await site.start()
    try:
        session = ConflictAwareSession(
            api=TelegramAPIServer.from_base("http://127.0.0.1:8499"))
        bot = Bot(token="123456:T", session=session,
                  default=DefaultBotProperties(parse_mode="HTML"))
        assert session.conflict_seen is False
        with pytest.raises(Exception):
            await bot.get_updates(timeout=0)
        assert session.conflict_seen is True
        await bot.session.close()
    finally:
        await runner.cleanup()


# ══════════════════════════════════════════════════════════════════════════
# Every Studio/Admin button must carry a signed, openable link.
# ══════════════════════════════════════════════════════════════════════════
from driveup.core.links import parse_token                     # noqa: E402


def _urls(env) -> list:
    kb = env["sess"].last().get("reply_markup") or {}
    return [b.get("url", "") for row in kb.get("inline_keyboard", []) for b in row if b.get("url")]


async def test_admin_command_hands_out_a_working_panel_link(botenv):
    """`/admin` used to print a bare PUBLIC_URL/admin that 401'd in a browser."""
    await feed(botenv, fake_tg.message("/admin", uid=ADMIN))
    urls = _urls(botenv)
    panel = [u for u in urls if "/start?" in u and "t=" in u]
    assert panel, f"no signed admin link in {urls}"
    tok = panel[0].split("t=")[1].split("&")[0]
    claim = parse_token(botenv["cfg"].session_secret, tok)
    assert claim and claim["admin"] is True
    assert claim["user_id"] == ADMIN


async def test_studio_link_is_signed_for_the_asking_user(botenv):
    await feed(botenv, fake_tg.message("/studio", uid=USER))
    urls = _urls(botenv)
    assert urls, "no URL button"
    tok = [u for u in urls if "t=" in u][0].split("t=")[1].split("&")[0]
    claim = parse_token(botenv["cfg"].session_secret, tok)
    assert claim and claim["user_id"] == USER and claim["admin"] is False


async def test_non_admin_gets_no_panel_link(botenv):
    """The link must never even be minted for a normal user."""
    await feed(botenv, fake_tg.message("/admin", uid=USER))
    assert not [u for u in _urls(botenv) if "t=" in u and "/start?" in u]


# ══════════════════════════════════════════════════════════════════════════
# Uploader preflight: `drive.file` cannot write to a folder it did not create.
# The old code swallowed that 403 and started the job anyway — "uploader not
# working" with no explanation. These prove the explanation now appears.
# ══════════════════════════════════════════════════════════════════════════
from driveup.drive.errors import PermissionDenied                 # noqa: E402
from driveup.core.preflight import destination_problem            # noqa: E402


class _DeniedClient:
    async def get(self, fid):        # what Google answers under drive.file
        raise PermissionDenied("No permission for that folder", status=403)


class _OkClient:
    async def get(self, fid):
        return {"id": fid, "name": "Somewhere"}


class _Accounts:
    def __init__(self, client):
        self._c = client

    async def client(self, uid):
        return self._c


class _App:
    def __init__(self, client, full=False):
        self.accounts = _Accounts(client)
        self.cfg = type("C", (), {"scope_mode_full": full})()


async def test_preflight_flags_a_drive_file_folder_with_the_fix():
    app = _App(_DeniedClient(), full=False)
    msg = await destination_problem(app, 42, {"destination": {"folder_id": "abc123",
                                                              "path": "My Drive › Movies"}})
    assert "cannot write" in msg.lower()
    assert "DRIVE_SCOPE_MODE=full" in msg        # the actual cure, not a generic error
    assert "My Drive › Movies" in msg


async def test_preflight_flags_a_permission_problem_in_full_mode_too():
    app = _App(_DeniedClient(), full=True)
    msg = await destination_problem(app, 42, {"destination": {"folder_id": "abc123"}})
    assert "no permission" in msg.lower()


async def test_preflight_passes_a_writable_folder():
    app = _App(_OkClient())
    assert await destination_problem(app, 42, {"destination": {"folder_id": "ok1"}}) == ""


async def test_preflight_skips_root_because_it_always_works():
    app = _App(_DeniedClient())
    assert await destination_problem(app, 42, {"destination": {"folder_id": "root"}}) == ""


# ── a full Drive must be explained, not spam-failed (production bug) ────────

async def _job_with_file(db, size: int) -> dict:
    """A genuine job + item, so preflight compares a real `need`."""
    jid = await repo.create_job(db, 42, destination={"folder_id": "root"},
                                naming={"template": "{original}.{ext}"})
    await repo.add_item(db, jid, file_id="f1", file_unique_id="u1", msg_id=1,
                        size=size, mime="application/octet-stream", name="a.bin")
    return await repo.get_job(db, jid)


async def test_full_drive_blocks_the_job_before_it_starts(botenv):
    """storageQuotaExceeded used to fail every file one by one.

    The preflight now asks Drive for free space once, so a job that cannot fit
    is refused with a sentence instead of a wall of identical failures.
    """
    from driveup.core.preflight import storage_problem
    job = await _job_with_file(botenv["db"], 5000)

    class _Q:
        async def about(self):
            return {"limit": 1000, "usage": 1000, "free": 0, "email": "a@b.c"}

    class _Acc:
        async def client(self, uid):
            return _Q()

    class _App:
        accounts = _Acc()
        db = botenv["db"]

    msg = await storage_problem(_App(), 42, job)
    assert "full" in msg.lower()
    assert "/storage" in msg


async def test_job_fits_so_preflight_stays_quiet(botenv):
    from driveup.core.preflight import storage_problem
    job = await _job_with_file(botenv["db"], 5000)

    class _Q:
        async def about(self):
            return {"limit": 10**12, "usage": 0, "free": 10**12, "email": "a@b.c"}

    class _Acc:
        async def client(self, uid):
            return _Q()

    class _App:
        accounts = _Acc()
        db = botenv["db"]

    assert await storage_problem(_App(), 42, job) == ""


async def test_storage_command_reports_space(botenv):
    from driveup.core.preflight import quota_line

    class _Q:
        async def about(self):
            return {"limit": 100, "usage": 40, "free": 60, "email": "me@x.y"}

    class _Acc:
        async def client(self, uid):
            return _Q()

    class _App:
        accounts = _Acc()

    line = await quota_line(_App(), 42)
    assert "me@x.y" in line and "40%" in line and "free" in line
