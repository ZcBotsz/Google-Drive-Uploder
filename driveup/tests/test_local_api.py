"""A self-hosted Bot API server hands back an ABSOLUTE file path.

Feeding that path into api.file_url() yields
    http://host/file/bot<token>//var/lib/telegram-bot-api/…   ← doubled slash, 404

so every large file failed *even with the setup done correctly*. These tests pin
both halves of the fix: the URL is normalised, and a readable local file is
copied straight from disk instead of being fetched over HTTP.
"""
from __future__ import annotations

import os

import pytest

from driveup.tg.download import download_to_file, file_url, stream_file_id


class _Session:
    def __init__(self, base):
        from aiogram.client.telegram import TelegramAPIServer
        self.api = TelegramAPIServer.from_base(base)


class _Bot:
    def __init__(self, base, token="123:ABC"):
        self.token = token
        self.session = _Session(base)


class _File:
    def __init__(self, file_path, size):
        self.file_path = file_path
        self.file_size = size


class _BotWithFile(_Bot):
    def __init__(self, base, file_path, size):
        super().__init__(base)
        self._f = _File(file_path, size)

    async def get_file(self, file_id):
        return self._f


def test_local_server_url_never_has_a_double_slash():
    """The pathological case the old code produced — kept as a regression."""
    from aiogram.client.telegram import TelegramAPIServer
    bot = _Bot("http://127.0.0.1:8081")
    bad = "/var/lib/telegram-bot-api/123:ABC/documents/f.pdf"
    naive = bot.session.api.file_url(bot.token, bad)
    assert "//var" in naive, "if aiogram ever fixes this, revisit the workaround"
    # our normalised form
    fixed = file_url(bot, bad.lstrip("/"))
    assert "//var" not in fixed
    assert fixed.startswith("http://127.0.0.1:8081/file/bot")


async def test_local_file_is_read_from_disk_not_fetched(tmp_path):
    """--local mode leaves the bytes on disk; we must copy, not HTTP them."""
    src = tmp_path / "tg_docs" / "big.bin"
    src.parent.mkdir(parents=True)
    src.write_bytes(b"x" * 40_000)

    bot = _BotWithFile("http://127.0.0.1:8081", str(src), 40_000)
    out = await download_to_file(bot, "fid", tmp_path / "out" / "big.bin")

    assert out["size"] == 40_000
    assert (tmp_path / "out" / "big.bin").read_bytes() == b"x" * 40_000
    assert not os.path.exists(src), "the Telegram-side copy should be consumed"


async def test_stream_file_id_reads_local_file_and_stops(tmp_path):
    src = tmp_path / "f.bin"
    src.write_bytes(b"y" * 5000)
    bot = _BotWithFile("http://127.0.0.1:8081", str(src), 5000)
    chunks = []
    async for piece in stream_file_id(bot, "fid"):
        chunks.append(piece)
    assert b"".join(chunks) == b"y" * 5000


# ── two containers: the bot cannot see the API server's disk ───────────────
def test_file_server_url_swaps_the_work_dir_prefix():
    from driveup.tg.download import file_server_url
    bot = _Bot("http://tg-bot-api.railway.internal:8081")
    bot.files_base_url = "http://tg-bot-api.railway.internal:8080"
    bot.files_work_dir = "/var/lib/telegram-bot-api"
    got = file_server_url(bot, "/var/lib/telegram-bot-api/123/documents/f.pdf",
                          bot.files_work_dir)
    assert got == "http://tg-bot-api.railway.internal:8080/123/documents/f.pdf"


def test_no_file_server_configured_means_no_rewrite():
    from driveup.tg.download import file_server_url
    bot = _Bot("http://127.0.0.1:8081")
    bot.files_base_url = ""
    assert file_server_url(bot, "/var/lib/telegram-bot-api/1/documents/f.pdf",
                           "/var/lib/telegram-bot-api") == ""


async def test_stream_download_uses_the_file_server_when_disk_is_invisible(tmp_path):
    """An unreadable absolute path must be fetched over HTTP, not treated as
    a disk file — this is the Railway two-container case."""
    from driveup.tg.download import stream_download

    captured = {}

    class _Session:
        async def stream_content(self, url, **kw):
            captured["url"] = url
            yield b"abc"

    bot = _Bot("http://tg-bot-api.railway.internal:8081")
    bot.session = _Session()
    bot.files_base_url = "http://tg-bot-api.railway.internal:8080"
    bot.files_work_dir = "/var/lib/telegram-bot-api"

    chunks = []
    async for c in stream_download(bot, "/var/lib/telegram-bot-api/9/documents/x.bin"):
        chunks.append(c)

    assert b"".join(chunks) == b"abc"
    assert captured["url"] == "http://tg-bot-api.railway.internal:8080/9/documents/x.bin"


# ══════════════════════════════════════════════════════════════════════════
# Render injects a bare `host:port` (`fromService: hostport`). aiogram accepts
# it silently and then every API call fails — "the bot does not work". These
# tests pin the two places that turn that shape into a working URL.
# ══════════════════════════════════════════════════════════════════════════
@pytest.mark.parametrize("raw,expected", [
    ("driveup-tg-api-abc123:8081", "http://driveup-tg-api-abc123:8081"),
    ("http://h:8081", "http://h:8081"),
    ("https://api.telegram.org", "https://api.telegram.org"),
    ("tg.example.com", "https://tg.example.com"),
    ("", "https://api.telegram.org"),
])
def test_api_base_always_has_a_scheme(monkeypatch, raw, expected):
    from driveup.config import Settings
    monkeypatch.setenv("TELEGRAM_API_URL", raw)
    assert Settings().api_base == expected


@pytest.mark.parametrize("base,expected", [
    ("driveup-tg-api-abc123:8081", "driveup-tg-api-abc123:8080"),
    ("http://h:8081", "http://h:8080"),
    ("http://h:8080", "http://h:8080"),          # already right, untouched
    ("https://api.telegram.org", "https://api.telegram.org"),
    ("h:9000", "h:9000"),                        # not the API port, untouched
    ("http://h:8081/base", "http://h:8080/base"),
])
def test_file_server_moves_to_its_own_port(base, expected):
    """The API listens on 8081 and the file server on 8080; `hostport` gives one."""
    from driveup.tg.download import _with_file_port
    assert _with_file_port(base, "8081", "8080") == expected


def test_hostport_value_becomes_a_working_file_url():
    from types import SimpleNamespace
    from driveup.tg.download import file_server_url
    bot = SimpleNamespace(files_base_url="driveup-tg-api-abc123:8081",
                          files_work_dir="/var/lib/telegram-bot-api",
                          api_port="8081", files_port="8080")
    url = file_server_url(bot, "/var/lib/telegram-bot-api/99/documents/a.mp4",
                          "/var/lib/telegram-bot-api")
    assert url == "driveup-tg-api-abc123:8080/99/documents/a.mp4"
