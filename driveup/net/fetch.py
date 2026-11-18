"""Fetch a user-supplied URL without letting the user reach our own network.

A bot that downloads whatever link a stranger sends is a proxy into the server
it runs on. Without a check, `http://169.254.169.254/latest/meta-data/` (cloud
metadata) or `http://127.0.0.1:8080/admin` is fetched *by the bot* and the
response can come back out of it. That is server-side request forgery.

Rules enforced here:
  · only http and https
  · the hostname must resolve to a public address — no loopback, private,
    link-local, reserved, multicast or unspecified ranges
  · every redirect is re-validated before it is followed, because a public URL
    can 302 to an internal one

Known limit: DNS can change between our lookup and the actual connection
(rebinding). This check closes the practical hole; it is not a kernel-level
sandbox. Recorded here rather than pretended away.
"""
from __future__ import annotations

import ipaddress
import mimetypes
import socket
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

MAX_REDIRECTS = 5
REDIRECT_STATUSES = {301, 302, 303, 307, 308}


class UnsafeURL(ValueError):
    """The URL is not something we will fetch on a user's behalf."""


def _addresses_for(host: str) -> list:
    try:
        return [ipaddress.ip_address(host)]
    except ValueError:
        pass
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError as e:
        raise UnsafeURL(f"could not resolve {host}") from e
    return [ipaddress.ip_address(info[4][0]) for info in infos]


def assert_public_url(url: str) -> str:
    """Return the URL, or raise UnsafeURL. Cheap enough to call twice."""
    raw = (url or "").strip()
    if not raw:
        raise UnsafeURL("empty URL")
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURL(f"only http/https links are supported (got {parsed.scheme or 'none'})")
    host = parsed.hostname
    if not host:
        raise UnsafeURL("that link has no host")
    for ip in _addresses_for(host):
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise UnsafeURL("that link points inside a private network — refused")
    return raw


def filename_from_url(url: str, fallback: str = "download") -> str:
    """Best-effort name from the path, always a safe basename.

    A user can type `https://host/file.zip` or `uri | my name.mkv`, so the name
    arrives from an untrusted source and must never contain a path separator.
    """
    name = Path(unquote(urlparse(url).path)).name
    return safe_filename(name or fallback)


def safe_filename(name: str, fallback: str = "download") -> str:
    base = Path((name or "").replace("\\", "/")).name.strip()
    if base in ("", ".", ".."):
        base = fallback
    # keep it short enough for Drive and for the filesystem
    return base[:180]


def guess_extension(content_type: str) -> str:
    raw = (content_type or "").split(";")[0].strip()
    ext = mimetypes.guess_extension(raw) if raw else None
    return (ext or "").lstrip(".")


def looks_like_url(text: str) -> bool:
    t = (text or "").strip().lower()
    return t.startswith(("http://", "https://"))


def parse_url_line(text: str) -> tuple[str, str]:
    """"url" or "url | name" or "url * name" → (url, name).

    Two separators because people type a pipe when they mean a star; matching
    both costs nothing and saves a confusing failure.
    """
    raw = (text or "").strip()
    for sep in (" | ", " * ", "|", "*"):
        if sep in raw:
            head, tail = raw.split(sep, 1)
            head, tail = head.strip(), tail.strip()
            if looks_like_url(head):
                return head, tail
    return raw, ""


class FetchTooLarge(Exception):
    """The remote file is bigger than this job or deployment will accept."""


async def open_url(session, url: str):
    """GET a user URL, returning (response, final_url) after safe redirects.

    Each `Location` is validated before it is followed, so a public URL that
    redirects to 169.254.169.254 is still refused.
    """
    current = assert_public_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        resp = await session.get(current, allow_redirects=False)
        if resp.status in REDIRECT_STATUSES and resp.headers.get("Location"):
            target = urljoin(current, resp.headers["Location"])
            resp.release()
            current = assert_public_url(target)      # re-validate every hop
            continue
        return resp, current
    raise UnsafeURL("too many redirects")


async def stream_url(session, url: str, *, chunk: int = 1 << 20,
                     max_bytes: int = 0, should_stop=None, info: dict | None = None):
    """Yield the body in chunks, filling `info` with what the response told us.

    `info` gets: url (final), name, mime, total (from Content-Length, 0 if the
    server will not say). Raises FetchTooLarge before reading a byte when the
    advertised size is already over the limit.
    """
    info = info if info is not None else {}
    resp, final = await open_url(session, url)
    try:
        if resp.status >= 400:
            raise UnsafeURL(f"the server answered {resp.status}")
        ctype = resp.headers.get("Content-Type", "")
        total = int(resp.headers.get("Content-Length", "0") or "0")
        ext = guess_extension(ctype)
        base = filename_from_url(final)
        if ext and not base.lower().endswith("." + ext.lower()):
            base = f"{base}.{ext}"
        info.update(url=final, name=base, mime=ctype.split(";")[0].strip(), total=total)
        if max_bytes and total and total > max_bytes:
            raise FetchTooLarge(f"that file is {total} bytes, limit is {max_bytes}")

        got = 0
        async for piece in resp.content.iter_chunked(chunk):
            if should_stop and should_stop():
                return
            got += len(piece)
            if max_bytes and got > max_bytes:
                raise FetchTooLarge(f"download passed the {max_bytes}-byte limit")
            yield piece
        info["total"] = info.get("total") or got
    finally:
        resp.release()
