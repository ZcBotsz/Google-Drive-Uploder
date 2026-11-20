"""Progress bookkeeping for one item — two-stage pipeline + cancellation.

`ProgressState` is what the Telegram card and the web SSE both read, so both
surfaces always agree. It is intentionally tiny and synchronous: the worker
calls it from the upload callbacks, the UI reads it on a timer.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Optional

from ..bot.ui.cards import SpeedMeter, clamp_pct


@dataclass
class ProgressState:
    job_id: str = ""
    user_id: int = 0
    item_id: str = ""
    name: str = ""
    folder: str = ""
    index: int = 1
    count: int = 1
    total: int = 0
    tg_done: int = 0
    drive_done: int = 0
    retries: int = 0
    stage: str = "waiting"      # waiting | downloading | uploading | verifying | done | failed
    started: float = field(default_factory=time.time)
    started_monotonic: float = field(default_factory=time.monotonic)
    overall_bytes_done: int = 0
    overall_bytes_total: int = 0
    paused: bool = False
    error: str = ""
    finished: bool = False     # set when the run ends, so the animator stops
    meter: SpeedMeter = field(default_factory=SpeedMeter)  # source/download meter
    drive_meter: SpeedMeter = field(default_factory=SpeedMeter)
    streaming: bool = False
    source_label: str = "Telegram"
    detail: str = ""
    cancel: Optional[asyncio.Event] = None

    # ── updates from the engines ─────────────────────────────────────────
    def on_tg(self, done: int, total: int) -> None:
        self.tg_done = done
        if total:
            self.total = total
        self.stage = "transferring" if self.streaming else "downloading"
        self.meter.add(time.monotonic(), done)

    def on_drive(self, done: int, total: int) -> None:
        self.drive_done = done
        if total:
            self.total = total
        self.stage = "transferring" if self.streaming else "uploading"
        self.drive_meter.add(time.monotonic(), done)

    def note_retry(self) -> None:
        self.retries += 1

    # ── numbers the UI shows ─────────────────────────────────────────────
    @property
    def tg_pct(self) -> float:
        return clamp_pct(self.tg_done, self.total or 1)

    @property
    def drive_pct(self) -> float:
        return clamp_pct(self.drive_done, self.total or 1)

    @property
    def done(self) -> int:
        return self.tg_done if self.stage == "downloading" else self.drive_done

    @property
    def speed(self) -> float:
        meter = self.meter if self.stage == "downloading" else self.drive_meter
        if self.stage == "transferring" and not meter.speed():
            meter = self.meter
        if not meter.samples or time.monotonic() - meter.samples[-1][0] > 10:
            return 0.0
        return meter.speed()

    @property
    def elapsed(self) -> float:
        return max(0.0, time.time() - self.started)

    @property
    def eta(self) -> Optional[float]:
        if not self.speed:
            return None
        if self.stage == "transferring" and not self.drive_meter.speed():
            return self.meter.eta(self.tg_done, self.total or 0)
        meter = self.meter if self.stage == "downloading" else self.drive_meter
        return meter.eta(self.done, self.total or 0)

    @property
    def overall_pct(self) -> float:
        if self.overall_bytes_total:
            base = self.overall_bytes_done + self.drive_done
            return clamp_pct(base, self.overall_bytes_total)
        done_files = max(0, self.index - 1)
        part = (self.drive_done / self.total) if self.total else 0
        return clamp_pct(done_files + part, self.count)

    def snapshot(self) -> dict:
        return {"job_id": self.job_id, "item_id": self.item_id, "name": self.name,
                "folder": self.folder, "index": self.index, "count": self.count,
                "total": self.total, "done": self.done, "tg_done": self.tg_done,
                "drive_done": self.drive_done, "source_label": self.source_label, "detail": self.detail,
                "tg_pct": round(self.tg_pct, 1),
                "drive_pct": round(self.drive_pct, 1), "overall_pct": round(self.overall_pct, 1),
                "speed": round(self.speed), "elapsed": round(self.elapsed),
                "eta": None if self.eta is None else round(self.eta),
                "retries": self.retries, "stage": self.stage, "paused": self.paused,
                "error": self.error}

    def want_stop(self) -> bool:
        return bool(self.cancel and self.cancel.is_set())


class ProgressHub:
    """One place that fans progress out to the bot card and to SSE clients."""

    def __init__(self) -> None:
        self.current: dict[str, ProgressState] = {}
        self._subs: dict[str, list[asyncio.Queue]] = {}

    def put(self, state: ProgressState) -> None:
        self.current[state.job_id] = state
        for q in self._subs.get(state.job_id, []):
            try:
                q.put_nowait(state.snapshot())
            except asyncio.QueueFull:
                try:
                    q.get_nowait()  # retain the most recent state, not stale chunks
                    q.put_nowait(state.snapshot())
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass

    def get(self, job_id: str) -> Optional[ProgressState]:
        return self.current.get(job_id)

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._subs.setdefault(job_id, []).append(q)
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        lst = self._subs.get(job_id, [])
        if q in lst:
            lst.remove(q)

    def finish(self, job_id: str) -> None:
        self.current.pop(job_id, None)
        for q in self._subs.get(job_id, []):
            try:
                q.put_nowait({"stage": "job-finished"})
            except asyncio.QueueFull:
                try:
                    q.get_nowait()
                    q.put_nowait({"stage": "job-finished"})
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
