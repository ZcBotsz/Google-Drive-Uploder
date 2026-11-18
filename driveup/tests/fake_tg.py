"""A real Telegram Bot API, in-process.

Instead of hand-rolling fake aiogram objects (which drift from reality), the
bot tests run the *actual* aiogram Bot against a fake HTTP session. Every
method is serialised to a dict, recorded, and answered with a JSON payload
that aiogram itself validates into its own types — so the handlers, the
middlewares and the keyboards get exercised exactly as in production.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.types import Chat, Message, Update, User

BOT_USER = {"id": 999, "is_bot": True, "first_name": "DriveUp", "username": "driveup_test_bot"}


class FakeSession(BaseSession):
    """Answers Bot API calls with realistic payloads; records them."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: List[Dict[str, Any]] = []
        self.messages: Dict[int, Dict[str, Any]] = {}
        self._msg_id = 500
        self.files: Dict[str, bytes] = {}
        self.store_paths: Dict[str, str] = {}
        self.path_of_file: Dict[str, str] = {}
        self.edit_count = 0
        self.fail_after: Optional[int] = None        # raise 429 after N calls
        self.retry_after = 1
        self.member_status = "member"                # getChatMember answer

    # ── helpers ───────────────────────────────────────────────────────────
    def add_file(self, name: str, data: bytes, file_id: Optional[str] = None) -> str:
        fid = file_id or f"file{len(self.files) + 1}"
        self.files[fid] = data
        self.store_paths[f"documents/{name}"] = fid
        self.path_of_file[fid] = f"documents/{name}"
        return fid

    def _chat(self, chat_id: int) -> dict:
        return {"id": chat_id, "type": "private", "first_name": "Tester",
                "username": "tester"}

    def _message(self, chat_id: int, text: str, **extra) -> dict:
        self._msg_id += 1
        msg = {"message_id": self._msg_id, "date": int(time.time()),
               "chat": self._chat(chat_id), "from": BOT_USER, "text": text, **extra}
        self.messages[self._msg_id] = msg
        return msg

    TEXT_METHODS = ("SendMessage", "EditMessageText", "EditMessageCaption")

    def texts(self) -> List[str]:
        return [c["params"].get("text", "") or "" for c in self.calls
                if c["method"] in self.TEXT_METHODS]

    def last(self, method: str = "SendMessage") -> Optional[dict]:
        for c in reversed(self.calls):
            if c["method"] == method:
                return c["params"]
        return None

    # ── BaseSession ───────────────────────────────────────────────────────
    async def close(self) -> None:
        return None

    async def stream_content(self, url: str, headers=None, timeout: int = 30,
                             chunk_size: int = 65536, raise_for_status: bool = True,
                             **_kw):
        path = str(url).split("/file/bot", 1)[-1].split("/", 1)[-1]
        fid = self.store_paths.get(path) or path
        data = self.files.get(fid, b"")
        for i in range(0, max(1, len(data)), chunk_size):
            await asyncio.sleep(0)
            yield data[i:i + chunk_size]

    async def make_request(self, bot: Bot, method, timeout: int | None = None) -> Any:
        name = type(method).__name__
        params = {k: v for k, v in method.model_dump(warnings=False).items()
                  if v is not None}
        self.calls.append({"method": name, "params": params})
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            self.fail_after = None
            raise _retry_after(self.retry_after)
        payload = self._answer(name, params)
        return _validate(method.__returning__, payload)

    def _answer(self, name: str, p: dict) -> dict:
        chat_id = p.get("chat_id", 0)
        if name == "GetMe":
            return BOT_USER
        if name == "GetUpdates":
            return []
        if name == "SendMessage":
            msg = self._message(chat_id, p.get("text", ""), reply_markup=p.get("reply_markup"))
            return msg
        if name == "EditMessageText":
            self.edit_count += 1
            mid = p.get("message_id") or 0
            base = self.messages.get(mid) or self._message(chat_id, "")
            base["text"] = p.get("text", "")
            if "reply_markup" in p:
                base["reply_markup"] = p["reply_markup"]
            return base
        if name == "EditMessageReplyMarkup":
            mid = p.get("message_id") or 0
            base = self.messages.get(mid) or self._message(chat_id, "")
            base["reply_markup"] = p.get("reply_markup")
            return base
        if name in ("SendDocument", "SendVideo", "SendAudio", "SendPhoto"):
            return self._message(chat_id, p.get("caption", "") or "")
        if name == "GetFile":
            fid = p.get("file_id", "")
            data = self.files.get(fid, b"")
            path = self.path_of_file.get(fid) or next(
                (k for k, v in self.store_paths.items() if v == fid), f"documents/{fid}")
            return {"file_id": fid, "file_unique_id": "u" + fid,
                    "file_size": len(data), "file_path": path}
        if name == "GetChatMember":
            return {"status": self.member_status,
                    "user": {"id": p.get("user_id", 0), "is_bot": False,
                             "first_name": "T"}}
        if name == "GetChat":
            return self._chat(chat_id)
        if name == "AnswerCallbackQuery":
            return True
        if name == "DeleteMessage":
            return True
        if name == "CopyMessage":
            return {"message_id": self._msg_id + 1}
        if name == "ForwardMessage":
            return self._message(chat_id, "")
        return True


