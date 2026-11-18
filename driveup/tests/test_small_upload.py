"""The report: "main KB ki file de raha hun, upload nahi ho rahi".

A handful of kilobytes must travel message → draft job → Start → Drive with no
gate, cap or preflight check refusing them. This drives the real dispatcher, so
routing, middleware and callbacks are all exercised.
"""
from __future__ import annotations

import pytest

from driveup.db import repo
from driveup.tests import fake_tg
from driveup.tests.test_bot import botenv, connect_drive, feed, said  # noqa: F401
from driveup.tests.test_worker import wait_done


async def test_a_3kb_document_reaches_drive(botenv):
    await connect_drive(botenv)
    payload = b"h" * 3072                              # 3 KB
    update, body = fake_tg.document(payload, name="small.txt", uid=42,
                                    mime="text/plain", file_id="SMALL1")
    botenv["sess"].add_file("small.txt", body, file_id="SMALL1")
    await feed(botenv, update)
    assert "1 file(s)" in said(botenv), f"no job card appeared:\n{said(botenv)}"

    job_id = botenv["app"].draft_jobs.get(42)
    assert job_id, "no draft job was created for a 3 KB file"
    items = await repo.items_of(botenv["db"], job_id)
    assert len(items) == 1 and items[0]["tg"]["size"] == 3072

    # press Start exactly as a user would
    await feed(botenv, fake_tg.callback(f"v1:start:{job_id}", uid=42))
    job = await wait_done(botenv["worker"], job_id, botenv["db"], timeout=40)
    assert job["status"] == "done", f"job ended {job['status']}: {said(botenv)}"
    files = [f for f in botenv["fd"].files.values() if f["name"].endswith("small.txt")]
    assert files, f"nothing landed in Drive: {list(botenv['fd'].files)}"


async def test_a_failure_says_why_in_the_chat(botenv):
    """A bare "0 done · 1 failed" is what made a working bot look broken."""
    from driveup.core import failures
    from driveup.drive.errors import PermissionDenied, StorageQuota

    line = failures.explain(StorageQuota("quota"))
    assert "full" in line.lower() and "/connect" in line
    line = failures.explain(PermissionDenied("nope"))
    assert "/folders" in line or "folder" in line.lower()
    # an unknown error must not be dressed up as a known cause
    assert "Unexpected" in failures.explain(ValueError("weird"))


async def test_worker_tells_the_user_when_a_job_dies(botenv):
    """If the worker gives up, the chat must carry the reason, not just a count."""
    from driveup.core.worker import Worker
    from driveup.drive.errors import StorageQuota

    await connect_drive(botenv)
    app = botenv["app"]
    said_before = len(botenv["sess"].texts())

    update, body = fake_tg.document(b"z" * 1024, name="x.bin", uid=42, file_id="D1")
    botenv["sess"].add_file("x.bin", body, file_id="D1")
    await feed(botenv, update)
    job_id = app.draft_jobs[42]
    from driveup.db import repo as r
    await r.update_job(botenv["db"], job_id,
                       status_message={"chat_id": 42, "message_id": 777})

    await botenv["worker"]._tell_failure(job_id, StorageQuota("quota"))
    new_text = "\n".join(botenv["sess"].texts()[said_before:])
    assert "full" in new_text.lower(), f"no explanation was sent:\n{new_text}"
    assert "x.bin" in new_text, "the failing file was not named"


async def test_summary_card_explains_a_failed_file(botenv, monkeypatch):
    """The card said "0 done · 1 failed" — it must now say WHY.

    Reported twice: the file never left, nothing explained it, so the only
    reading was "the bot is broken". The reason is already known at failure
    time; this asserts it reaches the chat.
    """
    await connect_drive(botenv)
    app = botenv["app"]
    update, body = fake_tg.document(b"z" * 2048, name="broken.txt", uid=42, file_id="FX1")
    botenv["sess"].add_file("broken.txt", body, file_id="FX1")
    await feed(botenv, update)
    job_id = app.draft_jobs[42]

    async def boom(*args, **kwargs):
        from driveup.drive.errors import PermissionDenied
        raise PermissionDenied("no access to that folder")

    monkeypatch.setattr(botenv["worker"], "_process_item", boom)

    await feed(botenv, fake_tg.callback(f"v1:start:{job_id}", uid=42))
    job = await wait_done(botenv["worker"], job_id, botenv["db"], timeout=40)
    assert job["status"] == "done"

    text = said(botenv)
    assert "Why it failed" in text, f"no failure block in the card:\n{text}"
    assert "broken.txt" in text, "the failing file was not named"
    assert "folder" in text.lower(), "the cause was not spelled out"

    items = await repo.items_of(botenv["db"], job_id)
    assert items[0]["state"] == "failed"
    assert "folder" in (items[0]["error"] or "").lower(), (
        "the stored error should be the plain reason, not a raw repr")
