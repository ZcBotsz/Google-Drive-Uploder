"""Test doubles: a fake Bot (records calls, serves a byte stream) and helpers."""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class FileRef:
    file_id: str
    file_unique_id: str
    file_path: str
    file_size: int


class Msg:
    def __init__(self, message_id: int, chat_id: int, text: str = "") -> None:
        self.message_id = message_id
        self.chat = type("Chat", (), {"id": chat_id})()
        self.text = text


class FakeSession:
    """Mimics AiohttpSession.stream_content for `/file/bot…/<path>`."""

    def __init__(self, store: Dict[str, bytes], chunk: int = 256 * 1024) -> None:
        self.store = store
        self.chunk = chunk
        self.closed = False
        self.bytes_served = 0

    async def stream_content(self, url: str, headers=None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True,
                             **_kw):
        name = url.rsplit("/", 1)[-1]
        data = self.store.get(name, b"")
        for i in range(0, len(data), chunk_size or self.chunk):
            piece = data[i:i + chunk_size]
            self.bytes_served += len(piece)
            await asyncio.sleep(0)             # yield, so cancellation is possible
            yield piece

    async def close(self) -> None:
        self.closed = True


@dataclass
class FakeBot:
    """The subset of aiogram's Bot that DriveUp actually calls."""
    token: str = "123456:TEST"
    store: Dict[str, bytes] = field(default_factory=dict)
    files: Dict[str, FileRef] = field(default_factory=dict)
    sent: List[dict] = field(default_factory=list)
    edits: List[dict] = field(default_factory=list)
    copied: List[dict] = field(default_factory=list)
    _mid: int = 1000
    me_username: str = "driveup_test_bot"
    download_fail: bool = False
    session: Optional[FakeSession] = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = FakeSession(self.store)

    # aiogram API ────────────────────────────────────────────────────────
    async def get_me(self):
        return type("Me", (), {"username": self.me_username, "id": 1, "is_bot": True})()

    async def send_message(self, chat_id: int, text: str, **kw):
        self._mid += 1
        rec = {"chat_id": chat_id, "text": text, "message_id": self._mid, **kw}
        self.sent.append(rec)
        return Msg(self._mid, chat_id, text)

    async def copy_message(self, chat_id: int, from_chat_id: int, message_id: int, **kw):
        rec = {"chat_id": chat_id, "from_chat_id": from_chat_id,
               "message_id": message_id, **kw}
        self.copied.append(rec)
        return Msg(message_id + 100000, chat_id, "copied")

    async def edit_message_text(self, chat_id: int = 0, message_id: int = 0,
                                text: str = "", **kw):
        self.edits.append({"chat_id": chat_id, "message_id": message_id, "text": text, **kw})
        return Msg(message_id, chat_id, text)

    async def get_file(self, file_id: str):
        ref = self.files.get(file_id)
        if not ref or self.download_fail:
            raise RuntimeError("file ref expired")
        return ref

    async def download_file(self, file_path: str, destination=None, timeout: int = 30,
                            progress=None, cancel=None, **kw):
        if self.download_fail:
            raise RuntimeError("download failed")
        data = self.store.get(str(file_path).rsplit("/", 1)[-1], b"")
        if destination is not None:
            from pathlib import Path
            Path(str(destination)).write_bytes(data)
        if progress:
            import inspect
            out = progress(len(data), len(data))
            if inspect.isawaitable(out):
                await out
        return len(data)

    async def forward_message(self, chat_id: int, from_chat_id: int, message_id: int):
        return Msg(message_id + 5000, chat_id)

    async def delete_message(self, chat_id: int, message_id: int):
        return True

    async def get_chat_member(self, chat_id, user_id):
        return type("Member", (), {"status": "member"})()

    async def close(self) -> None:
        return None

    # helpers ────────────────────────────────────────────────────────────
    def add_file(self, name: str, data: bytes, file_id: Optional[str] = None,
                 unique: Optional[str] = None, mime: str = "application/octet-stream"):
        fid = file_id or f"file_{len(self.files) + 1}"
        ref = FileRef(fid, unique or f"u_{fid}", f"documents/{name}", len(data))
        self.files[fid] = ref
        self.store[ref.file_path.rsplit("/", 1)[-1]] = data
        return ref

    def texts(self) -> List[str]:
        return [m["text"] for m in self.sent] + [e["text"] for e in self.edits]


class FakeChat:
    def __init__(self, chat_id: int = 42) -> None:
        self.id = chat_id


class FakeMessage:
    def __init__(self, chat_id: int = 42, message_id: int = 1, text: str = "") -> None:
        self.chat = FakeChat(chat_id)
        self.message_id = message_id
        self.text = text
        self.caption = ""
        self.document = None
        self.video = None
        self.audio = None
        self.photo = None
        self.voice = None
        self.video_note = None
        self.animation = None
        self.media_group_id = None
        self.forward_origin = None

    async def answer(self, text: str, **kw):
        return {"text": text, **kw}


def make_media_message(kind: str, chat_id: int = 42, message_id: int = 1,
                       name: str = "x.bin", file_id: str = "f1", size: int = 100,
                       mime: str = "application/octet-stream", caption: str = ""):
    """A message shaped like aiogram's, for handler-level tests."""
    msg = FakeMessage(chat_id, message_id)
    msg.caption = caption
    media = type(f"Media_{kind}", (), {})()
    media.file_id = file_id
    media.file_unique_id = f"u_{file_id}"
    media.file_size = size
    media.mime_type = mime
    if kind == "document":
        media.file_name = name
    if kind == "photo":
        setattr(msg, "photo", [media])
    elif kind == "animation":
        media.file_name = name
        setattr(msg, kind, media)
    else:
        setattr(msg, kind, media)
    return msg
