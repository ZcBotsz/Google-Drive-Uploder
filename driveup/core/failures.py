"""Why a file failed, in words the person in the chat can act on.

The report was "KB ki file de raha hun, upload nahi ho raha" — and the bot was
answering with a summary line: "0 done · 1 failed". Technically true, useless in
practice: nothing said *why*, so the only reading is "the bot is broken".

Every failure here is a real, named condition. If a reason ever reaches the
fallback branch, that branch says so plainly rather than inventing a cause —
a guessed explanation is worse than an honest "I do not know".
"""
from __future__ import annotations

from ..drive.errors import (AuthExpired, DailyLimit, DriveError, NetworkError,
                            NotFound, PermissionDenied, StorageQuota)
from ..strings import esc

# One line, ordered from most specific to most general.
PLAIN: list[tuple[type, str]] = [
    (StorageQuota,
     "Google Drive is full — free some space, or /connect another account."),
    (DailyLimit,
     "Drive's 750 GB daily upload cap is used up — it resets in 24 hours."),
    (AuthExpired,
     "Drive access expired — send /connect and allow access again."),
    (PermissionDenied,
     "Drive refused permission for that folder. Pick a folder DriveUp created "
     "(/folders), or ask the owner to allow full Drive access."),
    (NotFound,
     "Google Drive could not find that file or folder any more."),
    (NetworkError,
     "The connection broke while transferring. Check the bot's internet and "
     "try again — a finished part is not lost."),
]


def explain(err: Exception) -> str:
    """A short, human cause for one failed file."""
    if err is None:
        return "Unknown error."
    for cls, words in PLAIN:
        if isinstance(err, cls):
            return words
    if isinstance(err, DriveError):
        detail = str(err).strip()
        return f"Google Drive rejected it: {detail[:160]}" if detail else "Drive rejected it."
    detail = str(err).strip()
    low = detail.lower()
    # Transfer-layer failures (aiohttp gives the status, not a sentence). These
    # are the ones a self-hosted Bot API produces most often, and the raw repr
    # ("ClientResponseError 404 ...") told the user nothing actionable.
    status = getattr(err, "status", None)
    if status == 404:
        return ("Telegram's file server returned 404 for that file — the Bot API "
                "server did not have it on disk. Check TELEGRAM_FILES_URL and the "
                "FILE_SERVER=1 setting on the API service.")
    if status in (401, 403):
        return (f"The file server refused the request (HTTP {status}) — check that "
                "TELEGRAM_FILES_URL points at the API service's file port (8080).")
    if status:
        return f"The file server answered HTTP {status} while fetching the file."
    if "cannot connect" in low or "connector" in low or "name or service" in low:
        return ("Could not reach the Bot API server — check TELEGRAM_API_URL / "
                "TELEGRAM_FILES_URL (private networking, same region).")
    if "timed out" in low or "timeout" in low:
        return "It took too long to answer — try again."
    if isinstance(err, FileNotFoundError):
        return "A file the download expected is no longer there — send it again."
    if isinstance(err, OSError) and getattr(err, "errno", 0) == 28:
        return "The bot's disk is full — free space or switch UPLOAD_MODE to stream."
    return f"Unexpected error: {detail[:160]}" if detail else "Unexpected error."


def service_line(err: Exception) -> str:
    """For the log and the admin panel: the class name plus the message."""
    return f"{type(err).__name__}: {str(err)[:300]}"
