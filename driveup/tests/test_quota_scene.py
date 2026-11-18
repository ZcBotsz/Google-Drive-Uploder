"""The production scene, reproduced: a full Drive with several files queued.

Reported from Railway logs:

    driveup.worker  item failed: e94c70659a4b4024
    driveup.drive.errors.StorageQuota: Drive storage is full (storageQuotaExceeded)

The old behaviour marked each remaining file failed in turn — five files meant
five identical tracebacks and no explanation. These tests pin the fix.
"""
from __future__ import annotations

import pytest

from driveup.db import repo
from driveup.tests.test_worker import env, make_job, wait_done  # noqa: F401


async def test_a_full_drive_pauses_once_instead_of_failing_every_file(env):
    files = [(f"f{i}.bin", b"z" * 1000) for i in range(5)]
    job_id = await make_job(env, files)
    env["fd"].faults["quota"] = True          # ← Drive is full, as in production
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)

    job = await wait_done(worker, job_id, db, timeout=30)
    items = await repo.items_of(db, job_id)
    failed = [i for i in items if i["state"] == "failed"]
    pending = [i for i in items if i["state"] == "pending"]

    assert job["status"] == "paused", f"expected paused, got {job['status']}"
    assert len(failed) == 0, f"{len(failed)} files were marked failed (old bug)"
    assert len(pending) == 5, "every file should stay resumable"


async def test_the_user_is_told_why_and_what_to_do(env):
    files = [(f"g{i}.bin", b"z" * 1000) for i in range(3)]
    job_id = await make_job(env, files)
    env["fd"].faults["quota"] = True
    db, worker = env["db"], env["worker"]
    await repo.update_job(db, job_id, status="queued")
    worker.spawn(job_id)
    await wait_done(worker, job_id, db, timeout=30)

    said = " ".join(m.get("text", "") for m in env["bot"].sent).lower()
    assert "full" in said, "the user was never told Drive is full"
    assert "resume" in said, "the user was not told how to continue"
    # and NOT a bare traceback string
    assert "storagequotaexceeded" not in said
