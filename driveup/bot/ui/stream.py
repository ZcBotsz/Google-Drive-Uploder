"""Typewriter `stream_reply` (async version of the zip's function).

Sends "…", then reveals the text line by line by editing the same message.
Delay is per line, so long reports stay under Telegram's rate limits.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

log = logging.getLogger("driveup.stream")


async def stream_reply(bot, chat_id: int, text: str, *, delay: float = 0.12,
                       message_id: Optional[int] = None, reply_markup=None,
                       max_lines: int = 40) -> Optional[int]:
    """Reveal `text` progressively. Returns the message id (or None on failure)."""
    lines = text.splitlines()
    if len(lines) == 1:
        delay = 0.0
    try:
        if message_id is None:
            msg = await bot.send_message(chat_id, "…", parse_mode="HTML")
            message_id = msg.message_id
        shown: list[str] = []
        for i, line in enumerate(lines):
            shown.append(line)
            if i < len(lines) - 1:
                try:
                    await bot.edit_message_text(chat_id=chat_id, message_id=message_id,
                                                text="\n".join(shown) + " ▌",
                                                parse_mode="HTML",
                                                disable_web_page_preview=True)
                except Exception:                       # noqa: BLE001
                    pass
                if delay:
                    await asyncio.sleep(delay)
            if i >= max_lines:
                break
        await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                    parse_mode="HTML", disable_web_page_preview=True,
                                    reply_markup=reply_markup)
        return message_id
    except Exception as e:                               # noqa: BLE001
        log.debug("stream_reply fell back to a plain send: %s", e)
        try:
            if message_id:
                await bot.edit_message_text(chat_id=chat_id, message_id=message_id, text=text,
                                            parse_mode="HTML", reply_markup=reply_markup)
                return message_id
            msg = await bot.send_message(chat_id, text, parse_mode="HTML",
                                         reply_markup=reply_markup,
                                         disable_web_page_preview=True)
            return msg.message_id
        except Exception:                                # noqa: BLE001
            return None


async def typewriter_frames(text: str, frames: int = 6) -> list[str]:
    """Frames a test can assert without sending anything to Telegram."""
    out = []
    chars = list(text)
    for n in range(1, frames + 1):
        cut = max(1, int(len(chars) * n / frames))
        out.append("".join(chars[:cut]))
    return out