def _validate(ret, payload):
    """aiogram's declared return type may be a bool, a Union, or a model."""
    import types
    import typing
    if ret is bool:
        return bool(payload)
    if ret is None or ret is type(None):
        return None
    if isinstance(ret, types.UnionType) or typing.get_origin(ret) is typing.Union:
        for part in typing.get_args(ret):
            if part is type(None):
                continue
            model = part.__args__[0] if hasattr(part, "__args__") else part
            try:
                return model.model_validate(payload)
            except Exception:                              # noqa: BLE001
                continue
        return payload
    return ret.model_validate(payload)


class _retry_after(Exception):
    """Raised inside the session; converted to aiogram's TelegramRetryAfter."""

    def __init__(self, secs: int) -> None:
        self.retry_after = secs
        super().__init__(f"Too Many Requests: retry after {secs}")


async def make_bot() -> Bot:
    return Bot(token="123456:TESTTOKEN", session=FakeSession())


def user(uid: int = 42, name: str = "Tester") -> User:
    return User(id=uid, is_bot=False, first_name=name, username=name.lower())


def message(text: str, uid: int = 42, chat_id: int = 42, mid: int = 1,
            msg: Optional[Message] = None) -> Update:
    payload = {"message_id": mid, "date": int(time.time()),
               "chat": {"id": chat_id, "type": "private"},
               "from": {"id": uid, "is_bot": False, "first_name": "Tester"},
               "text": text}
    return Update(update_id=mid, message=Message.model_validate(payload))


def callback(data: str, uid: int = 42, chat_id: int = 42, mid: int = 1) -> Update:
    payload = {
        "update_id": mid + 100000,
        "callback_query": {
            "id": f"cb{mid}", "from": {"id": uid, "is_bot": False, "first_name": "Tester"},
            "chat_instance": "1", "data": data,
            "message": {"message_id": mid, "date": int(time.time()),
                        "chat": {"id": chat_id, "type": "private"},
                        "from": BOT_USER, "text": "card"},
        },
    }
    return Update.model_validate(payload)


def document(body: bytes, name: str = "file.bin", uid: int = 42, chat_id: int = 42,
             mid: int = 2, mime: str = "application/octet-stream",
             file_id: str = "DOC1") -> Update:
    """A document message, as Telegram would deliver it."""
    payload = {
        "update_id": mid + 200000,
        "message": {
            "message_id": mid, "date": int(time.time()),
            "chat": {"id": chat_id, "type": "private"},
            "from": {"id": uid, "is_bot": False, "first_name": "Tester"},
            "document": {
                "file_id": file_id, "file_unique_id": f"u{file_id}",
                "file_name": name, "mime_type": mime, "file_size": len(body),
            },
        },
    }
    return Update.model_validate(payload), body
