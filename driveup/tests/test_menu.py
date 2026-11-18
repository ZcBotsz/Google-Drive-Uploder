"""Every command must be reachable by tapping, and the tap must do the real thing.

The complaint: "pure commands button mein bhi aane chahiye". A menu that is only
decorative is worse than none, so each button is pressed here and the answer is
read back from Telegram.
"""
from __future__ import annotations

import pytest

from driveup.bot.menus import ADMIN_MENU, USER_MENU, main_menu_kb, quick_kb
from driveup.tests import fake_tg
from driveup.tests.test_bot import botenv, connect_drive, feed, said  # noqa: F401

ALL_COMMANDS = {c for c, _, _ in USER_MENU} | {c for c, _, _ in ADMIN_MENU}


def test_every_shipped_command_has_a_button():
    """Nothing a user can type may be missing from the menu."""
    import re
    from pathlib import Path
    handlers = Path(__file__).resolve().parents[1] / "bot" / "handlers"
    found = set()
    for f in handlers.glob("*.py"):
        found |= set(re.findall(r'Command\("([a-z_]+)"\)', f.read_text()))
    missing = found - ALL_COMMANDS - {"start", "menu", "hello"}
    assert not missing, f"commands with no button: {sorted(missing)}"


def test_menu_rows_are_phone_sized():
    kb = main_menu_kb(is_admin=True)
    for row in kb.inline_keyboard:
        assert len(row) <= 2, f"a row of {len(row)} would wrap badly on a phone"


async def test_hello_greets_and_offers_buttons(botenv):
    await feed(botenv, fake_tg.message("/hello", uid=42, mid=11))
    text = said(botenv)
    assert "Hello" in text
    sent = [c for c in botenv["sess"].calls if c["method"] == "SendMessage"]
    kb = sent[-1]["params"].get("reply_markup")
    assert kb and kb["inline_keyboard"], "no buttons on the greeting"


async def test_menu_lists_every_command(botenv):
    await feed(botenv, fake_tg.message("/menu", uid=42, mid=12))
    sent = [c for c in botenv["sess"].calls if c["method"] == "SendMessage"]
    buttons = [b["callback_data"] for row in
               sent[-1]["params"]["reply_markup"]["inline_keyboard"] for b in row]
    callbacks = set(buttons)
    # The home screen stays compact; the More screen leads to focused sections.
    assert {"m:connect", "m:studio", "ui:more"} <= callbacks
    before = len(botenv["sess"].calls)
    await feed(botenv, fake_tg.callback("ui:more", uid=42, mid=12))
    edits = [c for c in botenv["sess"].calls[before:] if c["method"] == "EditMessageText"]
    assert edits, "More should edit the existing menu message"
    section_callbacks = [b["callback_data"] for row in
                         edits[-1]["params"]["reply_markup"]["inline_keyboard"] for b in row]
    assert {"ui:drive", "ui:uploads", "ui:account"} <= set(section_callbacks)
    assert "ui:admin" not in section_callbacks
    # Every user command appears in at least one focused screen.
    from driveup.bot.menus import drive_menu_kb, uploads_menu_kb, account_menu_kb
    scoped = set()
    for kb in (drive_menu_kb(), uploads_menu_kb(), account_menu_kb()):
        scoped |= {b.callback_data[2:] for row in kb.inline_keyboard for b in row
                   if b.callback_data and b.callback_data.startswith("m:")}
    assert {c for c, _, _ in USER_MENU} - {"help"} <= scoped | {"help", "export"}
    assert "m:admin" not in callbacks


async def test_a_button_runs_the_real_command(botenv):
    """m:help must produce exactly what /help produces."""
    await feed(botenv, fake_tg.message("/help", uid=42, mid=13))
    typed = said(botenv)
    assert "Commands" in typed
    before = len(botenv["sess"].calls)
    await feed(botenv, fake_tg.callback("m:help", uid=42, mid=14))
    after = botenv["sess"].calls[before:]
    edits = [c for c in after if c["method"] in ("EditMessageText", "SendMessage")]
    assert edits, "the button produced no answer"
    assert any("Commands" in (c["params"].get("text") or "") for c in edits)


async def test_admin_button_is_refused_for_a_normal_user(botenv):
    before = len(botenv["sess"].calls)
    await feed(botenv, fake_tg.callback("m:stats", uid=42, mid=15))
    answers = [c for c in botenv["sess"].calls[before:] if c["method"] == "AnswerCallbackQuery"]
    assert answers, "the refusal was never sent"
    assert "admin" in (answers[-1]["params"].get("text") or "").lower()
