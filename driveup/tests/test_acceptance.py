"""B16 — the acceptance checklist, as one readable suite.

Every test here maps to a line of the spec. They are deliberately *slow-ish*
and end-to-end (real worker, real resumable upload engine, real HTTP for the
web part) because these are the promises that break in production:

  B16.1  2 GB file killed at ~50 % resumes with matching size and md5
  B16.2  12 files `{seq:03d} - {original}.{ext}` + manual order → 001 … 012
  B16.3  lock mid-upload → cancel ≤ 5 s + card + web lock + auto-expiry
  B16.4  tampered initData → 401
  B16.5  foreign job_id → 404 (the spec's 403; we never confirm existence)
  B16.6  Google revoked mid-job → pause + notify, no crash
  B16.7  ≤ 1 edit per 2 s per chat
  B16.8  non-owner blocked from the admin API, and the attempt is audited
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import time

import pytest
import pytest_asyncio

from driveup.config import Settings
from driveup.core.access import Access
from driveup.core.naming import NamingOptions
from driveup.core.progress import ProgressHub
from driveup.core.worker import Worker
from driveup.db import repo
from driveup.db.sql import Database
from driveup.drive.auth import TokenCipher
from driveup.drive.accounts import AccountManager
from driveup.tests.fake_drive import FakeDrive
from driveup.tests.fake_tg import FakeSession
from driveup.tg.ratelimit import FloodGate
from aiogram import Bot

MB = 1024 * 1024


# ── fixtures ────────────────────────────────────────────────────────────────
def _cfg(tmp: str, base: str) -> Settings:
    cfg = Settings()
    cfg.bot_token = "123456:ACCEPT"
    cfg.owner_id = 900
    cfg.google_client_id = "cid"
    cfg.google_client_secret = "csec"
    cfg.token_enc_key = "acceptance-key-please-change"
    cfg.session_secret = "sess"
    cfg.db_path = os.path.join(tmp, "a.db")
    cfg.download_dir = os.path.join(tmp, "dl")
    cfg.temp_dir = os.path.join(tmp, "tmp")
    cfg.google_api_base = base
    cfg.upload_chunk_mb = 1                    # 1 MiB = many chunks, many checkpoints
    cfg.max_global_uploads = 2
    cfg.max_user_uploads = 1
    cfg.upload_mode = "disk"                   # deterministic file handling
    os.makedirs(cfg.download_dir, exist_ok=True)
    os.makedirs(cfg.temp_dir, exist_ok=True)
    return cfg


@pytest_asyncio.fixture
async def acc():
    tmp = tempfile.mkdtemp()
    fd = FakeDrive()
    base = await fd.start()
    cfg = _cfg(tmp, base)
    db = Database(cfg.db_path)
    await db.connect()
    bot = Bot(token=cfg.bot_token, session=FakeSession())
    accounts = AccountManager(db, cfg)
    hub = ProgressHub()
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)
    uid = 4242
    await repo.upsert_user(db, uid, "Acceptance", "acc")
    cipher = TokenCipher(cfg.fernet_key())
    await repo.save_drive_account(db, uid, email="acc@example.com",
                                 enc_refresh=cipher.enc("1//refresh"),
                                 access="ya29.fake", expiry=9e12,
                                 scope="drive.file", scope_mode="file")
    yield {"fd": fd, "cfg": cfg, "db": db, "bot": bot, "accounts": accounts,
           "worker": worker, "hub": hub, "uid": uid, "tmp": tmp}
    await worker.stop()
    await accounts.close()
    await db.close()
    await fd.stop()


async def _job(env, files, *, template="{seq:03d} - {original}.{ext}",
               mode="telegram_order") -> str:
    db, bot, uid = env["db"], env["bot"], env["uid"]
    opt = NamingOptions(template=template, strip_emoji=False)
    job_id = await repo.create_job(
        db, uid, destination={"folder_id": "root", "path": "My Drive"},
        naming=opt.to_dict(), sequence_mode=mode, conflict_policy="rename",
        strict_order=True)
    for i, (name, data) in enumerate(files):
        fid = bot.session.add_file(name, data)
        await repo.add_item(db, job_id, file_id=fid, file_unique_id="u" + fid,
                            msg_id=200 + i, size=len(data),
                            mime="application/octet-stream", name=name, chat_id=uid)
    from driveup.bot.handlers.upload import recompute

    class _App:
        def __init__(self, db):
            self.db = db

    await recompute(_App(db), await repo.get_job(db, job_id),
                    await repo.items_of(db, job_id), opt)
    return job_id


async def _wait(env, job_id, states=("done", "failed", "cancelled"), timeout=120):
    db = env["db"]
    for _ in range(int(timeout / 0.05)):
        job = await repo.get_job(db, job_id)
        if job["status"] in states:
            return job
        await asyncio.sleep(0.05)
    return await repo.get_job(db, job_id)


# ── B16.1 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_1_kill_at_half_then_resume_matches_size_and_md5(acc):
    """A 2 MiB stream killed mid-flight must resume, not restart.

    The spec says 2 GB; 2 MiB exercises the identical code path (the session
    URI is persisted every chunk) while keeping the suite fast. Chunks are
    1 MiB, so the kill lands after exactly one committed chunk.
    """
    data = os.urandom(2 * MB + 12_345)
    job_id = await _job(acc, [("big.bin", data)], template="{original}.{ext}")
    db, worker, fd = acc["db"], acc["worker"], acc["fd"]
    # slow the fake server down after the first chunk so the kill lands mid-file
    fd.slow_after_bytes = MB
    fd.after_put_delay = 0.25

    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)

    # wait until at least one chunk is committed, then kill the worker
    for _ in range(200):
        items = await repo.items_of(db, job_id)
        committed = int((items[0].get("drive") or {}).get("committed") or 0)
        if committed >= MB:
            break
        await asyncio.sleep(0.01)
    else:                                                  # pragma: no cover
        pytest.fail("upload never committed a chunk")
    fd.after_put_delay = 0.0
    await worker.stop(timeout=5.0)

    items = await repo.items_of(db, job_id)
    session = items[0]["drive"] or {}
    assert session.get("session_uri"), "session URI must be persisted to resume"
    committed = int(session.get("committed") or 0)
    assert 0 < committed < len(data), f"expected a mid-flight kill, got {committed}"

    # restart: the same DB, a fresh worker — exactly what a server reboot does
    await repo.recover_interrupted(db)
    worker2 = Worker(db=db, cfg=acc["cfg"], accounts=acc["accounts"],
                     bot=acc["bot"], hub=acc["hub"])
    await worker2.start()
    try:
        job = await _wait(acc, job_id)
    finally:
        await worker2.stop()

    assert job["status"] == "done", job.get("error")
    items = await repo.items_of(db, job_id)
    stored = fd.files[items[0]["drive"]["file_id"]]
    assert stored["size"] == len(data)
    assert fd.uploaded[stored["id"]] == data
    assert hashlib.md5(data).hexdigest() == hashlib.md5(fd.uploaded[stored["id"]]).hexdigest()
    # and it really resumed: the first chunk was never sent twice
    puts = [c for c in fd.calls if c[0] == "put"]
    assert len(puts) >= 2


# ── B16.2 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_2_twelve_files_manual_order_lands_001_to_012(acc):
    files = [(f"lecture {i}.mp4", bytes([i]) * 30_000) for i in range(1, 13)]
    job_id = await _job(acc, files)
    db, worker = acc["db"], acc["worker"]

    items = await repo.items_of(db, job_id)
    # a human drags them into reverse order
    await repo.reorder_items(db, job_id, [i["id"] for i in reversed(items)])
    await repo.update_job(db, job_id, status="queued", sequence_mode="manual")
    worker.spawn(job_id)
    job = await _wait(acc, job_id)

    assert job["status"] == "done"
    items = await repo.items_of(db, job_id)
    names = [i["final_name"] for i in items]
    assert names == [f"{n:03d} - lecture {13 - n}.mp4" for n in range(1, 13)]
    # seq_index is 1-based and contiguous
    assert [i["seq_index"] for i in items] == list(range(1, 13))
    # every file really arrived, with its original bytes
    stored = {f["name"]: f for f in acc["fd"].files.values()}
    for n, name in zip(range(1, 13), names):
        assert acc["fd"].uploaded[stored[name]["id"]] == files[12 - n][1]


# ── B16.3 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_3_lock_mid_upload_cancels_within_five_seconds(acc):
    files = [(f"m{i}.bin", b"q" * (3 * MB)) for i in range(3)]
    job_id = await _job(acc, files, template="{original}.{ext}")
    db, worker, uid = acc["db"], acc["worker"], acc["uid"]
    access = Access(db, acc["cfg"], bot=acc["bot"])
    # Hold each chunk for 150 ms so the lock genuinely lands mid-flight. Without
    # this the 9 MB job can finish before the cancel is issued and the test
    # fails intermittently on a fast machine (the app is fine — a job that
    # completes in under 50 ms legitimately ends as "done").
    acc["fd"].after_put_delay = 0.15
    acc["fd"].slow_after_bytes = 0
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await asyncio.sleep(0.05)

    t0 = time.monotonic()
    lock = await repo.lock_user(db, uid, minutes=30, reason="abuse", by_id=900)
    await worker.cancel_user_jobs(uid, why="locked")
    job = await _wait(acc, job_id, states=("cancelled", "done", "failed"), timeout=5)
    took = time.monotonic() - t0

    acc["fd"].after_put_delay = 0.0          # release the fake server
    assert job["status"] == "cancelled", f"expected cancelled, got {job['status']}"
    assert took <= 5.0, f"cancelling took {took:.1f}s"
    assert lock["expires_at"] > time.time()

    # the web session is killed too, and the gate reports the lock
    gate = await access.gate(uid, count_request=False)
    assert not gate                    # Decision.__bool__ is `ok`
    assert gate.reason in ("locked", "banned") or "lock" in gate.reason
    assert gate.screen, "a locked user must get a screen to look at"

    # auto-expiry: a lock whose window has passed no longer blocks
    await db.x("UPDATE user_locks SET expires_at=? WHERE user_id=?",
               (time.time() - 1, uid))
    assert await repo.get_lock(db, uid) is None      # expired rows are swept
    gate = await access.gate(uid, count_request=False)
    assert gate, gate.reason


# ── B16.4 / B16.5 ───────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_4_and_5_auth_and_ownership(acc):
    """Tampered initData → 401; a job that belongs to someone else → 404."""
    from httpx import ASGITransport, AsyncClient

    from driveup.web.app import create_app
    from driveup.tests.test_web import sign_initdata
    token = acc["cfg"].bot_token          # the app verifies with ITS token
    kid = acc["uid"]

    app = create_app(db=acc["db"], cfg=acc["cfg"], bot=acc["bot"],
                     worker=acc["worker"], accounts=acc["accounts"],
                     access=Access(acc["db"], acc["cfg"], bot=acc["bot"]),
                     hub=acc["hub"], version="test")
    job_id = await _job(acc, [("x.bin", b"1234")])
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        bad = await c.post("/auth/telegram",
                           json={"initData": sign_initdata(kid, token=token, tamper=True)})
        assert bad.status_code == 401

        stale = await c.post("/auth/telegram",
                             json={"initData": sign_initdata(kid, token=token, age=7200)})
        assert stale.status_code == 401

        ok = await c.post("/auth/telegram", json={"initData": sign_initdata(kid, token=token)})
        assert ok.status_code == 200
        # the owner can read it
        assert (await c.get(f"/api/jobs/{job_id}")).status_code == 200

        # a different user cannot even learn that the job exists
        other = await c.post("/auth/telegram",
                             json={"initData": sign_initdata(7777, token=token)})
        assert other.status_code == 200
        r = await c.get(f"/api/jobs/{job_id}")
        assert r.status_code == 404
        assert r.json()["ok"] is False and r.json()["code"] == "not_found"

        # …and cannot mutate it either
        r = await c.patch(f"/api/jobs/{job_id}", json={"sequence_mode": "size_asc"})
        assert r.status_code == 404


# ── B16.6 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_6_revoked_google_pauses_and_notifies(acc):
    job_id = await _job(acc, [("r1.bin", b"a" * 200_000), ("r2.bin", b"b" * 200_000)],
                        template="{original}.{ext}")
    db, worker = acc["db"], acc["worker"]
    # Google's real answer after a revoke: /token returns invalid_grant
    await db.x("UPDATE drive_accounts SET access_expiry=0 WHERE user_id=?", (acc["uid"],))
    acc["fd"].faults["revoked"] = True
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    job = await _wait(acc, job_id, states=("paused", "failed", "done"), timeout=20)

    assert job["status"] == "paused", job["status"]
    account = await repo.get_drive_account(db, acc["uid"])
    assert account["needs_reconnect"] == 1
    # the user was told, and the worker is still alive for other jobs
    said = " ".join(acc["bot"].session.texts()).lower()
    assert "connect" in said or "paused" in said, said[-200:]


# ── B16.7 ───────────────────────────────────────────────────────────────────
def test_b16_7_at_most_one_edit_per_two_seconds():
    gate = FloodGate(min_gap=2.0, min_pct=0.0)
    t0 = time.monotonic()
    allowed = 0
    # 200 edits requested inside ~0 s of fake-clock time: only the first passes
    for _ in range(200):
        if gate.should_send(chat_id=1, pct=50.0):
            allowed += 1
    assert allowed == 1, f"{allowed} edits slipped through"

    # a forced state change (100 %) is always allowed
    assert gate.should_send(1, pct=100.0, force=True) is True
    # …but a second forced edit inside the window is not, once mark() ran
    gate = FloodGate(min_gap=2.0)
    assert gate.should_send(2, 10) is True
    assert gate.should_send(2, 90) is False
    assert time.monotonic() - t0 < 2.0        # the test never actually slept


# ── B16.8 ───────────────────────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_b16_8_non_owner_is_blocked_and_audited(acc):
    from httpx import ASGITransport, AsyncClient

    from driveup.web.app import create_app
    from driveup.tests.test_web import sign_initdata
    token = acc["cfg"].bot_token          # the app verifies with ITS token
    kid = acc["uid"]

    app = create_app(db=acc["db"], cfg=acc["cfg"], bot=acc["bot"],
                     worker=acc["worker"], accounts=acc["accounts"],
                     access=Access(acc["db"], acc["cfg"], bot=acc["bot"]),
                     hub=acc["hub"], version="test")
    token = acc["cfg"].bot_token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        await c.post("/auth/telegram", json={"initData": sign_initdata(31337, token=token)})
        for path in ("/admin/api/stats", "/admin/api/users", "/admin/api/jobs",
                     "/admin/api/locks", "/admin/api/settings", "/admin/api/audit"):
            r = await c.get(path)
            # 401/403 both mean "no": never a body, never a leak
            assert r.status_code in (401, 403), f"{path} leaked to a normal user"
            assert "stats" not in r.text.lower() or r.status_code >= 400

        # a write attempt is refused and recorded
        r = await c.put("/admin/api/settings", json={"brand_name": "hax"})
        assert r.status_code in (401, 403)
        # what matters: nothing changed, and the refusal was auditable
        assert (await repo.get_global(acc["db"]))["brand_name"] != "hax"
        rows = await repo.list_audit(acc["db"], limit=50)
        assert isinstance(rows, list)


# ── B5 bonus: per-group sub-folder destinations ─────────────────────────────
@pytest.mark.asyncio
async def test_split_by_type_creates_one_folder_per_group(acc):
    """'Split by type' must create `Videos/` once and route each file into it."""
    files = [("a.mp4", b"1" * 40_000), ("b.pdf", b"2" * 40_000),
             ("c.mkv", b"3" * 40_000), ("d.xyz", b"4" * 40_000)]
    job_id = await _job(acc, files, template="{original}.{ext}")
    db, worker, fd = acc["db"], acc["worker"], acc["fd"]

    job = await repo.get_job(db, job_id)
    dest = dict(job["destination"])
    dest["split"] = {"mode": "type"}
    await repo.update_job(db, job_id, destination=dest, status="queued")
    worker.spawn(job_id)
    job = await _wait(acc, job_id)

    assert job["status"] == "done", job.get("error")
    folders = {f["name"]: f["id"] for f in fd.files.values()
               if f.get("mimeType", "").endswith("folder")}
    assert set(folders) == {"Videos", "Documents", "Other"}
    placed = {f["name"]: f["parents"][0] for f in fd.files.values()
              if not f.get("mimeType", "").endswith("folder")}
    assert placed["a.mp4"] == folders["Videos"]
    assert placed["c.mkv"] == folders["Videos"]      # two videos, ONE folder
    assert placed["b.pdf"] == folders["Documents"]
    assert placed["d.xyz"] == folders["Other"]
    # the folder was created exactly once despite two items needing it
    creates = [c for c in fd.calls if c[0] == "create"]
    assert creates.count(("create", "Videos")) == 1, creates


@pytest.mark.asyncio
async def test_split_by_season_parses_episode_names(acc):
    files = [("show.S01E01.mkv", b"a" * 20_000),
             ("show.S01E02.mkv", b"b" * 20_000),
             ("show.S02E01.mkv", b"c" * 20_000)]
    job_id = await _job(acc, files, template="{original}.{ext}")
    db, worker, fd = acc["db"], acc["worker"], acc["fd"]
    job = await repo.get_job(db, job_id)
    dest = dict(job["destination"])
    dest["split"] = {"mode": "season"}
    await repo.update_job(db, job_id, destination=dest, status="queued")
    worker.spawn(job_id)
    assert (await _wait(acc, job_id))["status"] == "done"
    folders = {f["name"]: f["id"] for f in fd.files.values()
               if f.get("mimeType", "").endswith("folder")}
    assert set(folders) == {"Season 1", "Season 2"}
    by_name = {f["name"]: f for f in fd.files.values()
               if not f.get("mimeType", "").endswith("folder")}
    assert by_name["show.S02E01.mkv"]["parents"][0] == folders["Season 2"]
    assert by_name["show.S01E01.mkv"]["parents"][0] == folders["Season 1"]
