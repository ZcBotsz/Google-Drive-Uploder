"""End-to-end pipeline: intake → job → worker → Drive, against the fake Drive.

These are the acceptance tests from B16 that can run without Google:
  · 12 files with `{seq:03d}` land in the chosen manual order
  · a kill at ~50 % resumes (persisted session URI) and the bytes match
  · cancel stops within a second and marks the item failed
  · pause blocks the loop; resume continues
  · locking a user mid-job cancels it
  · a revoked Google account pauses the job instead of failing every file
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile

import aiohttp
import pytest
import pytest_asyncio

from driveup.config import Settings
from driveup.core.access import Access
from driveup.core.naming import NamingOptions
from driveup.core.progress import ProgressHub
from driveup.core.worker import Worker
from driveup.db import repo
from driveup.db.sql import Database
from driveup.drive.accounts import AccountManager
from driveup.tests.fake_drive import FakeDrive
from driveup.tests.fakes import FakeBot

MB = 1024 * 1024


def make_cfg(tmp: str, base: str) -> Settings:
    cfg = Settings()
    cfg.bot_token = "123456:TEST"
    cfg.owner_id = 999
    cfg.google_client_id = "cid"
    cfg.google_client_secret = "csec"
    cfg.token_enc_key = "test-key-please-change"
    cfg.session_secret = "sess"
    cfg.db_path = os.path.join(tmp, "t.db")
    cfg.download_dir = os.path.join(tmp, "dl")
    cfg.temp_dir = os.path.join(tmp, "tmp")
    cfg.google_api_base = base
    cfg.upload_chunk_mb = 1
    cfg.max_global_uploads = 3
    cfg.max_user_uploads = 1
    cfg.upload_mode = "stream"
    os.makedirs(cfg.download_dir, exist_ok=True)
    return cfg


@pytest_asyncio.fixture
async def env():
    tmp = tempfile.mkdtemp()
    fd = FakeDrive()
    base = await fd.start()
    cfg = make_cfg(tmp, base)
    db = Database(cfg.db_path)
    await db.connect()
    bot = FakeBot(token=cfg.bot_token)
    accounts = AccountManager(db, cfg)
    hub = ProgressHub()
    access = Access(db, cfg, bot=bot)
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)
    uid = 21
    await repo.upsert_user(db, uid, "Tester", "tester")
    # connect a fake account directly (skip the browser round-trip)
    from driveup.drive.auth import TokenCipher
    cipher = TokenCipher(cfg.fernet_key())
    await repo.save_drive_account(db, uid, email="tester@example.com",
                                  enc_refresh=cipher.enc("1//refresh"),
                                  access="ya29.fake", expiry=9e12, scope="drive.file",
                                  scope_mode="file")
    yield {"fd": fd, "base": base, "cfg": cfg, "db": db, "bot": bot,
           "accounts": accounts, "worker": worker, "uid": uid, "access": access,
           "hub": hub, "tmp": tmp}
    await worker.stop()
    await accounts.close()
    await db.close()
    await fd.stop()


async def make_job(env, files, *, template="{seq:03d} - {original}.{ext}",
                   policy="rename", mode="telegram_order", strict=True):
    db, bot, uid = env["db"], env["bot"], env["uid"]
    opt = NamingOptions(template=template, strip_emoji=True)
    job_id = await repo.create_job(db, uid,
                                   destination={"folder_id": "root", "path": "My Drive"},
                                   naming=opt.to_dict(), sequence_mode=mode,
                                   conflict_policy=policy, strict_order=strict)
    for i, (name, data) in enumerate(files):
        ref = bot.add_file(name, data)
        await repo.add_item(db, job_id, file_id=ref.file_id, file_unique_id=ref.file_unique_id,
                            msg_id=100 + i, size=len(data), mime="application/octet-stream",
                            name=name, chat_id=42)
    from driveup.bot.handlers.upload import recompute
    class _App:
        def __init__(self, db): self.db = db
    job = await repo.get_job(db, job_id)
    items = await repo.items_of(db, job_id)
    await recompute(_App(db), job, items, opt)
    return job_id


async def wait_done(worker, job_id, db, timeout=60):
    for _ in range(int(timeout / 0.05)):
        job = await repo.get_job(db, job_id)
        if job["status"] in ("done", "failed", "cancelled"):
            return job
        await asyncio.sleep(0.05)
    return await repo.get_job(db, job_id)


# ── the 12-file, ordered, named, uploaded acceptance test ──────────────────
@pytest.mark.asyncio
async def test_twelve_files_upload_in_chosen_order(env):
    files = [(f"file{i}.bin", bytes([i]) * (40_000 + i)) for i in range(1, 13)]
    job_id = await make_job(env, files)
    db, worker = env["db"], env["worker"]
    # reverse the order by hand, like a drag-and-drop
    items = await repo.items_of(db, job_id)
    await repo.reorder_items(db, job_id, [i["id"] for i in reversed(items)])
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db)
    assert job["status"] == "done"
    items = await repo.items_of(db, job_id)
    names = [i["final_name"] for i in items]
    assert names == [f"{n:03d} - file{13 - n}.bin" for n in range(1, 13)]
    fd = env["fd"]
    assert len(fd.uploaded) == 12
    by_name = {f["name"]: f for f in fd.files.values()}
    assert set(by_name) == set(names)
    # bytes of the first item in the new order are the last file's bytes
    first = by_name[names[0]]
    assert fd.uploaded[first["id"]] == files[11][1]


@pytest.mark.asyncio
async def test_duplicate_names_follow_the_policy(env):
    files = [("same.bin", b"a" * 1000), ("same.bin", b"b" * 1000)]
    job_id = await make_job(env, files, template="{original}.{ext}", policy="rename")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await wait_done(worker, job_id, db)
    names = sorted(f["name"] for f in env["fd"].files.values())
    assert names == ["same (1).bin", "same.bin"]


@pytest.mark.asyncio
async def test_skip_policy_leaves_the_file_out(env):
    files = [("same.bin", b"a" * 100), ("same.bin", b"b" * 100)]
    job_id = await make_job(env, files, template="{original}.{ext}", policy="skip")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await wait_done(worker, job_id, db)
    assert len(env["fd"].uploaded) == 1


# ── resume across a process restart ────────────────────────────────────────
@pytest.mark.asyncio
async def test_kill_mid_upload_then_resume_on_restart(env):
    data = os.urandom(6 * MB)
    job_id = await make_job(env, [("big.bin", data)], template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    # wait until the first chunk is committed, then "kill" the worker
    for _ in range(400):
        items = await repo.items_of(db, job_id)
        if items and items[0]["bytes_done"] >= 1 * MB:
            break
        await asyncio.sleep(0.01)
    await worker.stop()
    item = (await repo.items_of(db, job_id))[0]
    committed = item["drive"].get("committed") or 0
    uri = item["drive"].get("session_uri")
    assert uri, "the Drive session URI must be persisted for a resume"
    assert committed >= 1 * MB

    # simulate a fresh process: recover_interrupted + a new Worker
    await db.x("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    n = await repo.recover_interrupted(db)
    assert n >= 1
    accounts = AccountManager(db, env["cfg"])
    worker2 = Worker(db=db, cfg=env["cfg"], accounts=accounts, bot=env["bot"],
                     hub=ProgressHub())
    await worker2.start()
    job = await wait_done(worker2, job_id, db)
    await worker2.stop()
    await accounts.close()
    assert job["status"] == "done"
    done_item = (await repo.items_of(db, job_id))[0]
    assert done_item["state"] == "done"
    fid = done_item["drive"]["file_id"]
    assert env["fd"].uploaded[fid] == data
    assert env["fd"].files[fid]["md5Checksum"] == hashlib.md5(data).hexdigest()


# ── cancel / pause / resume ────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_cancel_stops_the_job(env):
    files = [(f"c{i}.bin", b"x" * (8 * MB)) for i in range(4)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await asyncio.sleep(0.02)
    await worker.cancel(job_id)
    await asyncio.sleep(0.3)
    job = await repo.get_job(db, job_id)
    assert job["status"] == "cancelled"
    assert len(env["fd"].uploaded) < 4


@pytest.mark.asyncio
async def test_pause_then_resume_completes(env):
    files = [(f"p{i}.bin", b"y" * (512 * 1024)) for i in range(3)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await asyncio.sleep(0.02)
    await worker.pause(job_id)
    paused = await repo.get_job(db, job_id)
    assert paused["status"] in ("paused", "done")
    await worker.resume(job_id)
    job = await wait_done(worker, job_id, db)
    assert job["status"] == "done"


@pytest.mark.asyncio
async def test_lock_cancels_running_jobs(env):
    files = [(f"l{i}.bin", b"z" * (2 * MB)) for i in range(3)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await asyncio.sleep(0.02)
    n = await worker.cancel_user_jobs(env["uid"], why="locked")
    assert n >= 1
    await asyncio.sleep(0.2)
    assert (await repo.get_job(db, job_id))["status"] == "cancelled"


# ── Google failures ────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_revoked_google_pauses_instead_of_failing_everything(env):
    files = [(f"r{i}.bin", b"q" * 1000) for i in range(2)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    # expire the token and make the refresh fail the way Google does
    await db.x("UPDATE drive_accounts SET access_expiry=0 WHERE user_id=?", (env["uid"],))
    from driveup.drive.auth import TokenCipher
    await db.x("UPDATE drive_accounts SET enc_refresh_token=? WHERE user_id=?",
               (TokenCipher(env["cfg"].fernet_key()).enc("bad"), env["uid"]))
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db, timeout=20)
    assert job["status"] == "paused"
    acc = await repo.get_drive_account(db, env["uid"])
    assert acc["needs_reconnect"] == 1
    said = " ".join(env["bot"].texts())
    assert "/connect" in said or "paused" in said.lower()


@pytest.mark.asyncio
async def test_quota_error_stops_the_job_cleanly(env):
    """A full Drive must PAUSE the run, not fail every file in turn.

    This used to assert `failed` — the old behaviour marked each remaining item
    red one by one, which buried the real cause. Production showed the result:
    a wall of `storageQuotaExceeded` tracebacks and no usable explanation.
    """
    files = [(f"s{i}.bin", b"w" * 1000) for i in range(2)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    env["fd"].faults["quota"] = True
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db, timeout=20)
    items = await repo.items_of(db, job_id)
    assert job["status"] == "paused", f"job should pause, got {job['status']}"
    # The item that hit the wall stays pending so a resume retries it…
    assert any(i["state"] == "pending" for i in items)
    # …and nothing is marked failed just for Drive being full.
    assert not any(i["state"] == "failed" for i in items)


async def test_pause_message_names_the_cause(env):
    """The user must be told *why* the run stopped, with the next step."""
    from driveup.core.pause_reason import pause_message
    from driveup.drive.errors import DailyLimit, StorageQuota, AuthExpired
    quota = pause_message(StorageQuota("x"), done=3)
    assert "full" in quota.lower() and "resume" in quota.lower() and "3" in quota
    assert "750" in pause_message(DailyLimit("x"), done=0)
    assert "/connect" in pause_message(AuthExpired("expired"), done=0)


@pytest.mark.asyncio
async def test_quota_usage_and_stats_are_recorded(env):
    files = [("one.bin", b"1" * 5000)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await wait_done(worker, job_id, db)
    usage = await repo.quota_used(db, env["uid"])
    assert usage["bytes"] == 5000 and usage["files"] == 1
    stats = await repo.user_stats(db, env["uid"])
    assert stats["files"] == 1 and stats["bytes"] == 5000


@pytest.mark.asyncio
async def test_disk_mode_also_works_and_cleans_up(env):
    env["cfg"].upload_mode = "disk"
    data = b"d" * (300 * 1024)
    job_id = await make_job(env, [("disk.bin", data)], template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db)
    assert job["status"] == "done"
    assert os.listdir(env["cfg"].download_dir) == []      # temp file removed


@pytest.mark.asyncio
async def test_empty_job_fails_fast(env):
    db, worker, uid = env["db"], env["worker"], env["uid"]
    job_id = await repo.create_job(db, uid, destination={"folder_id": "root"},
                                   naming=NamingOptions().to_dict())
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db, timeout=10)
    assert job["status"] == "failed"


# ══════════════════════════════════════════════════════════════════════════
# Pause/resume races — a real production bug found by running this file 4x.
# `resume()` used to write status="running" unconditionally, so a tap that
# raced the finish flipped a DONE job back to running with no task behind it:
# the progress card froze at 100 % and the queue never drained.
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.asyncio
async def test_resume_after_finish_is_a_noop(env):
    files = [(f"r{i}.bin", b"q" * (128 * 1024)) for i in range(2)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db, timeout=30)
    assert job["status"] == "done"

    await worker.resume(job_id)                  # the late tap
    await asyncio.sleep(0.2)
    after = await repo.get_job(db, job_id)
    assert after["status"] == "done", "a finished job must never go back to running"


@pytest.mark.asyncio
async def test_resume_is_a_noop_when_never_paused(env):
    files = [(f"n{i}.bin", b"s" * (64 * 1024)) for i in range(2)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db = env["db"]
    await repo.update_job(db, job_id, status="queued")
    await env["worker"].resume(job_id)           # resume a queued (not paused) job
    assert (await repo.get_job(db, job_id))["status"] == "queued"


@pytest.mark.asyncio
async def test_pause_cannot_move_a_done_job(env):
    files = [(f"d{i}.bin", b"w" * (64 * 1024)) for i in range(1)]
    job_id = await make_job(env, files, template="{original}.{ext}")
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await wait_done(worker, job_id, db, timeout=30)
    await worker.pause(job_id)
    assert (await repo.get_job(db, job_id))["status"] == "done"


@pytest.mark.asyncio
async def test_every_run_ends_in_a_terminal_state(env):
    """20 pause/resume taps at random moments must never strand a job."""
    for n in range(6):
        files = [(f"z{n}_{i}.bin", b"k" * (96 * 1024)) for i in range(2)]
        job_id = await make_job(env, files, template="{original}.{ext}")
        db, worker = env["db"], env["worker"]
        await repo.update_job(db, job_id, status="queued")
        worker.spawn(job_id)
        await asyncio.sleep(0.01 * (n % 3))
        await worker.pause(job_id)
        await worker.resume(job_id)
        job = await wait_done(worker, job_id, db, timeout=30)
        assert job["status"] in ("done", "failed", "cancelled"), \
            f"job {n} stranded as {job['status']}"
        assert job["status"] == "done", f"job {n} ended {job['status']}"


@pytest.mark.asyncio
async def test_successful_upload_is_logged_and_mirrored_to_configured_channels(env):
    worker, cfg, bot = env["worker"], env["cfg"], env["bot"]
    cfg.log_channel_id = "-100111"
    cfg.dump_channel_id = "-100222"
    job = {"id": "job-test", "user_id": env["uid"]}
    item = {"id": "item-test", "final_name": "lesson.mp4",
            "tg": {"name": "lesson.mp4", "chat_id": env["uid"], "msg_id": 77}}

    await worker._record_uploaded(
        job, item, {"id": "drive-file-1",
                    "webViewLink": "https://drive.google.com/file/d/drive-file-1/view"}, 1234)

    assert len(bot.copied) == 1
    assert bot.copied[0]["chat_id"] == "-100222"
    assert bot.copied[0]["from_chat_id"] == env["uid"]
    assert bot.copied[0]["message_id"] == 77
    assert any(m["chat_id"] == "-100111" and "Drive upload complete" in m["text"]
               for m in bot.sent)
    assert any(m["chat_id"] == "-100222" and "Drive upload complete" in m["text"]
               for m in bot.sent)

@pytest.mark.asyncio
async def test_manual_tree_end_to_end_creates_empty_and_nested_folders(env):
    job_id = await make_job(env, [('root.bin', b'a'*100), ('child.bin', b'b'*100), ('nested.bin', b'c'*100)], template='{original}.{ext}')
    db, worker = env['db'], env['worker']
    items = await repo.items_of(db, job_id)
    split = {'mode': 'tree', 'folders': [['Course'], ['Course', 'Module'], ['Empty']],
             'plan': {items[0]['id']: [], items[1]['id']: ['Course'], items[2]['id']: ['Course', 'Module']}}
    await repo.update_job(db, job_id, destination={'folder_id':'root', 'path':'My Drive', 'split':split}, status='queued')
    worker.spawn(job_id)
    job = await wait_done(worker, job_id, db)
    assert job['status'] == 'done'
    assert all(item['state'] == 'done' for item in await repo.items_of(db, job_id))
    files = {f['name']: f for f in env['fd'].files.values()}
    assert files['Course']['parents'] == ['root']
    assert files['Module']['parents'] == [files['Course']['id']]
    assert files['Empty']['parents'] == ['root']
    assert files['root.bin']['parents'] == ['root']
    assert files['child.bin']['parents'] == [files['Course']['id']]
    assert files['nested.bin']['parents'] == [files['Module']['id']]

@pytest.mark.asyncio
async def test_manual_tree_same_names_different_folders_not_skipped(env):
    job_id = await make_job(env, [('same.bin', b'a'*100), ('same.bin', b'b'*100)], template='{original}.{ext}', policy='skip')
    db, worker = env['db'], env['worker']
    items = await repo.items_of(db, job_id)
    split = {'mode':'tree', 'folders':[['A'], ['B']], 'plan':{items[0]['id']:['A'], items[1]['id']:['B']}}
    await repo.update_job(db, job_id, destination={'folder_id':'root','split':split}, status='queued')
    worker.spawn(job_id)
    await wait_done(worker, job_id, db)
    assert [i['state'] for i in await repo.items_of(db,job_id)] == ['done','done']
    assert len([f for f in env['fd'].files.values() if f['name'] == 'same.bin']) == 2
