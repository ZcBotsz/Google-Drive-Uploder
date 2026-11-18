"""Telegram → local file streaming, with progress and cancellation.

`Bot.download_file()` in aiogram 3 gives you no progress hook and no way to
stop mid-download, which is unacceptable when a user can send 2 GB and then tap
Cancel. So we talk to the session directly:

    file = await bot.get_file(file_id)          # → file_path + file_size
    async for piece in stream_download(bot, file): ...

The session's `stream_content()` is the same call aiogram makes internally, so
this works against both the cloud Bot API and a local Bot API server
(`TELEGRAM_API_URL`), where `file_path` is an absolute path on our disk.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

log = logging.getLogger("driveup.tg.download")

Progress = Optional[Callable[[int, int], Awaitable[None] | None]]
ShouldStop = Optional[Callable[[], bool]]

CHUNK = 256 * 1024


class DownloadError(RuntimeError):
    """Telegram refused or broke the transfer."""


def file_url(bot, file_path: str) -> str:
    """Where the session should read the bytes from."""
    session = bot.session
    api = getattr(session, "api", None)
    if api is not None and hasattr(api, "file_url"):
        try:
            return api.file_url(bot.token, file_path)
        except Exception:                                  # noqa: BLE001
            pass
    base = str(getattr(api, "base", "https://api.telegram.org")).rstrip("/")
    return f"{base}/file/bot{bot.token}/{file_path}"


async def stream_download(
    bot,
    file_path: str,
    *,
    total: int = 0,
    chunk_size: int = CHUNK,
    progress: Progress = None,
    should_stop: ShouldStop = None,
    timeout: int = 3600,
) -> AsyncIterator[bytes]:
    """Yield the file in chunks, reporting progress after each one."""
    done = 0
    session = bot.session
    # An absolute path that we cannot read off disk means the Bot API server is
    # in another container: fetch it from that server's HTTP file endpoint.
    url = (file_server_url(bot, file_path, getattr(bot, "files_work_dir", ""))
           or file_url(bot, file_path))
    async for piece in session.stream_content(
        url,
        timeout=timeout,
        chunk_size=chunk_size,
        raise_for_status=True,
    ):
        if should_stop and should_stop():
            raise DownloadError("cancelled")
        if not piece:
            continue
        done += len(piece)
        if progress:
            out = progress(done, total or done)
            if hasattr(out, "__await__"):
                await out
        yield piece


async def download_to_file(
    bot,
    file_id: str,
    destination: "str | Path",
    *,
    progress: Progress = None,
    should_stop: ShouldStop = None,
    timeout: int = 3600,
) -> dict:
    """Download a Telegram file to `destination` atomically.

    Writes `<destination>.part` first: a crash mid-download can never leave a
    truncated file that looks complete. Returns {path, size, file_path}.
    """
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")

    file = await bot.get_file(file_id)
    file_path = getattr(file, "file_path", "") or ""
    total = int(getattr(file, "file_size", 0) or 0)

    # `file_path` is a bare "documents/file_3.pdf" on the cloud API, but an
    # ABSOLUTE path on a local Bot API server. Feeding an absolute path back
    # into api.file_url() produces ".../file/bot<token>//var/lib/…" — a doubled
    # slash and a 404, so every large file failed even with the right setup.
    # Nothing in this app serves files by absolute path, so dropping the leading
    # slash is always correct and keeps file_url() usable for logging.
    url_path = file_path.lstrip("/")

    # Even better, when the local server put the bytes on a disk we can reach,
    # just read them — no HTTP copy through the same box.
    if os.path.isabs(file_path) and os.path.exists(file_path):
        if progress:
            out = progress(total or os.path.getsize(file_path), total)
            if hasattr(out, "__await__"):
                await out
        try:
            os.replace(file_path, path)      # same filesystem: atomic move
        except OSError:
            shutil.move(file_path, path)     # different mount: copy + unlink
        return {"path": str(path), "size": path.stat().st_size,
                "file_path": url_path}

    size = 0
    try:
        with open(tmp, "wb") as fh:
            async for piece in stream_download(bot, url_path, total=total,
                                               progress=progress,
                                               should_stop=should_stop,
                                               timeout=timeout):
                fh.write(piece)
                size += len(piece)
        if total and size != total:
            raise DownloadError(f"short download: {size} of {total} bytes")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return {"path": str(path), "size": size, "file_path": url_path}


async def stream_file_id(bot, file_id: str, *, progress: Progress = None,
                         should_stop: ShouldStop = None,
                         timeout: int = 3600) -> AsyncIterator[bytes]:
    """Stream a Telegram file straight into the uploader — no disk at all."""
    file = await bot.get_file(file_id)
    file_path = getattr(file, "file_path", "") or ""
    total = int(getattr(file, "file_size", 0) or 0)
    # same absolute-path caveat as download_to_file(): strip the leading slash
    # before building an HTTP url, but keep it for the direct disk read
    url_path = file_path.lstrip("/")
    if os.path.isabs(file_path) and os.path.exists(file_path):
        with open(file_path, "rb") as fh:
            while True:
                if should_stop and should_stop():
                    raise DownloadError("cancelled")
                piece = fh.read(CHUNK)
                if not piece:
                    return
                if progress:
                    out = progress(len(piece), total)
                    if hasattr(out, "__await__"):
                        await out
                yield piece
        return          # fully read from disk — never fall through to HTTP
    async for piece in stream_download(bot, url_path, total=total,
                                       progress=progress, should_stop=should_stop,
                                       timeout=timeout):
        yield piece


def _with_file_port(base: str, api_port: str, file_port: str) -> str:
    """Point a host-injected `host:<api_port>` at the file server's own port.

    `base` may arrive with or without a scheme, and may or may not carry a port.
    `partition`/`rpartition` both mis-split a scheme-less `host:port` (one puts
    the whole string in `scheme`, the other leaves `host` empty), so the split
    is done by hand on the last colon after the host part.
    """
    api_port = (api_port or "").strip()
    file_port = (file_port or "").strip()
    if not (api_port and file_port and api_port != file_port):
        return base
    scheme, sep, rest = base.partition("://")
    if not sep:
        scheme, sep, rest = "", "", base
    if "/" in rest:                        # drop any path before splitting the port
        authority, slash, path = rest.partition("/")
        tail = slash + path
    else:
        authority, tail = rest, ""
    if ":" not in authority:
        return base
    host, _, port = authority.rpartition(":")
    if port != api_port:
        return base
    return f"{scheme}{sep}{host}:{file_port}{tail}"


def file_server_url(bot, local_path: str, work_dir: str) -> str:
    """Where to fetch the bytes when the server's disk is not readable by us.

    On a multi-container host the bot cannot see the Bot API container's
    filesystem, so an absolute `file_path` is unusable. The gramiojs image can
    expose that same directory over HTTP (`FILE_SERVER=1`) at token-less,
    path-based URLs, so all we do is swap the working-dir prefix for the base.

        /var/lib/telegram-bot-api/123/documents/f.pdf
        → http://tg-bot-api.railway.internal:8080/123/documents/f.pdf
    """
    base = (getattr(bot, "files_base_url", "") or "").rstrip("/")
    if not base:
        return ""
    # The API listens on 8081 and the bundled file server on 8080. When a host
    # injects one address for both (`fromService: hostport`), the file URL must
    # still reach 8080 — otherwise every download 404s and looks like "upload
    # does not work".
    base = _with_file_port(base, getattr(bot, "api_port", ""),
                           getattr(bot, "files_port", ""))
    # Callers may already have removed the leading slash from Telegram's
    # absolute local-API path. Normalize both sides before stripping the work
    # directory; otherwise the server is asked for
    # /var/lib/telegram-bot-api/<bot>/documents/... under an nginx root that is
    # already /var/lib/telegram-bot-api, producing a 404.
    rel = (local_path or "").lstrip("/")
    prefix = (work_dir or "").strip("/")
    if prefix and (rel == prefix or rel.startswith(prefix + "/")):
        rel = rel[len(prefix):].lstrip("/")
    return f"{base}/{rel}"


def remote_path_for(bot, file_path: str, work_dir: str) -> str:
    """The path to hand to stream_download() for a getFile result.

    Cloud API and non-local servers → already a relative path.
    Local server we can read            → the absolute path (direct disk read).
    Local server we cannot read         → the file server is configured, so an
                                          absolute path is left untouched and
                                          stream_download() swaps it via
                                          `bot.files_base_url`.
    """
    if not os.path.isabs(file_path):
        return file_path
    if os.path.exists(file_path):
        return file_path
    return file_path            # stream_download() resolves it through the file server
