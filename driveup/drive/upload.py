"""Resumable upload engine (B3.4), implemented to the letter.

Initiate → POST .../files?uploadType=resumable → remember `Location`.
Send     → PUT that URI with `Content-Range: bytes a-b/total`, 256 KiB multiples.
308      → continue from the `Range` header.
200/201  → done.
5xx/net  → `PUT Content-Range: bytes */total` to ask what Google committed, resume.
404      → session gone → start a new session (caller re-initiates).
403      → rate/quota/permission classified by drive/errors.py.

The engine is transport-agnostic: it takes an `AsyncIterator[bytes]` (or a file
path) and reports progress through a callback. That is what lets the same code
upload from a live Telegram stream and from a temp file on disk.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import random
import re
from pathlib import Path
import inspect
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Union

import aiohttp

from .errors import (DriveError, NetworkError, RateLimited, RetryableDriveError,
                     SessionGone, classify)

MIN_CHUNK = 256 * 1024
MAX_ATTEMPTS = 6
CHUNK_ALIGN = 256 * 1024

Progress = Callable[..., Any]      # (bytes_done, total) — sync or async
Source = Union[AsyncIterator[bytes], Path, str]


async def _fire(cb, *args) -> None:
    """Call a progress callback that may be sync or async."""
    if cb is None:
        return
    out = cb(*args)
    if inspect.isawaitable(out):
        await out


def align_chunk(size: int) -> int:
    """Drive wants a 256 KiB multiple (the final chunk may be smaller)."""
    return max(MIN_CHUNK, size - (size % CHUNK_ALIGN))


def parse_range(header: str) -> Optional[int]:
    """`bytes=0-1048575` → 1048576 (bytes committed so far)."""
    if not header:
        return None
    m = re.search(r"bytes=(\d+)-(\d+)", header)
    if not m:
        m = re.search(r"bytes=(\d+)", header)
        if not m:
            return None
        return int(m.group(1))
    return int(m.group(2)) + 1


class ResumableUploader:
    def __init__(self, http: aiohttp.ClientSession, token: str, *, chunk: int = 8 * 1024 * 1024,
                 max_attempts: int = MAX_ATTEMPTS, backoff_base: float = 1.0,
                 on_progress: Optional[Progress] = None,
                 should_cancel: Optional[Callable[[], bool]] = None,
                 sleep=asyncio.sleep, rng: Optional[random.Random] = None,
                 upload_url: str = "https://www.googleapis.com/upload/drive/v3/files") -> None:
        self.http = http
        self.upload_url = upload_url
        self.token = token
        self.chunk = align_chunk(chunk)
        self.max_attempts = max_attempts
        self.backoff_base = backoff_base
        self.on_progress = on_progress
        self.should_cancel = should_cancel or (lambda: False)
        self._sleep = sleep
        self._rng = rng or random.Random(4)
        self.session_uri: str = ""
        self.committed: int = 0
        self.total: int = 0
        self.md5 = hashlib.md5()

    # ── headers ──────────────────────────────────────────────────────────
    def _auth(self) -> dict:
        return {"Authorization": f"Bearer {self.token}"}

    async def initiate(self, metadata: dict, total: int) -> str:
        """Create the resumable session; returns the session URI."""
        self.total = max(0, int(total))
        headers = {
            **self._auth(),
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Type": metadata.get("mimeType", "application/octet-stream"),
            "X-Upload-Content-Length": str(self.total),
        }
        params = {"uploadType": "resumable", "supportsAllDrives": "true", "fields": "id,name"}
        async with self.http.post(self.upload_url, params=params, json=metadata, headers=headers,
                                  timeout=aiohttp.ClientTimeout(total=60)) as r:
            body = await r.text()
            if r.status in (200, 201):
                uri = r.headers.get("Location") or r.headers.get("location")
                if not uri:
                    raise DriveError("Google did not return a session URI", status=r.status)
                self.session_uri = uri
                self.committed = 0
                return uri
            raise classify(r.status, body)

    # ── one chunk ────────────────────────────────────────────────────────
    async def put_chunk(self, data: bytes, start: int, total: int) -> tuple[int, dict]:
        """PUT one chunk. Returns (status, parsed_json). Handles 308 bookkeeping."""
        end = start + len(data) - 1
        headers = {
            **self._auth(),
            "Content-Length": str(len(data)),
            "Content-Range": f"bytes {start}-{end}/{total}",
        }
        async with self.http.put(self.session_uri, data=data, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=600)) as r:
            body = await r.text()
            if r.status == 308:
                rng = r.headers.get("Range") or r.headers.get("range")
                committed = parse_range(rng)
                if committed is not None:
                    self.committed = committed
                else:
                    self.committed = end + 1
                return 308, {"committed": self.committed}
            if r.status in (200, 201):
                self.committed = total
                import json
                try:
                    return r.status, json.loads(body or "{}")
                except ValueError:
                    return r.status, {}
            if r.status == 404:
                raise SessionGone("resumable session expired", status=404)
            if 500 <= r.status < 600:
                raise classify(r.status, body)
            raise classify(r.status, body)

    async def query_status(self) -> Optional[int]:
        """`PUT Content-Range: bytes */total` → how many bytes Google has."""
        headers = {**self._auth(), "Content-Range": f"bytes */{self.total}",
                   "Content-Length": "0"}
        async with self.http.put(self.session_uri, headers=headers,
                                 timeout=aiohttp.ClientTimeout(total=60)) as r:
            if r.status == 308:
                committed = parse_range(r.headers.get("Range") or "")
                return committed if committed is not None else 0
            if r.status in (200, 201):
                return self.total
            if r.status == 404:
                raise SessionGone("resumable session expired", status=404)
            body = await r.text()
            if 500 <= r.status < 600:
                raise classify(r.status, body)
            raise classify(r.status, body)

    async def cancel_session(self) -> None:
        """Best-effort: tell Drive to drop the half-finished upload."""
        if not self.session_uri:
            return
        try:
            async with self.http.delete(self.session_uri, headers=self._auth(),
                                        timeout=aiohttp.ClientTimeout(total=20)) as r:
                await r.read()
        except Exception:
            pass

    # ── backoff ──────────────────────────────────────────────────────────
    def backoff(self, attempt: int, retry_after: float = 0.0) -> float:
        if retry_after:
            return min(retry_after, 300.0)
        return min(300.0, self.backoff_base * (2 ** attempt)) * (1 + self._rng.random() * 0.3)

    # ── the whole upload ─────────────────────────────────────────────────
    async def upload(self, source: Source, metadata: dict, total: int) -> dict:
        """Upload `source` (stream or file). Returns the final Drive file JSON."""
        if not self.session_uri:
            await self.initiate(metadata, total)
        elif self.committed > 0:
            # Resuming a real session: trust Google's own count over ours.
            self.total = total
            try:
                committed = await self.query_status()
                self.committed = committed if committed is not None else self.committed
                if total and self.committed >= total:
                    return {"id": "", "status": "already-complete"}
            except SessionGone:
                self.session_uri = ""
                self.committed = 0
                await self.initiate(metadata, total)
        else:
            # A session created but nothing pushed: start from zero, no probe.
            self.total = total

        if isinstance(source, (str, Path)):
            return await self._upload_file(Path(source), metadata, total)
        return await self._upload_stream(source, metadata, total)

    async def _upload_stream(self, stream: AsyncIterator[bytes], metadata: dict,
                             total: int) -> dict:
        buf = bytearray()
        # Resume: skip whatever Google already committed. The caller hands us a
        # stream of the whole file (Telegram cannot resume a download), so the
        # skip is local and costs no bandwidth.
        pos = self.committed or 0
        to_skip = pos          # bytes Google already has, still to drop locally
        attempts = 0
        it = stream.__aiter__()
        exhausted = False

        while True:
            if self.should_cancel():
                await self.cancel_session()
                raise DriveError("cancelled")

            # fill one chunk (or whatever is left)
            target = min(self.chunk, max(0, total - pos)) if total else self.chunk
            while not exhausted and len(buf) < target:
                try:
                    piece = await it.__anext__()
                except StopAsyncIteration:
                    exhausted = True
                    break
                if piece:
                    if to_skip:
                        drop = min(to_skip, len(piece))
                        piece = piece[drop:]
                        to_skip -= drop
                    if piece:
                        buf.extend(piece)

            if not buf and exhausted:
                break

            data = bytes(buf[:target or len(buf)])
            del buf[:len(data)]

            try:
                status, info = await self.put_chunk(data, pos, total or pos + len(data))
                attempts = 0
                if status in (200, 201):
                    self.md5.update(data)
                    pos += len(data)
                    if self.on_progress:
                        await _fire(self.on_progress, min(pos, total or pos), total or pos)
                    info.setdefault("id", "")
                    info["size"] = self.total or pos
                    return info
                # 308 → Google took the chunk
                self.md5.update(data)
                pos = info.get("committed", pos + len(data))
                if self.on_progress:
                    await _fire(self.on_progress, min(pos, total or pos), total or pos)
                effective_total = total or pos
                if pos >= effective_total:
                    # the final chunk is committed; a status probe returns the
                    # finished file JSON (200) or the offset again (308)
                    final = await self.query_status()
                    if final is not None and final >= effective_total:
                        return {"id": "", "size": effective_total,
                                "status": "complete-after-308"}
                    if not total:
                        return {"id": "", "size": pos, "status": "complete-after-308"}
            except SessionGone:
                # Drive dropped the session. A live stream cannot be rewound, so
                # the caller must re-initiate from a fresh file read.
                raise
            except RetryableDriveError as e:
                attempts += 1
                if attempts > self.max_attempts:
                    raise
                await self._sleep(self.backoff(attempts, e.retry_after))
                # Ask Google what it actually committed, then resend only the
                # part it is missing. (A 308 status probe answers with the
                # committed byte count; anything else means "unknown".)
                committed = None
                try:
                    committed = await self.query_status()
                except SessionGone:
                    raise
                except DriveError:
                    committed = None
                if committed is not None and committed > pos:
                    landed = min(len(data), committed - pos)
                    pos += landed
                    data = data[landed:]
                elif committed is not None and committed < pos:
                    raise SessionGone("committed offset went backwards", status=404)
                if data:
                    buf = bytearray(data) + bytes(buf)     # resend from `pos`
                continue
        raise DriveError("upload stream ended before the file was complete")

    async def _upload_file(self, path: Path, metadata: dict, total: int) -> dict:
        size = path.stat().st_size
        total = total or size
        pos = self.committed or 0
        attempts = 0
        with path.open("rb") as f:
            if pos:
                f.seek(pos)
            while pos < total:
                if self.should_cancel():
                    await self.cancel_session()
                    raise DriveError("cancelled")
                data = f.read(min(self.chunk, total - pos))
                if not data:
                    break
                try:
                    status, info = await self.put_chunk(data, pos, total)
                    attempts = 0
                    if status in (200, 201):
                        pos += len(data)
                        if self.on_progress:
                            await _fire(self.on_progress, pos, total)
                        info["size"] = total
                        return info
                    pos = info.get("committed", pos + len(data))
                    f.seek(pos)
                    if self.on_progress:
                        await _fire(self.on_progress, pos, total)
                    if pos >= total:
                        final = await self.query_status()
                        if final is None or final >= total:
                            return {"id": "", "size": total,
                                    "status": "complete-after-308"}
                except RetryableDriveError as e:
                    attempts += 1
                    if attempts > self.max_attempts:
                        raise
                    await self._sleep(self.backoff(attempts, e.retry_after))
                    try:
                        got = await self.query_status()
                    except SessionGone:
                        raise
                    except RetryableDriveError:
                        got = pos
                    if got is not None:
                        pos = min(got, total)
                        f.seek(pos)
                    continue
        raise DriveError("file ended before the upload completed")


async def md5_of_stream(stream: AsyncIterator[bytes]) -> str:
    h = hashlib.md5()
    async for chunk in stream:
        h.update(chunk)
    return h.hexdigest()
