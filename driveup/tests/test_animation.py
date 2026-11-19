"""The card must visibly move while a file uploads.

The complaint was "process bar kaam nahi karta". The bars were correct but the
message was only edited twice per file, so on a small file the user saw a single
frozen frame. These tests pin the behaviour that fixes it.
"""
from __future__ import annotations

import asyncio

import pytest

from driveup.bot.ui.cards import bar, dots, progress_card, pulse, runner, spinner
from driveup.strings import sc
from driveup.core.progress import ProgressState


def test_two_consecutive_frames_differ():
    """If two frames are identical there is no animation at all."""
    kw = dict(index=1, count=3, name="a.mp4", folder="My Drive", tg_pct=50.0,
              drive_pct=20.0, done=500, total=1000, overall_pct=30.0)
    a = progress_card(frame=0, **kw)
    b = progress_card(frame=1, **kw)
    c = progress_card(frame=5, **kw)
    assert a != b, "frame 0 and 1 are identical — nothing animates"
    assert a != c
    assert "Telegram to server" in a
    assert "Server to Google Drive" in a
    assert all(label in a for label in ("Left", "ETA", "Retries", "Overall"))
    assert "█" not in a and "╭" not in a and "░" not in a


def test_the_bar_head_walks_within_a_whole_percentage():
    """At a fixed 44 % the bar must still show motion between ticks."""
    frames = {bar(44.0, 20, "glow", f) for f in range(8)}
    assert len(frames) > 1, "the bar is frozen between percentages"


def test_spinner_and_pulse_cycle():
    assert len({spinner(f) for f in range(20)}) > 1
    assert len({pulse(f) for f in range(20)}) > 1
    assert len({runner(f, p) for f, p in [(0, 10), (0, 50), (0, 95)]}) == 3
    assert "🏁" in runner(0, 100)


def test_dots_breathe():
    assert len({dots(f) for f in range(4)}) > 1


async def test_the_animator_edits_the_message_while_it_runs():
    """Drive a state forward and count the edits the animator made."""
    from driveup.core.animate import Animator

    class Bot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, **kw):
            self.edits.append(kw["text"])

    class Cfg:
        chunk_bytes = 8 * 1024 * 1024
        progress_style = "glow"
        progress_tick = 0.01

    bot = Bot()
    st = ProgressState(job_id="j", item_id="i", name="x.bin", total=1000)
    anim = Animator(bot, st, chat_id=1, message_id=2, cfg=Cfg(), gate=None, tick=0.01)
    anim.start()
    # Animator.tick has a deliberate 0.25 s floor (never edit faster than 4x a
    # second — Telegram rate limits), so each step must wait longer than that.
    for done in (100, 400, 900):
        st.on_tg(done, 1000)
        await asyncio.sleep(0.35)
    await anim.stop()
    assert len(bot.edits) >= 2, f"only {len(bot.edits)} edits — the card is static"
    # the last frame must be the most recent progress, not a stale one
    assert "900" in bot.edits[-1] or "90" in bot.edits[-1]


def test_the_tick_never_goes_below_telegram_safe_speed():
    """A tick under 0.25 s would edit faster than 4x/second and get us throttled."""
    from driveup.core.animate import Animator
    a = Animator(None, ProgressState(job_id="j"), chat_id=1, message_id=1,
                 cfg=type("C", (), {"chunk_bytes": 1024, "progress_style": "glow"})(),
                 tick=0.01)
    assert a.tick >= 0.25
