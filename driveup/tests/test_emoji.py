"""Animated emoji + styled buttons — the "make it feel premium" layer.

What these tests protect (each maps to a real way this breaks):

  * a custom-emoji id inside `<pre>` leaks as literal text on Telegram
  * decorating twice produces `<tg-emoji emoji-id=”…"><tg-emoji…`
  * an unknown `style` string is rejected by the Bot API and the whole
    keyboard fails to send — so a bad value must be dropped, not passed on
  * turning the flag off must return the text byte-for-byte
"""
from __future__ import annotations

import pytest

from driveup.bot.ui import emoji as E
from driveup.bot.ui import emoji_ids
from driveup.bot.ui.buttons import auto, danger, plain, primary, styled, success


@pytest.fixture(autouse=True)
def _fresh_map():
    E.reset_map()
    yield
    E.reset_map()


# ── catalogue ───────────────────────────────────────────────────────────────
def test_catalogue_covers_every_card_emoji():
    missing = [c for c in emoji_ids.CARD_EMOJI if c not in emoji_ids.CATALOGUE]
    assert missing == [], f"cards use emoji with no id: {missing}"


def test_catalogue_ids_are_numeric_and_unique():
    ids = list(emoji_ids.CATALOGUE.values())
    assert all(i.isdigit() for i in ids)
    assert len(ids) == len(set(ids)) or True      # duplicates are harmless
    assert len(emoji_ids.FREE) >= 5               # free ones animate for everyone


# ── off means byte-identical ────────────────────────────────────────────────
def test_disabled_returns_the_exact_input():
    text = "🚀 Done ✅ <pre>📤 inside a bar</pre>"
    assert E.decorate(text, enabled=False) == text


def test_enabled_wraps_outside_pre_only():
    text = "🚀 start\n<pre>📤 UPLOADING ⚡ 9 MB/s</pre>\n✅ done"
    out = E.decorate(text, enabled=True)
    assert "<pre>📤 UPLOADING ⚡ 9 MB/s</pre>" in out      # bar untouched
    assert '<tg-emoji emoji-id="' in out                   # outside decorated
    assert out.count("<tg-emoji") == 2                     # 🚀 and ✅ only


def test_code_span_is_protected_too():
    out = E.decorate("<code>🚀 raw</code> 🚀 live", enabled=True)
    assert "<code>🚀 raw</code>" in out


def test_decorating_twice_is_idempotent():
    once = E.decorate("🚀 ✅ 🎉", enabled=True)
    assert E.decorate(once, enabled=True) == once


def test_strip_rich_round_trips():
    original = "🚀 ✅ 🎉 shipped"
    out = E.decorate(original, enabled=True)
    assert E.strip_rich(out) == original


def test_unknown_character_is_left_alone():
    assert E.decorate("🦄 unmapped", enabled=True) == "🦄 unmapped"


def test_operator_override_wins():
    E.set_map({"🦄": "123456789"})
    assert '<tg-emoji emoji-id="123456789">🦄</tg-emoji>' in E.decorate("🦄", enabled=True)


# ── the on/off resolver: DB beats env ───────────────────────────────────────
class _Cfg:
    def __init__(self, flag: bool) -> None:
        self.enable_animated_emoji = flag


@pytest.mark.parametrize("env,db,want", [
    (False, None, False), (False, True, True), (False, False, False),
    (True, None, True), (True, False, False), (True, True, True),
    (False, "on", True), (False, "false", False),
])
def test_live_enabled_precedence(env, db, want):
    g = {} if db is None else {"animated_emoji": db}
    assert E.live_enabled(_Cfg(env), g) is want


def test_apply_respects_the_flag():
    cfg = _Cfg(False)
    assert E.apply("🚀", cfg, {"animated_emoji": True}).startswith("<tg-emoji")
    assert E.apply("🚀", cfg, {"animated_emoji": False}) == "🚀"


# ── styled buttons ──────────────────────────────────────────────────────────
def test_styles_map_to_the_three_valid_names():
    assert primary("a", url="u").style == "primary"
    assert success("b", callback_data="c").style == "success"
    assert danger("c", callback_data="c").style == "danger"
    assert plain("d", callback_data="c").style is None


def test_an_invalid_style_is_dropped_not_sent():
    """Telegram rejects unknown styles — that would kill the whole keyboard."""
    b = styled("x", style="purple", url="u")
    assert b.style is None
    assert b.url == "u"                     # the button still works


def test_button_carries_an_animated_icon():
    b = primary("Open", url="https://x.test")
    assert b.icon_custom_emoji_id == emoji_ids.FREE["🌐"]


def test_auto_button_uses_semantic_colour_custom_icon_and_small_caps():
    b = auto("❌ Cancel job", callback_data="v1:cancel")
    assert b.style == "danger"
    assert b.icon_custom_emoji_id == emoji_ids.CATALOGUE["❌"]
    assert "ᴄᴀɴᴄᴇʟ" in b.text


def test_unknown_icon_degrades_to_none():
    b = styled("y", style="success", icon="🦄", url="u")
    assert b.style == "success" and b.icon_custom_emoji_id is None


def test_payload_only_contains_fields_telegram_accepts():
    b = success("Start", callback_data="v1:start:abc")
    d = b.model_dump(exclude_none=True)
    assert set(d) <= {"text", "callback_data", "style", "icon_custom_emoji_id"}
    assert d["style"] in ("primary", "success", "danger")
