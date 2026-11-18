"""Styled inline buttons — colour + animated icon, degradation-safe.

Telegram's Bot API accepts three extra fields on an inline button that most bots
never send:

  * ``style`` — ``primary`` (blue) · ``success`` (green) · ``danger`` (red)
  * ``icon_custom_emoji_id`` — an animated emoji drawn on the button itself

Both are optional on Telegram's side, but an *unknown* style string is rejected
outright, and older API layers reject the field entirely. So every button here is
built through one factory that:

  1. only ever emits the three valid style names, and
  2. retries the button **without** the newer fields if the library or the
     server refuses it.

That means a keyboard can never fail to send because of a cosmetic upgrade —
the worst case is a plain button.
"""
from __future__ import annotations

from typing import Optional

from aiogram.types import InlineKeyboardButton as B

from . import emoji_ids

VALID_STYLES = ("primary", "success", "danger")

#: Semantic → (label prefix already contains the emoji, so keep icon minimal)
_DEFAULT_ICON = {
    "primary": "🌐",
    "success": "✅",
    "danger": "❌",
}


def styled(text: str, *, style: Optional[str] = None, icon: Optional[str] = None,
           **kwargs) -> B:
    """Build a button, upgrading it to a coloured/iconed one only if valid.

    Extra keyword arguments (`callback_data`, `url`, `web_app`, …) are passed
    through untouched — exactly one of them may be set, as Telegram requires.
    """
    kwargs = {k: v for k, v in kwargs.items() if v is not None}
    clean = style if style in VALID_STYLES else None
    icon_id = emoji_ids.CATALOGUE.get(icon or _DEFAULT_ICON.get(clean or "", ""), None)

    if not clean and not icon_id:
        return B(text=text, **kwargs)

    try:
        return B(text=text, style=clean, icon_custom_emoji_id=icon_id, **kwargs)
    except (TypeError, ValueError):
        # Library too old for the field: send a normal button, never fail.
        return B(text=text, **kwargs)


def primary(text: str, icon: Optional[str] = "🌐", **kw) -> B:
    return styled(text, style="primary", icon=icon, **kw)


def success(text: str, icon: Optional[str] = "✅", **kw) -> B:
    return styled(text, style="success", icon=icon, **kw)


def danger(text: str, icon: Optional[str] = "❌", **kw) -> B:
    return styled(text, style="danger", icon=icon, **kw)


def plain(text: str, **kw) -> B:
    return styled(text, **kw)
