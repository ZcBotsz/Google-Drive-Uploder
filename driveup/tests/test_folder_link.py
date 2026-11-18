"""Pasting a Drive folder link must set the destination, not start an upload.

`on_link` (any non-command text) is registered *before* `on_link_text` (the
folder-link prompt). aiogram stops at the first handler whose filters match, so
the pasted folder URL was eaten by the upload intake and the folder silently
never changed — the same handler-collision class of bug the reference review
warned about, here in our own code.
"""
from __future__ import annotations

from driveup.bot.keyboards import cb
from driveup.db import repo
from driveup.tests import fake_tg
from driveup.tests.test_bot import botenv, connect_drive, feed, said  # noqa: F401

FOLDER_URL = "https://drive.google.com/drive/folders/1AbCdEfGhIjKlMnOpQrStUv"


async def test_pasted_folder_link_sets_the_folder(botenv):
    await connect_drive(botenv)
    app = botenv["app"]

    update, body = fake_tg.document(b"q" * 1024, name="one.bin", uid=42, file_id="F1")
    botenv["sess"].add_file("one.bin", body, file_id="F1")
    await feed(botenv, update)
    job_id = app.draft_jobs[42]
    before = len(await repo.items_of(botenv["db"], job_id))

    await feed(botenv, fake_tg.callback(cb("setlink"), uid=42))
    assert 42 in app.pending_link, "the prompt did not arm the folder-link state"

    await feed(botenv, fake_tg.message(FOLDER_URL, uid=42))

    job = await repo.get_job(botenv["db"], job_id)
    assert job["destination"]["folder_id"] == "1AbCdEfGhIjKlMnOpQrStUv", (
        "the pasted folder link did not become the destination")
    after = len(await repo.items_of(botenv["db"], job_id))
    assert after == before, "the folder link was queued as an upload item instead"
    assert 42 not in app.pending_link, "the pending flag was not cleared"
    assert "Destination" in said(botenv), f"no confirmation was shown:\n{said(botenv)}"
