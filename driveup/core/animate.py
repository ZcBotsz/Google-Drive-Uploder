"""Redraw the progress card while a job runs — the animation loop.

Why this module exists
----------------------
The card was only ever drawn twice per file: once when Start was pressed and
once when the file finished. On a 3 KB file that is a single frame, so the bars
looked frozen and the bot looked like it was doing nothing. That is the report
"process bar kaam nahi karta".

Here the card is redrawn on a fixed tick (default 0.5 s, well inside Telegram's
one-edit-per-second-per-chat guidance because the gate below still throttles),
so the spinner turns, the bars advance and the "left" number counts down even
while a single large file is moving.

Only *cosmetic* state lives here. If this task dies the upload carries on — the
worker owns the bytes, not the picture.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import time

from ..bot.ui.cards import progress_card
from ..bot.ui import emoji as emoji_ui
from ..tg.ratelimit import safe_edit

log = logging.getLogger("driveup.animator")


def progress_markup(state, cfg, chat_id):
    """Keep controls visible; never put a signed private link into a group chat."""
    from ..bot.keyboards import progress_kb
    url = ""
    uid = getattr(state, "user_id", 0)
    if uid and int(chat_id) == int(uid) and getattr(cfg, "session_secret", ""):
        from .links import mint_token
        base = getattr(cfg, "public_url", "").rstrip("/")
        if base.startswith("https://"):
            token = mint_token(cfg.session_secret, uid, job_id=state.job_id)
            url = f"{base}/start?t={token}&job={state.job_id}"
    return progress_kb(state.job_id, paused=bool(state.paused), studio_url=url)


class Animator:
    """Keeps one Telegram message in step with one ProgressState."""

    def __init__(self, bot, state, *, chat_id: int, message_id: int, cfg,
                 gate=None, tick: float = 0.5) -> None:
        self.bot = bot
        self.state = state
        self.chat_id = chat_id
        self.message_id = message_id
        self.cfg = cfg
        self.gate = gate
        self.tick = max(0.25, float(tick))
        self.frame = 0
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.edits = 0

    # ── the picture, from the live state ────────────────────────────────
    def render(self) -> str:
        s = self.state
        text = progress_card(
            index=s.index, count=s.count, name=s.name, folder=s.folder,
            tg_pct=s.tg_pct, drive_pct=s.drive_pct, done=s.done, total=s.total,
            speed=s.speed, elapsed=s.elapsed, eta=s.eta, retries=s.retries,
            chunk_mb=self.cfg.chunk_bytes // 1024 // 1024,
            overall_pct=s.overall_pct, frame=self.frame,
            style=getattr(self.cfg, "progress_style", "glow"),
            paused=bool(getattr(s, "paused", False)),
            stage_label=self._stage_label(), source_label=s.source_label,
            tg_done=s.tg_done, drive_done=s.drive_done, detail=s.detail,
        )
        return emoji_ui.apply(text, self.cfg)

    def _stage_label(self) -> str:
        stage = getattr(self.state, "stage", "") or ""
        return {
            "preparing": "PREPARING TRANSFER",
            "downloading": "DOWNLOADING TO SERVER",
            "transferring": "DOWNLOADING AND UPLOADING",
            "uploading": "UPLOADING TO DRIVE",
            "paused": "PAUSED",
            "done": "DONE",
        }.get(stage, "WORKING")

    # ── lifecycle ───────────────────────────────────────────────────────
    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"anim-{self.state.job_id}")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(self._task, timeout=3)

    async def _run(self) -> None:
        last = ""
        try:
            while not self._stop.is_set():
                if getattr(self.state, "finished", False):
                    break
                text = self.render()
                # An edit that changes nothing is wasted; the frame counter
                # moves the spinner, so this skips only when the spinner is
                # paused too (a real pause).
                if text != last:
                    ok = await safe_edit(self.bot, self.chat_id, self.message_id, text,
                                         reply_markup=progress_markup(self.state, self.cfg, self.chat_id),
                                         gate=self.gate, pct=(self.state.tg_pct + self.state.drive_pct) / 2)
                    if ok:
                        self.edits += 1
                        last = text
                self.frame += 1
                await asyncio.sleep(self.tick)
        except asyncio.CancelledError:
            raise
        except Exception as e:                     # noqa: BLE001
            # The animation is cosmetic: a failure here must never fail a job.
            log.debug("animator stopped: %s", e)

    async def final(self) -> None:
        """Draw one last frame, bypassing the throttle (100 % must be shown)."""
        with contextlib.suppress(Exception):
            await safe_edit(self.bot, self.chat_id, self.message_id, self.render(),
                            reply_markup=progress_markup(self.state, self.cfg, self.chat_id),
                            gate=self.gate, pct=100.0, force=True)


def start_for(worker, state, chat_id: int, message_id: int) -> Animator:
    """Create and start an animator for a job, reusing the worker's gate."""
    anim = Animator(worker.bot, state, chat_id=chat_id, message_id=message_id,
                    cfg=worker.cfg, gate=getattr(worker, "fg", None),
                    tick=getattr(worker.cfg, "progress_tick", 0.5))
    anim.start()
    return anim
