"""Animated custom emoji for every outgoing card (opt-in, safe by construction).

Design rules learned the hard way:

1. **No monkey-patching.** The reference implementation wrapped `send_message`
   and friends on the client class. That works on one library version and
   breaks on the next. Here the cards call `decorate()` explicitly, so nothing
   outside this module knows animated emoji exist.

2. **Never inside `<pre>`.** Telegram does not render custom emoji inside
   `<pre>`/`<code>`; the tag would leak as literal text. `decorate()` therefore
   splits on `pre`/`code` spans and leaves them untouched. The progress *bar*
   stays plain — only the wrapper markup around it is decorated.

3. **Built-in catalogue + operator override.** `emoji_ids.CATALOGUE` ships 36
   verified ids so the feature works out of the box; an operator can paste more
   from Settings and they win. Ids the bot may not use simply fall back to the
   plain character, so a stale id degrades gracefully instead of breaking.

4. **Idempotent.** Already-wrapped tags are skipped, so double decoration is a
   no-op rather than `&lt;tg-emoji…`.
"""
from __future__ import annotations

import re
from typing import Dict, Optional

from . import emoji_ids

# char → (custom_emoji_id, fallback_char). Seeded from the bundled catalogue.
EMOJI_MAP: Dict[str, tuple[str, str]] = {c: (i, c) for c, i in emoji_ids.CATALOGUE.items()}

# Characters we style when the feature is on and an id exists.
DEFAULT_EMOJI = {"rocket": "🚀", "folder": "📂", "upload": "📤", "check": "✅",
                 "warn": "⚠️", "cross": "❌", "gear": "⚙️", "lock": "🔒", "fire": "🔥"}

# Spans Telegram refuses to render custom emoji inside.
_PROTECTED = re.compile(r"(<(?:pre|code)\b[^>]*>.*?</(?:pre|code)>)", re.S | re.I)
_ANY_TAG = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)


def set_map(mapping: Dict[str, str]) -> None:
    """Admin paste: {"🚀": "5368324170671202286"} — merged over the catalogue."""
    for k, v in (mapping or {}).items():
        if k and v:
            EMOJI_MAP[k] = (str(v), k)


def reset_map() -> None:
    """Back to the bundled catalogue (used by tests and by 'clear overrides')."""
    EMOJI_MAP.clear()
    EMOJI_MAP.update({c: (i, c) for c, i in emoji_ids.CATALOGUE.items()})


def rich(char: str, enabled: bool = True) -> str:
    """`🚀` → `<tg-emoji emoji-id="…">🚀</tg-emoji>` when enabled and known."""
    if not enabled:
        return char
    pair = EMOJI_MAP.get(char)
    if not pair:
        return char
    eid, fallback = pair
    return f'<tg-emoji emoji-id="{eid}">{fallback}</tg-emoji>'


def _keys_by_length() -> list[str]:
    return sorted(EMOJI_MAP.keys(), key=len, reverse=True)


def decorate(text: str, enabled: bool = False) -> str:
    """Replace every mapped emoji outside `<pre>`/`<code>`/existing tags."""
    if not enabled or not text or not EMOJI_MAP:
        return text
    parts = _PROTECTED.split(text)
    out: list[str] = []
    for part in parts:
        if not part:
            continue
        if _PROTECTED.fullmatch(part):        # a <pre> block: leave byte-identical
            out.append(part)
            continue
        out.append(_decorate_plain(part))
    return "".join(out)


def _decorate_plain(text: str) -> str:
    keys = _keys_by_length()
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        # never touch an emoji that is already inside a tg-emoji tag
        if text.startswith("<tg-emoji", i):
            end = text.find("</tg-emoji>", i)
            if end != -1:
                out.append(text[i:end + len("</tg-emoji>")])
                i = end + len("</tg-emoji>")
                continue
        for k in keys:
            if text.startswith(k, i):
                out.append(rich(k, enabled=True))
                i += len(k)
                break
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def strip_rich(text: str) -> str:
    """For plain-text exports (CSV, logs)."""
    return _ANY_TAG.sub(r"\1", text)


def count_rich(text: str) -> int:
    """How many animated emoji are in a string — used by the tests."""
    return len(_ANY_TAG.findall(text or ""))


def is_enabled(cfg, default: bool = False) -> bool:
    """Read the live flag defensively: cfg may be a stub in tests."""
    flag: Optional[bool] = getattr(cfg, "enable_animated_emoji", default)
    return bool(flag)


def live_enabled(cfg, global_settings: Optional[dict] = None) -> bool:
    """Admin panel wins over the environment variable.

    Precedence: Settings → animated emoji (DB) → ENABLE_ANIMATED_EMOJI (env).
    The DB value is stored as a bool or the strings "1"/"true"/"on".
    """
    g = global_settings or {}
    if "animated_emoji" in g:
        v = g["animated_emoji"]
        if isinstance(v, str):
            return v.strip().lower() in ("1", "true", "yes", "on")
        return bool(v)
    return is_enabled(cfg)


def apply(text: str, cfg, global_settings: Optional[dict] = None) -> str:
    """The call the cards use: decorate only when the feature is actually on."""
    return decorate(text, enabled=live_enabled(cfg, global_settings))
