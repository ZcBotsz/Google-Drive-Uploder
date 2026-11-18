"""Rate-limited Telegram edits (spec B4 / zip's `tg_edit_safe`, fixed).

Rules enforced here:
  * at most one edit per chat per `min_gap` (default 2.5 s)
  * only when progress moved ≥ `min_pct` (default 1 %) or the state changed
  * FloodWait → sleep `value + 1`, retry once, never crash the upload
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

log = logging.getLogger("driveup.ratelimit")


class FloodGate:
    def __init__(self, min_gap: float = 2.5, min_pct: float = 1.0) -> None:
        self.min_gap = min_gap
        self.min_pct = min_pct
        self._last: dict[int, float] = {}
        self._last_pct: dict[int, float] = {}
        self.edits = 0
        self.skipped = 0
        self.floodwaits = 0

    def should_send(self, chat_id: int, pct: float = 0.0, force: bool = False) -> bool:
        now = time.monotonic()
        last = self._last.get(chat_id, 0.0)
        if not force:
            if now - last < self.min_gap:
                self.skipped += 1
                return False
            if abs(pct - self._last_pct.get(chat_id, -100.0)) < self.min_pct and pct < 100:
                self.skipped += 1
                return False
        self._last[chat_id] = now
        self._last_pct[chat_id] = pct
        self.edits += 1
        return True

    def mark(self, chat_id: int, pct: float = 0.0) -> None:
        now = time.monotonic()
        self._last[chat_id] = now
        self._last_pct[chat_id] = pct


async def safe_edit(bot, chat_id: int, message_id: int, text: str,
                    reply_markup=None, gate: Optional[FloodGate] = None,
                    pct: float = 0.0, force: bool = False) -> bool:
    """Edit a message, honouring the gate and FloodWait. Returns True if edited."""
    if gate is not None and not gate.should_send(chat_id, pct, force):
        return False
    for attempt in (1, 2):
        try:
            kw = {"text": text, "chat_id": chat_id, "message_id": message_id,
                  "parse_mode": "HTML", "disable_web_page_preview": True}
            if reply_markup is not None:
                kw["reply_markup"] = reply_markup
            from aiogram.exceptions import TelegramBadRequest
            try:
                await bot.edit_message_text(**kw)
            except TelegramBadRequest as e:
                msg = str(e).lower()
                if "message is not modified" in msg:
                    return False
                if "no text in the message" in msg or "message can't be edited" in msg:
                    return False
                raise
            return True
        except Exception as e:                                  # noqa: BLE001
            wait = flood_wait_seconds(e)
            if wait and attempt == 1:
                if gate is not None:
                    gate.floodwaits += 1
                log.info("FloodWait %ss on chat %s — sleeping", wait, chat_id)
                await asyncio.sleep(wait + 1)
                continue
            log.debug("edit failed: %s", e)
            return False
    return False


def flood_wait_seconds(exc: Exception) -> float:
    """Read `retry_after` off any Telegram flood exception (aiogram 3 shapes)."""
    for attr in ("retry_after", "value"):
        v = getattr(exc, attr, None)
        if isinstance(v, (int, float)) and v:
            return float(v)
    secs = getattr(getattr(exc, "value", None), "seconds", None)
    if isinstance(secs, (int, float)) and secs:
        return float(secs)
    name = type(exc).__name__
    if "Flood" in name or "Retry" in name:
        return 5.0
    return 0.0
