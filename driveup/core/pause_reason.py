"""Why a run stopped, in words a user can act on.

A full Drive, a daily cap and an expired token are all terminal for the current
run. Failing the remaining files one by one buries the real cause under identical
red rows, so the worker pauses once and explains instead. The copy lives here so
the worker module stays about mechanics.
"""
from __future__ import annotations

from ..drive.errors import DailyLimit, StorageQuota
from ..strings import esc


def pause_message(err: Exception, done: int) -> str:
    """The sentence the user sees when the run stops for a Drive-side reason."""
    if isinstance(err, StorageQuota):
        return ("💾 <b>Google Drive is full.</b> Stopped here — the rest would all "
                f"fail the same way. {done} file(s) already safe.\n\n"
                "Free space (or /connect another account), then /queue → Resume.")
    if isinstance(err, DailyLimit):
        return "📉 Drive's 750 GB/day cap hit. Job paused — resume tomorrow."
    return f"🔑 {esc(str(err))}\nJob paused — /connect, then /queue → Resume."
