"""A fake Google Drive that speaks the REAL resumable protocol.

Used by the test-suite (and by anyone who wants to run DriveUp without a Google
account). It implements just enough of v3 to exercise every code path:

  POST  /upload/drive/v3/files?uploadType=resumable  → 200 + Location
  PUT   /upload/drive/v3/files/{id}?upload_id=…      → 308 + Range | 200 + file json
  PUT   …/files?uploadType=resumable&upload_id=…     → bytes */N status probe
  GET   /drive/v3/about                              → quota
  GET   /drive/v3/files                              → list / search
  POST  /drive/v3/files                              → create folder
  GET   /drive/v3/files/{id}                         → metadata
  POST  /drive/v3/files/{id}/permissions             → share
  DELETE …/files/{id}                                → delete

Fault injection (`faults` dict) lets a test ask for a 500 on chunk N, a 404 on
the session, a quota error, or a 401 so the resume/auth paths run for real.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time

from aiohttp import web

FOLDER = "application/vnd.google-apps.folder"


class FakeDrive:
    def __init__(self, *, quota_limit: int = 15 * 1024 ** 3) -> None:
        self.files: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.quota_limit = quota_limit
        self.quota_used = 0
        self.calls: list[tuple[str, str]] = []
        self.faults: dict = {}          # {"fail_chunks": [2], "drop_session": False, "quota": False}
        self.after_put_delay: float = 0.0   # slow uploads down for kill/resume tests
        self.slow_after_bytes: int = 0
        self.uploaded: dict[str, bytes] = {}
        self._runner = None
        self._site = None
        self.port = 0

    # ── server lifecycle ─────────────────────────────────────────────────
    async def start(self) -> str:
        app = web.Application(client_max_size=1024 ** 3)
        app.router.add_post("/upload/drive/v3/files", self._resumable_start)
        app.router.add_put("/upload/drive/v3/files/{file_id}", self._resumable_put)
        app.router.add_put("/upload/drive/v3/files", self._resumable_put)
        app.router.add_get("/drive/v3/about", self._about)
        app.router.add_get("/drive/v3/files", self._list)
        app.router.add_post("/drive/v3/files", self._create)
        app.router.add_post("/drive/v3/files/{file_id}/copy", self._copy)
        app.router.add_get("/drive/v3/files/{file_id}", self._get)
        app.router.add_post("/drive/v3/files/{file_id}/permissions", self._perm)
        app.router.add_delete("/drive/v3/files/{file_id}", self._delete)
        app.router.add_get("/drive/v3/drives", self._drives)
        app.router.add_post("/token", self._token)
        app.router.add_post("/revoke", self._revoke)
        app.router.add_get("/openidconnect/v1/userinfo", self._userinfo)

        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await self._site.start()
        for s in self._site._server.sockets:      # type: ignore[union-attr]
            self.port = s.getsockname()[1]
            break
        return f"http://127.0.0.1:{self.port}"

    def add_folder(self, name: str, parent: str = "root") -> str:
        fid = "d" + secrets.token_hex(6)
        self.files[fid] = {"id": fid, "name": name, "mimeType": FOLDER,
                           "parents": [parent], "size": "0",
                           "webViewLink": f"https://drive.google.com/drive/folders/{fid}"}
        return fid

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    # ── resumable ────────────────────────────────────────────────────────
    async def _resumable_start(self, req: web.Request) -> web.Response:
        self.calls.append(("start", req.query.get("uploadType", "")))
        raw = await req.read()
        if self.faults.get("start_500"):
            return web.Response(status=500, text='{"error":{"code":500}}')
        meta = {}
        if raw:
            try:
                meta = json.loads(raw)
            except ValueError:
                meta = {}
        sid = secrets.token_hex(8)
        self.sessions[sid] = {"meta": meta, "data": bytearray(), "total": int(
            req.headers.get("X-Upload-Content-Length", "0") or 0)}
        location = (f"http://127.0.0.1:{self.port}/upload/drive/v3/files/up{sid}"
                    f"?uploadType=resumable&upload_id={sid}")
        return web.Response(status=200, headers={"Location": location})

    async def _resumable_put(self, req: web.Request) -> web.Response:
        sid = req.query.get("upload_id", "")
        body = await req.read()
        sess = self.sessions.get(sid)
        if not sess:
            self.calls.append(("put", "404"))
            return web.Response(status=404, text="no such session")
        # NOTE: a status probe (Content-Range: bytes */N) with Content-Length 0
        # must still report what is committed — it is the resume anchor.
        rng = req.headers.get("Content-Range", "")
        if rng.startswith("bytes */"):
            total = int(rng.split("/")[-1])
            sess["total"] = total
            if len(sess["data"]) >= total:
                return await self._finish(sid, sess)
            self.calls.append(("status", str(len(sess["data"]))))
            hdrs = {} if not sess["data"] else {"Range": f"bytes=0-{len(sess['data']) - 1}"}
            return web.Response(status=308, headers=hdrs)

        # fault: 500 on a chosen chunk index
        fails = self.faults.get("fail_chunks") or []
        if fails and (len(sess["data"]) // (8 * 1024 * 1024) + 1) in fails:
            fails.remove(len(sess["data"]) // (8 * 1024 * 1024) + 1)
            self.calls.append(("put", "500"))
            return web.Response(status=500, text='{"error":{"code":500}}')
        if self.faults.get("quota"):
            return web.Response(status=403, text='{"error":{"errors":[{"reason":"storageQuotaExceeded"}]}}')
        if self.faults.get("drop_session"):
            self.faults["drop_session"] = False
            self.sessions.pop(sid, None)
            return web.Response(status=404, text="gone")

        start = int(rng.split(" ")[1].split("-")[0]) if " " in rng else 0
        sess["data"] = sess["data"][:start] + bytearray(body)
        total = int(rng.split("/")[-1])
        sess["total"] = total
        self.calls.append(("put", str(len(sess["data"]))))
        if self.after_put_delay and len(sess["data"]) >= self.slow_after_bytes:
            await asyncio.sleep(self.after_put_delay)
        if len(sess["data"]) >= total:
            return await self._finish(sid, sess)
        return web.Response(status=308, headers={
            "Range": f"bytes=0-{len(sess['data']) - 1}"})

    async def _finish(self, sid: str, sess: dict) -> web.Response:
        data = bytes(sess["data"])
        fid = "f" + secrets.token_hex(6)
        meta = sess["meta"] or {}
        rec = {"id": fid, "name": meta.get("name", "file"), "mimeType": meta.get("mimeType", ""),
               "size": len(data), "webViewLink": f"https://drive.google.com/file/d/{fid}/view",
               "parents": meta.get("parents", ["root"]), "md5Checksum": hashlib.md5(data).hexdigest(),
               "createdTime": time.strftime("%Y-%m-%dT%H:%M:%SZ")}
        self.files[fid] = rec
        self.uploaded[fid] = data
        self.quota_used += len(data)
        self.sessions.pop(sid, None)
        self.calls.append(("done", str(len(data))))
        return web.Response(status=200, content_type="application/json", text=json.dumps(rec))

    # ── read API ─────────────────────────────────────────────────────────
    async def _about(self, req: web.Request) -> web.Response:
        return web.json_response({"user": {"emailAddress": "tester@example.com", "displayName": "Tester"},
                                  "storageQuota": {"limit": str(self.quota_limit),
                                                   "usage": str(self.quota_used),
                                                   "usageInDrive": str(self.quota_used),
                                                   "usageInDriveTrash": "0"}})

    async def _list(self, req: web.Request) -> web.Response:
        q = req.query.get("q", "")
        files = [f for f in self.files.values() if f["mimeType"] == FOLDER] \
            if "mimeType='application/vnd.google-apps.folder'" in q else list(self.files.values())
        import re
        m = re.search(r"name contains '([^']*)'", q)
        if m:
            files = [f for f in files if m.group(1).lower() in f["name"].lower()]
        m = re.search(r"name='([^']*)'", q)
        if m:
            files = [f for f in files if f["name"] == m.group(1)]
        m = re.search(r"'([^']+)' in parents", q)
        if m and m.group(1) != "root":
            files = [f for f in files if m.group(1) in f.get("parents", [])]
        return web.json_response({"files": files})

    async def _create(self, req: web.Request) -> web.Response:
        meta = await req.json()
        fid = "d" + secrets.token_hex(6) if meta.get("mimeType") == FOLDER else "f" + secrets.token_hex(6)
        rec = {"id": fid, "name": meta.get("name", ""), "mimeType": meta.get("mimeType", ""),
               "parents": meta.get("parents", ["root"]), "webViewLink":
               f"https://drive.google.com/drive/folders/{fid}", "size": "0"}
        self.files[fid] = rec
        self.calls.append(("create", rec["name"]))
        return web.json_response(rec)

    async def _copy(self, req: web.Request) -> web.Response:
        """Server-side copy — mirrors Drive's files().copy."""
        src = self.files.get(req.match_info["file_id"])
        if not src:
            return web.Response(status=404, text='{"error":"notFound"}')
        body = await req.json() if req.can_read_body else {}
        parents = body.get("parents") or ["root"]
        new_id = "f" + secrets.token_hex(6)
        rec = dict(src, id=new_id, parents=parents,
                   name=req.query.get("name") or src["name"])
        self.files[new_id] = rec
        self.calls.append(("copy", src["name"]))
        return web.json_response(rec)

    async def _get(self, req: web.Request) -> web.Response:
        fid = req.match_info["file_id"]
        if fid not in self.files:
            return web.Response(status=404, text="not found")
        return web.json_response(self.files[fid])

    async def _perm(self, req: web.Request) -> web.Response:
        self.calls.append(("share", req.match_info["file_id"]))
        return web.json_response({"role": "reader", "type": "anyone"})

    async def _delete(self, req: web.Request) -> web.Response:
        fid = req.match_info["file_id"]
        self.files.pop(fid, None)
        self.calls.append(("delete", fid))
        return web.Response(status=204)

    async def _drives(self, req: web.Request) -> web.Response:
        return web.json_response({"drives": [{"id": "sd1", "name": "Team Drive"}]})

    async def _token(self, req: web.Request) -> web.Response:
        if self.faults.get("revoked"):
            # Google's actual answer after a user revokes access
            return web.json_response({"error": "invalid_grant",
                                      "error_description": "Token has been expired or revoked."},
                                     status=400)
        form = await req.post()
        if form.get("grant_type") == "refresh_token" and form.get("refresh_token") == "bad":
            return web.Response(status=400, text='{"error":"invalid_grant"}')
        return web.json_response({"access_token": "ya29.fake", "expires_in": 3600,
                                  "refresh_token": "1//refresh-fake", "scope": "drive.file",
                                  "token_type": "Bearer"})

    async def _revoke(self, req: web.Request) -> web.Response:
        await req.read()
        return web.Response(status=200)

    async def _userinfo(self, req: web.Request) -> web.Response:
        return web.json_response({"sub": "1234", "email": "tester@example.com", "name": "Tester"})


async def run_standalone(port: int = 8899) -> None:
    d = FakeDrive()
    app_url = await d.start()
    print(f"fake drive on {app_url}")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(run_standalone())
