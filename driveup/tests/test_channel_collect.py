"""Channel intake through the real dispatcher, without Telegram or Google credentials."""
import asyncio
import time

from aiogram.types import Update

from driveup.db import repo
from driveup.tests import fake_tg
from driveup.tests.test_bot import botenv, feed  # noqa: F401
from driveup.tests.test_web import web, login, make_job  # noqa: F401

CHANNEL = -1001234567890


def post(mid, channel=CHANNEL, file_id=None, caption="original caption"):
    return Update.model_validate({"update_id": 10000 + mid, "channel_post": {
        "message_id": mid, "date": int(time.time()),
        "chat": {"id": channel, "type": "channel", "title": "private"},
        "caption": caption,
        "document": {"file_id": file_id or f"file{mid}", "file_unique_id": file_id or f"unique{mid}",
                     "file_size": 123, "file_name": f"file{mid}.mp4", "mime_type": "video/mp4"}}})


async def test_channel_collection_and_finish(botenv):
    e = botenv
    e["cfg"].collect_channel_id = str(CHANNEL)
    await feed(e, post(1, channel=CHANNEL-1))
    assert await e["db"].q("SELECT * FROM channel_batches") == []
    # Separate posts can arrive in any processing order; order by message id.
    await asyncio.gather(feed(e, post(20, file_id="same")), feed(e, post(10, file_id="same")))
    row = await e["db"].one("SELECT * FROM channel_batches")
    assert row and row["owner_id"] == 1 and row["state"] == "open"
    job = await repo.get_job(e["db"], row["job_id"])
    assert job["status"] == "draft"
    items = await repo.items_of(e["db"], job["id"])
    assert [i["tg"]["msg_id"] for i in items] == [10, 20]
    assert all(i["tg"]["caption"] == "original caption" for i in items)
    await feed(e, post(10, file_id="same"))
    assert len(await repo.items_of(e["db"], job["id"])) == 2
    await feed(e, fake_tg.message("/finish", uid=42))
    assert (await e["db"].one("SELECT * FROM channel_batches"))["state"] == "open"
    await feed(e, fake_tg.message("/finish", uid=1, chat_id=1, mid=99))
    assert (await e["db"].one("SELECT * FROM channel_batches"))["state"] == "finished"
    assert (await repo.get_job(e["db"], job["id"]))["status"] == "draft"
    await feed(e, post(20, file_id="same"))  # redelivery after finish is ignored
    assert (await e["db"].one("SELECT * FROM channel_batches"))["state"] == "finished"
    await feed(e, post(30))
    new_row = await e["db"].one("SELECT * FROM channel_batches")
    assert new_row["state"] == "open" and new_row["job_id"] != job["id"]


async def test_button_and_status(botenv):
    e = botenv
    e["cfg"].collect_channel_id = str(CHANNEL)
    await feed(e, post(1))
    row = await e["db"].one("SELECT * FROM channel_batches")
    await feed(e, fake_tg.message("/channel_status", uid=1, chat_id=1, mid=98))
    assert any("Collecting: 1 files" in x for x in e["sess"].texts())
    await feed(e, fake_tg.callback(f"v1:chfinish:{row['job_id']}", uid=1, chat_id=1, mid=99))
    assert (await e["db"].one("SELECT * FROM channel_batches"))["state"] == "finished"


async def test_web_cannot_start_or_retry_open_batch(web):
    client = await login(web)
    job_id = await make_job(web)
    await web["db"].x("INSERT INTO channel_batches(channel_id, owner_id, job_id, state, updated_at) VALUES(?,?,?,'open',?)", (str(CHANNEL), 55, job_id, time.time()))
    for action in ("start", "resume", "retry-failed"):
        response = await client.post(f"/api/jobs/{job_id}/{action}", json={})
        assert response.status_code == 409, response.text
    assert (await repo.get_job(web["db"], job_id))["status"] == "draft"


async def test_rejections_are_persisted_and_visible(botenv):
    e = botenv
    e["cfg"].collect_channel_id = str(CHANNEL)
    await feed(e, post(1))
    payload = post(2).model_dump(exclude_none=True)
    payload["channel_post"]["document"]["file_size"] = 25 * 1024 * 1024
    await feed(e, Update.model_validate(payload))
    row = await e["db"].one("SELECT * FROM channel_batches")
    assert len(await repo.items_of(e["db"], row["job_id"])) == 1
    rejects = await e["db"].q("SELECT * FROM channel_rejections WHERE job_id=?", (row["job_id"],))
    assert [r["message_id"] for r in rejects] == [2]
    await feed(e, fake_tg.message("/channel_status", uid=1, chat_id=1, mid=98))
    assert any("post 2" in x for x in e["sess"].texts())


async def test_worker_refuses_open_batch(botenv):
    e = botenv
    e["cfg"].collect_channel_id = str(CHANNEL)
    await feed(e, post(1))
    row = await e["db"].one("SELECT * FROM channel_batches")
    await e["worker"]._run_job(row["job_id"])
    assert (await repo.get_job(e["db"], row["job_id"]))["status"] == "draft"
    assert (await repo.items_of(e["db"], row["job_id"]))[0]["state"] == "pending"
