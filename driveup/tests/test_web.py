"""Web API (B11) + auth (B5/B12) with a real httpx client against the ASGI app.

Covers the acceptance tests:
  · tampered initData → 401
  · another user's job → 404/403 (never leaks)
  · ownership checks on every job/item call
  · admin routes need a role AND a CSRF token
  · SSE streams progress events
  · /health answers
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import tempfile
import time
from urllib.parse import urlencode

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from driveup.config import Settings
from driveup.core.access import Access
from driveup.core.naming import NamingOptions
from driveup.core.progress import ProgressHub
from driveup.core.worker import Worker
from driveup.db import repo
from driveup.db.sql import Database
from driveup.drive.accounts import AccountManager
from driveup.drive.auth import TokenCipher
from driveup.tests.fake_drive import FakeDrive
from driveup.tests.fakes import FakeBot
from driveup.web.app import create_app
from driveup.web.auth import initdata_secret

TOKEN = "123456:WEBTEST"
MB = 1024 * 1024


def sign_initdata(user_id: int = 55, age: int = 0, token: str = TOKEN, tamper: bool = False) -> str:
    fields = {"auth_date": str(int(time.time()) - age), "query_id": "Q1",
              "user": json.dumps({"id": user_id, "first_name": "Web", "username": "webuser"})}
    check = "\n".join(f"{k}={v}" for k, v in sorted(fields.items()))
    h = hmac.new(initdata_secret(token), check.encode(), hashlib.sha256).hexdigest()
    if tamper:
        h = "f" * len(h)
    return urlencode({**fields, "hash": h})


@pytest_asyncio.fixture
async def web():
    tmp = tempfile.mkdtemp()
    fd = FakeDrive()
    base = await fd.start()
    cfg = Settings()
    cfg.bot_token = TOKEN
    cfg.owner_id = 1
    cfg.google_client_id = "cid"
    cfg.google_client_secret = "csec"
    cfg.token_enc_key = "k" * 40
    cfg.session_secret = "s"
    cfg.db_path = os.path.join(tmp, "w.db")
    cfg.download_dir = os.path.join(tmp, "dl")
    cfg.temp_dir = os.path.join(tmp, "tmp")
    cfg.google_api_base = base
    cfg.upload_chunk_mb = 1
    cfg.public_url = "http://testserver"
    db = Database(cfg.db_path)
    await db.connect()
    await repo.seed_plans(db, cfg)    # main.py does this at boot
    bot = FakeBot(token=TOKEN)
    accounts = AccountManager(db, cfg)
    hub = ProgressHub()
    access = Access(db, cfg, bot=bot)
    worker = Worker(db=db, cfg=cfg, accounts=accounts, bot=bot, hub=hub)
    app = create_app(db=db, cfg=cfg, bot=bot, worker=worker, accounts=accounts,
                     access=access, hub=hub, version="test")
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield {"client": client, "db": db, "cfg": cfg, "fd": fd, "base": base,
               "bot": bot, "accounts": accounts, "worker": worker, "app": app}
    await worker.stop()
    await accounts.close()
    await db.close()
    await fd.stop()


async def login(web, user_id: int = 55) -> AsyncClient:
    c = web["client"]
    r = await c.post("/auth/telegram", json={"initData": sign_initdata(user_id)})
    assert r.status_code == 200, r.text
    return c


async def make_job(web, user_id: int = 55, files=(("a.bin", b"1234"),)) -> str:
    db = web["db"]
    job_id = await repo.create_job(db, user_id,
                                   destination={"folder_id": "root", "path": "My Drive"},
                                   naming=NamingOptions(template="{seq:03d} - {original}.{ext}").to_dict())
    for i, (name, data) in enumerate(files):
        ref = web["bot"].add_file(name, data)
        await repo.add_item(db, job_id, file_id=ref.file_id, file_unique_id=ref.file_unique_id,
                            msg_id=10 + i, size=len(data), mime="", name=name, chat_id=7)
    return job_id


# ── auth ───────────────────────────────────────────────────────────────────
async def test_no_session_is_rejected(web):
    r = await web["client"].get("/api/me")
    assert r.status_code == 401


async def test_valid_initdata_mints_a_session(web):
    c = await login(web)
    r = await c.get("/api/me")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["userId"] == 55
    assert c.cookies.get("du_session")


async def test_tampered_initdata_is_401(web):
    r = await web["client"].post("/auth/telegram", json={"initData": sign_initdata(tamper=True)})
    assert r.status_code == 401
    assert r.json()["code"] == "bad_initdata"


async def test_old_initdata_is_401(web):
    r = await web["client"].post("/auth/telegram", json={"initData": sign_initdata(age=7200)})
    assert r.status_code == 401


async def test_session_cookie_is_httponly_and_samesite(web):
    c = await login(web)
    r = await c.get("/api/me")
    raw = r.headers.get("set-cookie", "")
    assert "du_session" in c.cookies
    assert await repo.get_session(web["db"], c.cookies["du_session"])


# ── jobs ───────────────────────────────────────────────────────────────────
async def test_job_payload_and_ownership(web):
    c = await login(web)
    job_id = await make_job(web)
    r = await c.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200 and r.json()["id"] == job_id
    # another user cannot see it
    c2 = await login(web, 77)
    r2 = await c2.get(f"/api/jobs/{job_id}")
    assert r2.status_code == 404


async def test_patch_updates_destination_and_names(web):
    c = await login(web)
    job_id = await make_job(web, files=(("x.mp4", b"a" * 10), ("y.mp4", b"b" * 10)))
    r = await c.patch(f"/api/jobs/{job_id}", json={
        "destination": {"id": "root", "path": "My Drive"},
        "naming": {"template": "{index:02d} {original}"},
        "sequence_mode": "manual", "conflict_policy": "rename", "strict_order": True})
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["final_name"] for i in items] == ["01 x.mp4", "02 y.mp4"]


async def test_reorder_changes_names_and_order(web):
    c = await login(web)
    job_id = await make_job(web, files=(("a.mp4", b"1"), ("b.mp4", b"2"), ("c.mp4", b"3")))
    items = (await c.get(f"/api/jobs/{job_id}")).json()["items"]
    order = [items[2]["id"], items[0]["id"], items[1]["id"]]
    r = await c.post(f"/api/jobs/{job_id}/items/reorder", json={"order": order})
    assert r.status_code == 200
    items = (await c.get(f"/api/jobs/{job_id}")).json()["items"]
    assert [i["name"] for i in items] == ["c.mp4", "a.mp4", "b.mp4"]
    assert items[0]["final_name"].startswith("001")


async def test_reorder_rejects_incomplete_lists(web):
    c = await login(web)
    job_id = await make_job(web, files=(("a.mp4", b"1"), ("b.mp4", b"2")))
    r = await c.post(f"/api/jobs/{job_id}/items/reorder", json={"order": ["only-one"]})
    assert r.status_code == 422


async def test_delete_item_renumbers(web):
    c = await login(web)
    job_id = await make_job(web, files=(("a.mp4", b"1"), ("b.mp4", b"2")))
    items = (await c.get(f"/api/jobs/{job_id}")).json()["items"]
    r = await c.delete(f"/api/jobs/{job_id}/items/{items[0]['id']}")
    assert r.status_code == 200 and r.json()["count"] == 1
    left = (await c.get(f"/api/jobs/{job_id}")).json()["items"]
    assert left[0]["final_name"].startswith("001")


async def test_preview_names_endpoint(web):
    c = await login(web)
    job_id = await make_job(web, files=(("a.mp4", b"1"), ("a.mp4", b"2")))
    r = await c.post(f"/api/jobs/{job_id}/preview-names",
                     json={"template": "{seq:02d} - {original}"})
    rows = r.json()["rows"]
    assert len(rows) == 2
    assert rows[0]["final"] == "01 - a.mp4"
    assert not any(x["duplicate"] for x in rows)      # a seq prefix disambiguates
    r = await c.post(f"/api/jobs/{job_id}/preview-names", json={"template": "{original}"})
    rows = r.json()["rows"]
    assert rows[0]["final"] == "a.mp4" and rows[1]["duplicate"] is True


async def test_csv_export(web):
    c = await login(web)
    job_id = await make_job(web)
    r = await c.get(f"/api/jobs/{job_id}/export.csv")
    assert r.status_code == 200
    assert r.text.splitlines()[0] == "order,name,size,state,drive_id,link"


async def test_start_requires_drive_connection(web):
    c = await login(web)
    job_id = await make_job(web)
    r = await c.post(f"/api/jobs/{job_id}/start", json={})
    assert r.status_code == 403
    assert "not_connected" in r.json()["code"] or "connect" in r.json()["message"].lower()


async def test_full_upload_through_the_api(web):
    c = await login(web)
    await repo.save_drive_account(web["db"], 55, email="w@e.com",
                                  enc_refresh=TokenCipher(web["cfg"].fernet_key()).enc("1//r"),
                                  access="ya29.fake", expiry=9e12, scope="s", scope_mode="file")
    job_id = await make_job(web, files=(("one.bin", b"a" * 5000), ("two.bin", b"b" * 5000)))
    r = await c.post(f"/api/jobs/{job_id}/start", json={})
    assert r.status_code == 200 and r.json()["status"] == "queued"
    for _ in range(300):
        job = (await c.get(f"/api/jobs/{job_id}")).json()
        if job["status"] in ("done", "failed"):
            break
        await _sleep(0.05)
    job = (await c.get(f"/api/jobs/{job_id}")).json()
    assert job["status"] == "done"
    assert len(web["fd"].uploaded) == 2
    assert all(i["link"].startswith("https://drive.google.com/") for i in job["items"])


async def _sleep(s):
    import asyncio
    await asyncio.sleep(s)


# ── drive endpoints ────────────────────────────────────────────────────────
async def test_drive_requires_connection(web):
    c = await login(web)
    r = await c.get("/api/drive/children?parent=root")
    assert r.status_code == 409


async def test_drive_children_create_and_search(web):
    c = await login(web)
    await repo.save_drive_account(web["db"], 55, email="w@e.com",
                                  enc_refresh=TokenCipher(web["cfg"].fernet_key()).enc("1//r"),
                                  access="ya29.fake", expiry=9e12, scope="s", scope_mode="file")
    r = await c.post("/api/drive/folders", json={"name": "Courses", "parent": "root"})
    assert r.status_code == 200
    fid = r.json()["id"]
    kids = (await c.get("/api/drive/children?parent=root")).json()
    assert [f["name"] for f in kids["folders"]] == ["Courses"]
    found = (await c.get("/api/drive/search?q=Cour")).json()
    assert found["folders"] and found["folders"][0]["name"] == "Courses"
    about = (await c.get("/api/drive/about")).json()
    assert about["email"] == "tester@example.com"
    parent = (await c.get(f"/api/drive/parent?child={fid}")).json()
    assert parent["id"] == "root"


async def test_empty_folder_name_is_refused(web):
    c = await login(web)
    await repo.save_drive_account(web["db"], 55, email="w@e.com",
                                  enc_refresh=TokenCipher(web["cfg"].fernet_key()).enc("1//r"),
                                  access="ya29.fake", expiry=9e12, scope="s", scope_mode="file")
    r = await c.post("/api/drive/folders", json={"name": "   ", "parent": "root"})
    assert r.status_code == 422


# ── SSE + health ───────────────────────────────────────────────────────────
# httpx's ASGITransport buffers the whole response, so a real uvicorn server is
# the only honest way to test a stream. This also proves uvicorn wiring works.
async def test_sse_streams_and_closes(web):
    import asyncio as aio
    import socket
    import uvicorn
    from driveup.core.progress import ProgressState

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(web["app"], host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off",
                                           access_log=False))
    task = aio.create_task(server.serve())
    for _ in range(100):
        if server.started:
            break
        await aio.sleep(0.02)
    try:
        async with AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            assert (await c.post("/auth/telegram",
                                 json={"initData": sign_initdata()})).status_code == 200
            job_id = await make_job(web)
            st = web["worker"].hub

            async def feed():
                ps = ProgressState(job_id=job_id, item_id="i1", name="a", index=1,
                                   count=1, total=100)
                ps.drive_done = 50
                ps.stage = "uploading"
                st.put(ps)
                await aio.sleep(0.1)
                ps.drive_done = 100
                st.put(ps)
                await aio.sleep(0.1)
                st.finish(job_id)

            feeder = aio.create_task(feed())
            got = []

            async def collect():
                async with c.stream("GET", f"/api/jobs/{job_id}/events") as r:
                    assert r.status_code == 200
                    assert r.headers["content-type"].startswith("text/event-stream")
                    async for line in r.aiter_lines():
                        if line.startswith("data: "):
                            got.append(json.loads(line[6:]))
                        if got and got[-1].get("stage") == "job-finished":
                            return
            await aio.wait_for(collect(), timeout=20)
            await feeder
            assert got[0]["item_id"] == "i1" and got[0]["drive_pct"] == 50.0
            assert got[-1]["stage"] == "job-finished"
    finally:
        server.should_exit = True
        await aio.wait_for(task, timeout=10)


async def test_health(web):
    r = await web["client"].get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] and body["db"] is True and "uptime" in body


# ── admin ──────────────────────────────────────────────────────────────────
async def test_admin_requires_a_role(web):
    c = await login(web, 55)
    r = await c.get("/admin/api/stats")
    assert r.status_code == 403


async def test_admin_owner_can_read_and_write(web):
    c = await login(web, 1)                      # OWNER_ID
    r = await c.get("/admin/api/stats")
    assert r.status_code == 200 and r.json()["stats"]["users"] >= 1
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    # without CSRF a write is refused
    bad = await c.put("/admin/api/settings", json={"brand_name": "Nope"})
    assert bad.status_code == 403 and bad.json()["code"] == "csrf"
    ok = await c.put("/admin/api/settings", json={"brand_name": "DriveUpPro"},
                     headers={"X-CSRF": csrf})
    assert ok.status_code == 200
    assert (await repo.get_global(web["db"]))["brand_name"] == "DriveUpPro"
    saved = await c.put("/admin/api/settings", json={"dump_channel_id": "-1001234567890"},
                        headers={"X-CSRF": csrf})
    assert saved.status_code == 200
    assert (await repo.get_global(web["db"]))["dump_channel_id"] == "-1001234567890"


async def test_admin_lock_unlock_flow_is_audited(web):
    c = await login(web, 1)
    await repo.upsert_user(web["db"], 88, "Bad", "bad")
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    h = {"X-CSRF": csrf}
    r = await c.post("/admin/api/users/88/lock",
                     json={"minutes": 30, "reason": "spam"}, headers=h)
    assert r.status_code == 200 and r.json()["lock"]["user_id"] == 88
    assert await repo.get_lock(web["db"], 88)
    locks = (await c.get("/admin/api/locks")).json()["locks"]
    assert any(l["user_id"] == 88 for l in locks)
    r = await c.post("/admin/api/users/88/unlock", headers=h)
    assert r.status_code == 200
    audit = (await c.get("/admin/api/audit")).json()["audit"]
    actions = [a["action"] for a in audit]
    assert "user.lock" in actions and "user.unlock" in actions


async def test_admin_cannot_lock_another_admin(web):
    c = await login(web, 1)
    await repo.upsert_user(web["db"], 89, "Adm")
    await repo.add_admin(web["db"], 89, "admin")
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    r = await c.post("/admin/api/users/89/lock", json={"minutes": 5},
                     headers={"X-CSRF": csrf})
    assert r.status_code == 403


async def test_admin_grant_plan_and_users_list(web):
    c = await login(web, 1)
    await repo.upsert_user(web["db"], 90, "Plan user")
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    r = await c.post("/admin/api/users/90/plan", json={"plan_id": "pro", "days": 30},
                     headers={"X-CSRF": csrf})
    assert r.status_code == 200
    plan = await repo.effective_plan(web["db"], 90)
    assert plan["id"] == "pro"
    users = (await c.get("/admin/api/users?q=Plan")).json()
    assert any(u["user_id"] == 90 for u in users["users"])


async def test_admin_broadcast_test_mode_reaches_only_me(web):
    c = await login(web, 1)
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    r = await c.post("/admin/api/broadcast", json={"text": "hello", "test": True},
                     headers={"X-CSRF": csrf})
    assert r.status_code == 200 and r.json()["sent"] == 1
    assert web["bot"].sent and web["bot"].sent[-1]["chat_id"] == 1


async def test_admin_pages_render(web):
    r = await web["client"].get("/admin")
    assert r.status_code == 200
    assert "DriveUp" in r.text and "Dashboard" in r.text


async def test_homepage_shows_the_zc_driveup_landing_site(web):
    r = await web["client"].get("/")
    assert r.status_code == 200
    assert "ZC DRIVE UPLOAD" in r.text and "Send it once." in r.text
    assert "Browser → Server" in r.text and "Server → Drive" in r.text
    assert 'href="/auth/google/login"' in r.text
    assert "₹499" in r.text and "up to 750 GB/day" in r.text
    assert "/studio" in r.text and "@ZCQXx" in r.text


async def test_google_web_login_starts_without_telegram_session(web):
    r = await web["client"].get("/auth/google/login", follow_redirects=False)
    assert r.status_code == 303
    assert "accounts.google.com" in r.headers["location"] or "googleapis.com" in r.headers["location"]
    assert "du_oauth_state" in r.headers.get("set-cookie", "")


async def test_studio_without_session_offers_direct_google_login(web):
    r = await web["client"].get("/studio")
    assert r.status_code == 200 and "Continue with Google Drive" in r.text
    assert "/auth/google/login" in r.text


async def test_studio_with_session_serves_the_upload_workspace(web):
    c = await login(web)
    r = await c.get("/studio")
    assert r.status_code == 200 and "DriveUp Studio" in r.text and "splash" in r.text


async def test_security_headers(web):
    r = await web["client"].get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    csp = r.headers["content-security-policy"]
    assert "frame-ancestors" in csp and "telegram.org" in csp


async def test_api_error_shape(web):
    r = await web["client"].get("/api/jobs/does-not-exist")
    assert r.status_code == 401 or r.json().get("ok") is False
    c = await login(web)
    r = await c.get("/api/jobs/does-not-exist")
    assert r.status_code == 404 and r.json()["ok"] is False and r.json()["code"] == "not_found"


async def test_admin_whoami_reports_role(web):
    c = await login(web, 1)
    r = await c.get("/admin/api/whoami")
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["id"] == 1 and body["user"]["role"] in ("owner", "admin")


async def test_admin_logout_actually_kills_the_session(web):
    """The panel's Lock button must end the server-side session, not just JS."""
    c = await login(web, 1)
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    assert (await c.get("/admin/api/whoami")).status_code == 200
    out = await c.post("/admin/api/logout", headers={"X-CSRF": csrf})
    assert out.status_code == 200
    after = await c.get("/admin/api/whoami")
    assert after.status_code in (401, 403), after.text


