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


async def test_user_channel_setup_isolation_and_routing(botenv, monkeypatch):
    from types import SimpleNamespace
    e = botenv
    app = e["app"]
    channels = {-1001111111111: {42}, -1002222222222: {43}}
    async def chat(chat_id):
        if chat_id not in channels:
            raise ValueError("unknown channel")
        return SimpleNamespace(type="channel")
    async def me():
        return SimpleNamespace(id=999)
    async def member(chat_id, user_id):
        return SimpleNamespace(status="administrator" if user_id == 999 or user_id in channels[chat_id] else "member")
    monkeypatch.setattr(app.bot, "get_chat", chat)
    monkeypatch.setattr(app.bot, "get_me", me)
    monkeypatch.setattr(app.bot, "get_chat_member", member)
    for uid, ch in [(42, -1001111111111), (43, -1002222222222)]:
        await feed(e, fake_tg.callback("v1:autoset", uid=uid, chat_id=uid, mid=50+uid))
        await feed(e, fake_tg.message(str(ch), uid=uid, chat_id=uid, mid=100+uid))
    bindings = await e["db"].q("SELECT * FROM channel_bindings ORDER BY user_id")
    assert [(r["user_id"], r["channel_id"]) for r in bindings] == [(42, "-1001111111111"), (43, "-1002222222222")]
    await feed(e, post(10, channel=-1001111111111))
    await feed(e, post(11, channel=-1002222222222))
    b42 = await e["db"].one("SELECT * FROM channel_batches WHERE owner_id=42")
    b43 = await e["db"].one("SELECT * FROM channel_batches WHERE owner_id=43")
    assert b42 and b43 and b42["job_id"] != b43["job_id"]
    assert (await repo.get_job(e["db"], b42["job_id"]))["user_id"] == 42
    assert (await repo.items_of(e["db"], b43["job_id"]))[0]["tg"]["chat_id"] == -1002222222222
    await feed(e, fake_tg.callback(f"v1:chfinish:{b43['job_id']}", uid=42, chat_id=42, mid=300))
    assert (await e["db"].one("SELECT state FROM channel_batches WHERE owner_id=43"))["state"] == "open"
    await feed(e, fake_tg.message("/finish", uid=42, chat_id=42, mid=301))
    assert (await e["db"].one("SELECT state FROM channel_batches WHERE owner_id=42"))["state"] == "finished"
    assert (await e["db"].one("SELECT state FROM channel_batches WHERE owner_id=43"))["state"] == "open"
    channels[-1002222222222].clear()  # revoked administrator: fail closed
    await feed(e, post(12, channel=-1002222222222))
    assert len(await repo.items_of(e["db"], b43["job_id"])) == 1
    await feed(e, fake_tg.callback("v1:autoset", uid=44, chat_id=44, mid=302))
    await feed(e, fake_tg.message("-1001111111111", uid=44, chat_id=44, mid=303))
    assert await e["db"].one("SELECT * FROM channel_bindings WHERE user_id=44") is None


async def test_channel_job_folder_review_and_isolation(web):
    from driveup.drive.auth import TokenCipher
    client = await login(web, 55)
    job_id = await make_job(web, user_id=55, files=(("one.mp4", b"1"), ("two.mp4", b"22"), ("three.mp4", b"333")))
    await repo.update_job(web["db"], job_id, source={"kind": "telegram_channel", "channel_id": str(CHANNEL)})
    await web["db"].x("INSERT INTO channel_batches(channel_id,owner_id,job_id,state,updated_at) VALUES(?,?,?,'open',?)", (str(CHANNEL), 55, job_id, time.time()))
    items = await repo.items_of(web["db"], job_id)
    payload = {"destination": {"folder_id": "root", "path": "My Drive", "split": {
        "mode": "batches", "groups": [{"name":"First", "count":1}, {"name":"Second", "count":2}],
        "plan": {items[0]["id"]:["First"], items[1]["id"]:["Second"], items[2]["id"]:["Second"]}}}}
    assert (await client.patch(f"/api/jobs/{job_id}", json=payload)).status_code == 409
    await web["db"].x("UPDATE channel_batches SET state='finished' WHERE job_id=?", (job_id,))
    response = await client.patch(f"/api/jobs/{job_id}", json=payload)
    assert response.status_code == 200, response.text
    await repo.save_drive_account(web["db"], 55, email="user@example.test", enc_refresh=TokenCipher(web["cfg"].fernet_key()).enc("1//r"), access="ya29.fake", expiry=9e12, scope="s", scope_mode="file")
    drive = await web["accounts"].client(55)
    job = await repo.get_job(web["db"], job_id)
    for item, name in zip(items, ["First", "Second", "Second"]):
        assert (await web["worker"]._folder_for(job, item, drive))["path"].endswith(name)
    other = await login(web, 56)
    assert (await other.get(f"/api/jobs/{job_id}")).status_code == 404
    assert (await other.patch(f"/api/jobs/{job_id}", json=payload)).status_code == 404


async def test_six_hundred_channel_posts_keep_one_ordered_draft(botenv, monkeypatch):
    from types import SimpleNamespace
    e = botenv
    app = e["app"]
    channel = -1003333333333
    async def chat(_): return SimpleNamespace(type="channel")
    async def me(): return SimpleNamespace(id=999)
    async def member(_channel, _user): return SimpleNamespace(status="administrator")
    monkeypatch.setattr(app.bot, "get_chat", chat)
    monkeypatch.setattr(app.bot, "get_me", me)
    monkeypatch.setattr(app.bot, "get_chat_member", member)
    await e["db"].x("UPDATE plans SET max_files_job=650 WHERE id='free'")
    await feed(e, fake_tg.callback("v1:autoset", uid=42, chat_id=42, mid=700))
    await feed(e, fake_tg.message(str(channel), uid=42, chat_id=42, mid=701))
    # Submit in reverse, emulating tasks completed out of order.
    await asyncio.gather(*(feed(e, post(i, channel=channel)) for i in range(600, 0, -1)))
    row = await e["db"].one("SELECT * FROM channel_batches WHERE owner_id=42")
    items = await repo.items_of(e["db"], row["job_id"])
    assert len(items) == 600
    assert [i["tg"]["msg_id"] for i in items] == list(range(1, 601))
    assert (await repo.get_job(e["db"], row["job_id"]))["status"] == "draft"
    await feed(e, fake_tg.message("/finish", uid=42, chat_id=42, mid=702))
    assert (await e["db"].one("SELECT state FROM channel_batches WHERE owner_id=42"))["state"] == "finished"
