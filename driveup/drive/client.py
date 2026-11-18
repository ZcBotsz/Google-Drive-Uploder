"""Drive v3 client — folders, search, about, create, share. Async, one session."""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import aiohttp

from .errors import (AuthExpired, DriveError, NotFound, PermissionDenied,
                     RetryableDriveError, classify)
from .upload import ResumableUploader

FOLDER_MIME = "application/vnd.google-apps.folder"
FIELDS_FILE = "id,name,mimeType,size,webViewLink,parents,createdTime,modifiedTime,md5Checksum"
FIELDS_LIST = f"nextPageToken,files({FIELDS_FILE})"


class DriveClient:
    """One per connected user-session; cheap to build, holds no per-request state."""

    def __init__(self, http: aiohttp.ClientSession, token: str, *,
                 token_refresher=None, sleep=asyncio.sleep, base: str = "") -> None:
        self.http = http
        base = (base or "").rstrip("/")
        self.API = f"{base}/drive/v3" if base else "https://www.googleapis.com/drive/v3"
        self.UPLOAD = f"{base}/upload/drive/v3/files" if base else "https://www.googleapis.com/upload/drive/v3/files"
        self.token = token
        self._refresh = token_refresher        # async () -> str (new token)
        self._sleep = sleep

    # ── plumbing ─────────────────────────────────────────────────────────
    def _h(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def _call(self, method: str, url: str, *, params=None, json_body=None,
                    attempts: int = 3, expect=(200,)) -> dict:
        last: Optional[DriveError] = None
        for n in range(attempts):
            async with self.http.request(method, url, params=params, json=json_body,
                                         headers=self._h(),
                                         timeout=aiohttp.ClientTimeout(total=60)) as r:
                body = await r.text()
                if r.status in expect:
                    if not body:
                        return {}
                    import json
                    try:
                        return json.loads(body)
                    except ValueError:
                        return {}
                err = classify(r.status, body)
                if isinstance(err, AuthExpired) and self._refresh:
                    self.token = await self._refresh()
                    continue
                if isinstance(err, RetryableDriveError) and n + 1 < attempts:
                    last = err
                    await self._sleep(min(8, 2 ** n))
                    continue
                raise err
        raise last or DriveError("request failed")

    # ── about / quota ────────────────────────────────────────────────────
    async def about(self) -> dict:
        data = await self._call("GET", f"{self.API}/about", params={
            "fields": "user(displayName,emailAddress),storageQuota(limit,usage,usageInDrive)",
            "supportsAllDrives": "true",
        })
        user = data.get("user", {})
        q = data.get("storageQuota", {})
        limit = int(q.get("limit", 0) or 0)
        usage = int(q.get("usage", 0) or 0)
        return {"email": user.get("emailAddress", ""), "name": user.get("displayName", ""),
                "limit": limit, "usage": usage, "free": max(0, limit - usage) if limit else -1}

    # ── folders ──────────────────────────────────────────────────────────
    async def list_children(self, parent: str = "root", *, page_token: str = "",
                            page_size: int = 100, q_extra: str = "", order: str = "folder,name") -> dict:
        q = f"'{parent}' in parents and trashed=false"
        if q_extra:
            q = f"{q} and ({q_extra})"
        params = {
            "q": q, "pageSize": str(min(1000, page_size)), "fields": FIELDS_LIST,
            "orderBy": order, "supportsAllDrives": "true",
            "includeItemsFromAllDrives": "true", "corpora": "user",
        }
        if page_token:
            params["pageToken"] = page_token
        data = await self._call("GET", f"{self.API}/files", params=params)
        return {"files": data.get("files", []), "next": data.get("nextPageToken", "")}

    async def list_folders(self, parent: str = "root", *, page_token: str = "",
                           page_size: int = 100) -> dict:
        return await self.list_children(parent, page_token=page_token, page_size=page_size,
                                        q_extra=f"mimeType='{FOLDER_MIME}'")

    async def search(self, term: str, *, folders_only: bool = False,
                     page_size: int = 50) -> List[dict]:
        term = (term or "").replace("'", "\\'")[:200]
        q = f"name contains '{term}' and trashed=false"
        if folders_only:
            q += f" and mimeType='{FOLDER_MIME}'"
        data = await self._call("GET", f"{self.API}/files", params={
            "q": q, "pageSize": str(min(200, page_size)), "fields": FIELDS_LIST,
            "supportsAllDrives": "true", "includeItemsFromAllDrives": "true",
        })
        return data.get("files", [])

    async def create_folder(self, name: str, parent: str = "root") -> dict:
        meta = {"name": name, "mimeType": FOLDER_MIME}
        if parent and parent != "root":
            meta["parents"] = [parent]
        return await self._call("POST", f"{self.API}/files", params={
            "supportsAllDrives": "true", "fields": FIELDS_FILE}, json_body=meta)

    async def get(self, file_id: str) -> dict:
        return await self._call("GET", f"{self.API}/files/{file_id}", params={
            "fields": FIELDS_FILE, "supportsAllDrives": "true"})

    async def path_of(self, file_id: str, depth: int = 8) -> tuple[str, str]:
        """`My Drive › Courses › Module 1` + the id path (root last)."""
        names: List[str] = []
        ids: List[str] = []
        cur = file_id
        for _ in range(depth):
            if not cur or cur == "root":
                names.append("My Drive")
                break
            meta = await self.get(cur)
            names.append(meta.get("name", cur))
            ids.append(cur)
            parents = meta.get("parents") or []
            cur = parents[0] if parents else "root"
        return " › ".join(reversed(names)), "/".join(reversed(ids))

    async def exists_named(self, name: str, parent: str) -> Optional[dict]:
        name_esc = (name or "").replace("'", "\\'")
        data = await self._call("GET", f"{self.API}/files", params={
            "q": f"name='{name_esc}' and '{parent}' in parents and trashed=false",
            "pageSize": "1", "fields": FIELDS_LIST, "supportsAllDrives": "true",
        })
        files = data.get("files", [])
        return files[0] if files else None

    async def find_by_app_property(self, key: str, value: str) -> Optional[dict]:
        data = await self._call("GET", f"{self.API}/files", params={
            "q": f"appProperties has {{ key='{key}' and value='{value}' }} and trashed=false",
            "pageSize": "1", "fields": FIELDS_LIST, "supportsAllDrives": "true",
        })
        files = data.get("files", [])
        return files[0] if files else None

    async def shared_drives(self) -> List[dict]:
        try:
            data = await self._call("GET", f"{self.API}/drives", params={"pageSize": "50",
                                                                   "fields": "drives(id,name)"})
            return data.get("drives", [])
        except (PermissionDenied, NotFound, DriveError):
            return []

    # ── share ────────────────────────────────────────────────────────────
    async def share_anyone(self, file_id: str) -> dict:
        return await self._call("POST", f"{self.API}/files/{file_id}/permissions", params={
            "supportsAllDrives": "true"}, json_body={"role": "reader", "type": "anyone"})

    async def delete(self, file_id: str) -> None:
        await self._call("DELETE", f"{self.API}/files/{file_id}", params={"supportsAllDrives": "true"},
                         expect=(200, 204))

    # ── upload ───────────────────────────────────────────────────────────
    def uploader(self, *, chunk: int, on_progress=None, should_cancel=None,
                 sleep=asyncio.sleep) -> ResumableUploader:
        return ResumableUploader(self.http, self.token, chunk=chunk,
                                 on_progress=on_progress, should_cancel=should_cancel,
                                 sleep=sleep, upload_url=self.UPLOAD)

    async def upload_stream(self, stream, *, name: str, mime: str, parent: str,
                            size: int, chunk: int, app_props: Optional[dict] = None,
                            description: str = "", on_progress=None,
                            should_cancel=None, session_uri: str = "",
                            committed: int = 0) -> dict:
        up = self.uploader(chunk=chunk, on_progress=on_progress,
                           should_cancel=should_cancel)
        if session_uri:
            up.session_uri = session_uri
            up.committed = committed
        meta: Dict[str, Any] = {"name": name, "mimeType": mime or "application/octet-stream"}
        if parent and parent != "root":
            meta["parents"] = [parent]
        if description:
            meta["description"] = description[:2000]
        if app_props:
            meta["appProperties"] = {k: str(v) for k, v in app_props.items()}
        info = await up.upload(stream, meta, size)
        info.setdefault("session_uri", up.session_uri)
        info["md5_local"] = up.md5.hexdigest()
        return info

    async def upload_file(self, path, *, name: str, mime: str, parent: str,
                          chunk: int, app_props: Optional[dict] = None,
                          description: str = "", on_progress=None,
                          should_cancel=None, session_uri: str = "",
                          committed: int = 0) -> dict:
        import os
        size = os.path.getsize(path)
        up = self.uploader(chunk=chunk, on_progress=on_progress, should_cancel=should_cancel)
        if session_uri:
            up.session_uri = session_uri
            up.committed = committed
        meta: Dict[str, Any] = {"name": name, "mimeType": mime or "application/octet-stream"}
        if parent and parent != "root":
            meta["parents"] = [parent]
        if description:
            meta["description"] = description[:2000]
        if app_props:
            meta["appProperties"] = {k: str(v) for k, v in app_props.items()}
        info = await up.upload(path, meta, size)
        info.setdefault("session_uri", up.session_uri)
        info["md5_local"] = up.md5.hexdigest()
        return info

    # ── server-side copy (never downloads) ───────────────────────────────
    async def copy_file(self, file_id: str, parent: str = "root") -> dict:
        """Copy within Drive. The bytes never leave Google's servers.

        From the reference bot `cdfxscrq/GDrive-Uploader-TG-Bot`, which is the
        one idea in that repo worth keeping — re-uploading to duplicate a file
        wastes bandwidth and the user's daily quota.
        """
        body = {"parents": [parent]} if parent and parent != "root" else {}
        return await self._call("POST", f"{self.API}/files/{file_id}/copy", params={
            "supportsAllDrives": "true", "fields": FIELDS_FILE}, json_body=body)

    async def copy_tree(self, folder_id: str, parent: str = "root",
                        *, max_files: int = 2000, max_depth: int = 12,
                        on_progress=None, should_stop=None) -> dict:
        """Recursive copy of a folder, looping over itself only as deep as allowed.

        Guards against the two ways this goes wrong: a folder that somehow
        contains itself (seen via a `visited` set) and a shared drive large
        enough to run forever (`max_files`).
        """
        root = await self.create_folder(
            (await self.get(folder_id)).get("name", "Copied folder"), parent)
        copied = 0
        seen: set[str] = {root["id"]}

        async def walk(src: str, dst: str, depth: int) -> None:
            nonlocal copied
            if depth > max_depth:
                return
            page = ""
            while True:
                found = await self.list_children(src, page_token=page, page_size=200)
                for entry in found.get("files", []):
                    if should_stop and should_stop():
                        return
                    if copied >= max_files:
                        return
                    if entry.get("mimeType") == FOLDER_MIME:
                        if entry["id"] in seen:
                            continue
                        seen.add(entry["id"])
                        child = await self.create_folder(entry.get("name", "folder"), dst)
                        await walk(entry["id"], child["id"], depth + 1)
                    else:
                        await self.copy_file(entry["id"], dst)
                        copied += 1
                        if on_progress:
                            await on_progress(copied)
                page = found.get("next", "")      # list_children returns "next"
                if not page:
                    return

        await walk(folder_id, root["id"], 0)
        return {"folder_id": root["id"], "files": copied}


def folder_id_from_url(text: str) -> str:
    """Accept a raw id or any Drive/Docs URL and return the folder id ('' if none)."""
    import re
    t = (text or "").strip()
    if not t:
        return ""
    for pat in (r"/folders/([A-Za-z0-9_-]{10,})", r"[?&]id=([A-Za-z0-9_-]{10,})",
                r"/d/([A-Za-z0-9_-]{20,})"):
        m = re.search(pat, t)
        if m:
            return m.group(1)
    if re.fullmatch(r"[A-Za-z0-9_-]{10,}", t):
        return t
    return ""