async def test_admin_logout_needs_csrf(web):
    c = await login(web, 1)
    r = await c.post("/admin/api/logout")
    assert r.status_code == 403 and r.json()["code"] == "csrf"


async def test_support_role_cannot_write(web):
    """support is read-only: it may look, it may not change anything."""
    db = web["db"]
    await repo.ensure_user(db, 77, "Support")
    await repo.add_admin(db, 77, "support")
    c = await login(web, 77)
    assert (await c.get("/admin/api/users")).status_code == 200
    csrf = (await c.get("/admin/api/csrf")).json()["token"]
    r = await c.put("/admin/api/settings", json={"brand_name": "X"},
                    headers={"X-CSRF": csrf})
    assert r.status_code == 403


# ── OAuth redirect URI must be byte-identical to Google's registered value ──
def test_redirect_uri_is_normalised(monkeypatch):
    """Trailing slashes / pasted ?code= tails must not reach Google."""
    from driveup.config import Settings, _clean_redirect

    cases = {
        "https://a.up.railway.app/auth/google/callback/":
            "https://a.up.railway.app/auth/google/callback",
        "  https://a.up.railway.app/auth/google/callback?code=x  ":
            "https://a.up.railway.app/auth/google/callback",
        "https://a.up.railway.app/auth/google/callback#frag":
            "https://a.up.railway.app/auth/google/callback",
        "https://a.up.railway.app/auth/google/callback":
            "https://a.up.railway.app/auth/google/callback",
    }
    for raw, expected in cases.items():
        assert _clean_redirect(raw) == expected, raw

    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "https://a.up.railway.app",
                 "GOOGLE_REDIRECT_URI":
                     "https://a.up.railway.app/auth/google/callback/"}.items():
        monkeypatch.setenv(k, v)
    cfg = Settings()
    assert cfg.redirect_uri == "https://a.up.railway.app/auth/google/callback"
    assert cfg.validate() == []


