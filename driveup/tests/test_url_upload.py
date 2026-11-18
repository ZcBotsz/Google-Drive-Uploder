"""A user pastes a link; the bytes must reach Drive without touching Telegram.

The feature comes from the three reference bots this round, but the mechanism
does not: their versions used `wget` through a shell (command injection), or
downloaded to disk first (no ceiling), or had no SSRF check at all.
"""
from __future__ import annotations

import pytest
from aiohttp import web

import driveup.net.fetch as fetch
from driveup.db import repo
from driveup.tests.test_worker import env, make_job, wait_done  # noqa: F401

PAYLOAD = b"Z" * 64_000


@pytest.fixture
async def public_host(monkeypatch):
    """A real HTTP server, with the public-URL check relaxed for loopback.

    The guard itself is tested separately; here we need it out of the way to
    exercise the streaming path.
    """
    async def handler(request):
        return web.Response(body=PAYLOAD, headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(len(PAYLOAD)),
        })

    async def redirect(request):
        raise web.HTTPFound("/file.bin")

    app = web.Application()
    app.router.add_get("/file.bin", handler)
    app.router.add_get("/go", redirect)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 9922)
    await site.start()
    real = fetch.assert_public_url
    monkeypatch.setattr(fetch, "assert_public_url",
                        lambda u: u if "169.254" in u else real.__wrapped__(u)
                        if hasattr(real, "__wrapped__") else u)
    yield "http://127.0.0.1:9922"
    await runner.cleanup()


async def test_a_link_is_uploaded_without_telegram(env, public_host):
    job_id = await make_job(env, [("x.bin", b"1" * 10)])
    items = await repo.items_of(env["db"], job_id)
    await repo.set_item(env["db"], items[0]["id"],
                        tg={"url": f"{public_host}/file.bin", "name": "movie.mp4",
                            "size": 0, "file_id": "", "file_unique_id": "", "msg_id": 0})
    await repo.update_job(env["db"], job_id, status="queued")
    env["worker"].spawn(job_id)

    job = await wait_done(env["worker"], job_id, env["db"], timeout=40)
    items = await repo.items_of(env["db"], job_id)
    assert job["status"] == "done", f"got {job['status']}"
    assert items[0]["state"] == "done"
    assert items[0]["bytes_done"] == len(PAYLOAD)
    assert items[0]["drive"]["file_id"], "nothing was written to Drive"


# ── Drive → Drive copy (from the cdfxscrq reference) ───────────────────────
async def test_copy_file_never_downloads(env):
    """A copy must be one Drive call, not a download + re-upload."""
    accounts = env["accounts"]
    client = await accounts.client(env["uid"])
    folder = await client.create_folder("src")
    # a real file to copy
    env["fd"].files["fsrc"] = {"id": "fsrc", "name": "lecture.mp4",
                               "mimeType": "video/mp4", "parents": ["root"], "size": "1234"}
    made = await client.copy_file("fsrc", folder["id"])
    assert made["id"] and made["id"] != "fsrc"
    assert made["parents"] == [folder["id"]]
    assert any(c[0] == "copy" for c in env["fd"].calls), "no copy call was made"


async def test_copy_tree_builds_the_same_shape(env):
    client = await env["accounts"].client(env["uid"])
    src = await client.create_folder("Course")
    sub = await client.create_folder("Module 1", src["id"])
    # two files under the subfolder
    for n in ("a.mp4", "b.mp4"):
        fid = "f" + n.replace(".", "")
        env["fd"].files[fid] = {"id": fid, "name": n, "mimeType": "video/mp4",
                               "parents": [sub["id"]], "size": "100"}
    dest = await client.copy_tree(src["id"], "root")
    assert dest["files"] == 2, f"copied {dest['files']} of 2"
    names = {f["name"] for f in env["fd"].files.values()}
    assert "Module 1" in names and "a.mp4" in names


# ── custom thumbnails (from All-Url-Uploader) ─────────────────────────────
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 500 + b"\xff\xd9"


async def test_thumbnail_round_trip(env):
    from driveup.db import repo
    assert await repo.get_user_thumb(env["db"], env["uid"]) == b""
    await repo.set_user_thumb(env["db"], env["uid"], JPEG)
    assert await repo.get_user_thumb(env["db"], env["uid"]) == JPEG
    # replacing works, clearing works
    await repo.set_user_thumb(env["db"], env["uid"], JPEG * 2)
    assert len(await repo.get_user_thumb(env["db"], env["uid"])) == len(JPEG) * 2
    assert await repo.set_user_thumb(env["db"], env["uid"], b"") is True
    assert await repo.get_user_thumb(env["db"], env["uid"]) == b""


async def test_thumbnail_becomes_content_hints(env):
    from driveup.core.thumbs import apply_thumb
    from driveup.db import repo

    class W:                       # the only attribute apply_thumb touches
        db = env["db"]

    meta = await apply_thumb(W, {"name": "x.mp4"}, env["uid"])
    assert "contentHints" not in meta, "no thumbnail saved, nothing added"

    await repo.set_user_thumb(env["db"], env["uid"], JPEG)
    meta = await apply_thumb(W, {"name": "x.mp4"}, env["uid"])
    assert meta["contentHints"]["thumbnail"]["mimeType"] == "image/jpeg"
    import base64
    assert base64.b64decode(meta["contentHints"]["thumbnail"]["image"]) == JPEG


def test_photo_handler_only_fires_when_armed():
    """Regression guard: /thumb_set must not swallow normal photo uploads.

    Putting `F.photo` in front of the uploader without an opt-in would mean a
    connected user's photos silently stop reaching Drive.
    """
    import inspect
    from driveup.bot.handlers import thumb
    src = inspect.getsource(thumb.on_photo)
    assert "thumb_await" in src, "on_photo no longer checks the arming set"
    assert "waiting.discard" in src, "arming is not one-shot"
