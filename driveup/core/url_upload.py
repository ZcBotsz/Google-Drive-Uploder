"""Upload a user's link straight into Drive — no Telegram, no full disk copy.

The idea comes from the three reference bots: they all accept a link and push
it to Drive. None of them did it safely, so this module keeps the feature and
replaces the mechanism:

    reference (aryanvikash)   wget via shell=True with the URL interpolated
                              → command injection; pydrive; no size cap
    reference (cdfxscrq)      downloads to disk first, no SSRF check
    reference (All-Url)       SSRF guard, but the payload goes back to Telegram

Here the bytes go  link → HTTP → queue → Drive resumable session, so a 2 GB
link never touches our disk and never passes through Telegram's 50 MB upload
ceiling. The URL is validated on every redirect and the plan's per-file limit
is enforced while reading, not after.
"""
from __future__ import annotations

import asyncio

import aiohttp

from ..config import effective_max_file_mb
from ..db import repo
from ..drive.errors import NetworkError
from ..net import fetch
from ..drive.upload import _fire


async def _pump(http, url: str, queue: asyncio.Queue, info: dict, state,
                limit: int, errors: list, publish=None) -> None:
    """Read the URL and push chunks; always closes the queue, even on failure."""
    done = 0
    try:
        async for piece in fetch.stream_url(http, url, chunk=1 << 20,
                                            max_bytes=limit,
                                            should_stop=state.want_stop, info=info):
            done += len(piece)
            await _fire(state.on_tg, done, info.get("total") or limit)
            if publish:
                publish(state)
            await queue.put(piece)
        await queue.put(None)
    except BaseException as e:                 # noqa: BLE001
        errors.append(e)
        await queue.put(None)


async def _drain(queue: asyncio.Queue):
    while True:
        piece = await queue.get()
        if piece is None:
            return
        yield piece


async def upload_from_url(worker, job: dict, item: dict, state) -> dict:
    """Drive the upload. `worker` gives us the db, the account manager and cfg."""
    tg = item["tg"]
    url = str(tg.get("url") or "")
    plan = await repo.effective_plan(worker.db, job["user_id"])
    plan_mb = effective_max_file_mb(worker.cfg, requested_mb=plan.get("max_file_mb", 2000))
    # the smaller of "what the plan allows" and "what the operator allows"
    cap = min(plan_mb, worker.cfg.url_upload_max_mb or plan_mb)
    limit = cap * 1024 ** 2

    client = await worker.accounts.client(job["user_id"])
    dst = await worker._folder_for(job, item, client)
    info: dict = {}
    errors: list = []
    queue: asyncio.Queue = asyncio.Queue(maxsize=4)

    async def report_drive(done, total):
        state.on_drive(done, total)
        worker.hub.put(state)

    up = client.uploader(chunk=worker.cfg.chunk_bytes,
                         on_progress=report_drive,
                         should_cancel=state.want_stop)

    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None)) as http:
        task = asyncio.create_task(_pump(http, url, queue, info, state, limit, errors, worker.hub.put))
        try:
            # The size only exists in the response headers, so wait for them.
            for _ in range(600):                       # ≤30 s
                if info.get("total") or errors:
                    break
                await asyncio.sleep(0.05)
            if errors:
                raise errors[0]
            total = int(info.get("total") or 0)
            if not total:
                raise NetworkError("that link did not say how big the file is — "
                                   "try a direct download link")
            state.total = total
            tg["size"] = total

            meta = {
                "name": item.get("final_name") or info.get("name") or "download",
                "mimeType": info.get("mime") or "application/octet-stream",
                "appProperties": {
                    "job_id": job["id"],
                    "source_host": fetch.urlparse(info.get("url", "")).hostname or "",
                },
            }
            if dst.get("folder_id") not in (None, "", "root"):
                meta["parents"] = [dst["folder_id"]]

            await up.initiate(meta, total)
            got = await up.upload(_drain(queue), meta, total)
        finally:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):      # noqa: BLE001
                pass

    if errors:
        raise errors[0]
    return {"file_id": got.get("id", ""), "link": got.get("webViewLink", ""),
            "session_uri": "", "committed": got.get("size", total)}


async def _put_piece(queue: asyncio.Queue, piece) -> None:
    """Backpressure-aware put — same contract the worker uses internally."""
    await queue.put(piece)