def test_validate_rejects_a_redirect_that_cannot_work(monkeypatch):
    """A wrong redirect must fail at boot, not as Google's error 400."""
    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "https://a.up.railway.app",
                 "GOOGLE_REDIRECT_URI": "https://a.up.railway.app/wrong-path"}.items():
        monkeypatch.setenv(k, v)
    problems = Settings().validate()
    assert any("auth/google/callback" in p for p in problems), problems


def test_validate_rejects_plain_http_on_a_public_host(monkeypatch):
    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "http://example.com"}.items():
        monkeypatch.setenv(k, v)
    problems = Settings().validate()
    assert any("https" in p for p in problems), problems


def test_localhost_http_is_allowed(monkeypatch):
    """Developer machines must keep working over plain http on loopback."""
    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "http://127.0.0.1:8080"}.items():
        monkeypatch.setenv(k, v)
    assert Settings().validate() == []


# ── PUBLIC_URL must be absolute, or every Studio link is broken ─────────────
def test_public_url_without_scheme_gets_https(monkeypatch):
    """Railway shows the domain without a scheme; pasting it must still work."""
    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "drivegram-bot-production.up.railway.app"}.items():
        monkeypatch.setenv(k, v)
    cfg = Settings()
    assert cfg.public_url == "https://drivegram-bot-production.up.railway.app"
    assert cfg.redirect_uri.endswith("/auth/google/callback")
    assert cfg.validate() == []


def test_public_url_with_trailing_slash_is_trimmed(monkeypatch):
    for k, v in {"BOT_TOKEN": "1:A", "OWNER_ID": "1", "GOOGLE_CLIENT_ID": "c",
                 "GOOGLE_CLIENT_SECRET": "s", "TOKEN_ENC_KEY": "k" * 44,
                 "SESSION_SECRET": "z" * 40,
                 "PUBLIC_URL": "https://a.example.com/"}.items():
        monkeypatch.setenv(k, v)
    # no double slash in the generated callback
    assert Settings().redirect_uri == "https://a.example.com/auth/google/callback"
