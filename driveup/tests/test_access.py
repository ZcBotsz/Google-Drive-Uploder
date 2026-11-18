"""Access control (B9): order of checks, fail-open force-sub, auto-lock, locks."""
from __future__ import annotations

import os
import tempfile
import time

import pytest
import pytest_asyncio

from driveup.config import Settings
from driveup.core.access import Access, human_bytes, unlock_at_text
from driveup.db import repo
from driveup.db.sql import Database


@pytest_asyncio.fixture
async def env():
    tmp = tempfile.mkdtemp()
    cfg = Settings()
    cfg.owner_id = 1
    cfg.auto_lock_limit = 5
    cfg.auto_lock_window = 60
    cfg.auto_lock_minutes = 10
    cfg.support_contact = "@help"
    db = Database(os.path.join(tmp, "a.db"))
    await db.connect()
    await repo.upsert_user(db, 10, "U", "u")
    await repo.upsert_user(db, 1, "Owner")
    yield {"db": db, "cfg": cfg, "access": Access(db, cfg)}
    await db.close()


async def test_clean_user_passes(env):
    d = await env["access"].gate(10)
    assert d.ok


async def test_maintenance_blocks_everyone_but_admins(env):
    await repo.set_global(env["db"], {"maintenance": True, "maintenance_msg": "back soon"})
    d = await env["access"].gate(10)
    assert not d.ok and d.reason == "maintenance" and "back soon" in d.screen
    owner = await env["access"].gate(1, count_request=False)
    assert owner.ok


async def test_lock_blocks_with_until_and_support(env):
    await repo.lock_user(env["db"], 10, reason="spam", by_id=1, minutes=30)
    d = await env["access"].gate(10)
    assert not d.ok and d.reason == "locked"
    assert "spam" in d.screen and "@help" in d.screen


async def test_lock_expires_by_itself(env):
    await repo.lock_user(env["db"], 10, reason="x", by_id=1, minutes=1)
    await env["db"].x("UPDATE user_locks SET expires_at=? WHERE user_id=10", (time.time() - 5,))
    assert (await env["access"].gate(10)).ok
    assert await repo.get_lock(env["db"], 10) is None


async def test_lock_scope_upload_only_lets_web_through(env):
    await repo.lock_user(env["db"], 10, reason="upload abuse", by_id=1, minutes=30, scope="upload")
    assert not (await env["access"].gate(10, scope="upload")).ok
    assert (await env["access"].gate(10, scope="web")).ok


async def test_admins_cannot_be_locked_out(env):
    await repo.add_admin(env["db"], 2, "admin")
    await repo.lock_user(env["db"], 2, reason="x", by_id=1, minutes=30)
    assert (await env["access"].gate(2)).ok        # admin bypasses the lock


async def test_force_sub_fails_open_when_telegram_errors(env):
    await repo.set_global(env["db"], {"force_sub_channels": "@chan1,@chan2"})

    class BoomBot:
        async def get_chat_member(self, *a, **k):
            raise RuntimeError("telegram is down")

    access = Access(env["db"], env["cfg"], bot=BoomBot())
    assert await access.force_sub_missing(10) == []
    assert (await access.gate(10)).ok


async def test_force_sub_detects_left_and_shows_links(env):
    await repo.set_global(env["db"], {"force_sub_channels": "@chan1"})

    class LeftBot:
        async def get_chat_member(self, *a, **k):
            return type("M", (), {"status": "left"})()

    access = Access(env["db"], env["cfg"], bot=LeftBot())
    d = await access.gate(10)
    assert not d.ok and d.reason == "force_sub" and "@chan1" in d.screen


async def test_force_sub_joined_user_passes(env):
    await repo.set_global(env["db"], {"force_sub_channels": "@chan1"})

    class OkBot:
        async def get_chat_member(self, *a, **k):
            return type("M", (), {"status": "member"})()

    access = Access(env["db"], env["cfg"], bot=OkBot())
    assert (await access.gate(10)).ok


async def test_auto_lock_after_too_many_requests(env):
    reasons = []
    for _ in range(6):
        d = await env["access"].gate(10)
        reasons.append(d.reason)
    assert reasons[-1] == "locked"
    assert await repo.get_lock(env["db"], 10) is not None


async def test_daily_quota_blocks_and_reports(env):
    await repo.upsert_plan(env["db"], "free", name="Free", price_text="", duration_days=0,
                           max_file_mb=10, daily_gb=0.001, concurrent=1, max_files_job=5,
                           features=[])
    await repo.add_quota(env["db"], 10, int(0.002 * 1024 ** 3))
    d = await env["access"].gate(10)
    assert not d.ok and d.reason == "quota"


async def test_file_over_plan_limit_is_refused(env):
    await repo.upsert_plan(env["db"], "free", name="Free", price_text="", duration_days=0,
                           max_file_mb=5, daily_gb=100, concurrent=1, max_files_job=5,
                           features=[])
    d = await env["access"].gate(10, file_bytes=10 * 1024 ** 2)
    assert not d.ok and d.reason == "plan" and "5" in d.screen


async def test_feature_flags_can_be_switched_off_globally(env):
    await repo.set_global(env["db"], {"features": {"web_studio": False}})
    assert not await env["access"].feature_enabled(10, "web_studio")
    assert await env["access"].feature_enabled(10, "bulk_jobs")
    d = await env["access"].gate(10, feature="web_studio")
    assert not d.ok and d.reason == "feature"


async def test_plan_features_restrict_free_users(env):
    await repo.upsert_plan(env["db"], "free", name="Free", price_text="", duration_days=0,
                           max_file_mb=2000, daily_gb=100, concurrent=1, max_files_job=50,
                           features=["web_studio"])
    assert await env["access"].feature_enabled(10, "web_studio")
    assert not await env["access"].feature_enabled(10, "presets")


async def test_need_connect_reports_both_states(env):
    d = await env["access"].gate(10, need_connect=True)
    assert not d.ok and d.reason == "not_connected"
    await repo.save_drive_account(env["db"], 10, email="a@b.c", enc_refresh="x",
                                  access="y", expiry=9e12, scope="s", scope_mode="file")
    assert (await env["access"].gate(10, need_connect=True)).ok
    await repo.mark_needs_reconnect(env["db"], 10, True)
    d = await env["access"].gate(10, need_connect=True)
    assert not d.ok and d.reason == "needs_reconnect"


def test_formatting_helpers():
    assert human_bytes(0) == "0 B"
    assert human_bytes(2048) == "2.00 KB"
    assert human_bytes(-1) == "unlimited"
    assert unlock_at_text(0) and isinstance(unlock_at_text(0), str)
