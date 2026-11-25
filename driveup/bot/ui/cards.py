"""Telegram cards — the progress card, job card, summaries.

The box-drawing style comes from the existing bot (spec A9); everything below is
rendered from data, so a test can assert the exact bytes.
"""
from __future__ import annotations

from typing import Iterable, Optional

from ...core.naming import human_size
from ...strings import esc, sc

# ASCII progress glyphs render consistently in Telegram Desktop, Android and
# web fonts (the previous block/braille glyphs showed as empty boxes on some phones).
BAR_FULL = "#"
BAR_EMPTY = "-"
SPINNER = "|/-\\"
STYLES = {
    "classic": (BAR_FULL, BAR_EMPTY),
    "striped": ("=", "."),
    "glow": ("#", "-"),
    "segmented": ("=", "."),
}

HEADS = "><"
PULSE = "|/-\\"
DOTS = "..."

# A tiny zip-file "runner" that walks along under the bars, frame by frame.
RUNNER = ("🏃", "🏃‍♂️", "🏃", "🏃‍♂️")


def bar(pct: float, width: int = 20, style: str = "glow", frame: int = 0) -> str:
    """A bar with a moving head, so motion is visible before the number moves."""
    full, empty = STYLES.get(style, STYLES["glow"])
    pct = max(0.0, min(100.0, float(pct or 0)))
    filled = int(round(pct / 100 * width))
    body = full * filled + empty * (width - filled)
    if 0 < filled < width:
        # replace the boundary cell with a rotating head character
        head = HEADS[(frame // 2) % len(HEADS)] if style == "glow" else full
        body = body[: filled - 1] + head + body[filled:]
    return body


def spinner(frame: int) -> str:
    return SPINNER[frame % len(SPINNER)]


def pulse(frame: int) -> str:
    return PULSE[frame % len(PULSE)]


def dots(frame: int) -> str:
    """A three-dot ellipsis that breathes — used in the stage line."""
    n = frame % 4
    return DOTS[: n + 1] if n < 3 else DOTS[:1]


def runner(frame: int, pct: float, width: int = 20) -> str:
    """A little figure walking along a track, position following the progress."""
    pos = int(max(0.0, min(100.0, pct)) / 100 * (width - 1))
    track = ["·"] * width
    track[pos] = "🏁" if pct >= 100 else "🏃"
    return "".join(track)


def clamp_pct(done: float, total: float) -> float:
    if not total:
        return 0.0
    return max(0.0, min(100.0, done / total * 100.0))


def human_secs(seconds: float) -> str:
    seconds = max(0, int(seconds or 0))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def speed_text(bps: float) -> str:
    return f"{human_size(bps)}/s" if bps else "—"


class SpeedMeter:
    """Moving average of the last 12 samples (the zip's approach, kept)."""

    def __init__(self, window: int = 12) -> None:
        self.samples: list[tuple[float, float]] = []
        self.window = window

    def add(self, ts: float, done: int) -> None:
        self.samples.append((ts, done))
        if len(self.samples) > self.window + 1:
            self.samples.pop(0)

    def speed(self) -> float:
        if len(self.samples) < 2:
            return 0.0
        (t0, b0), (t1, b1) = self.samples[0], self.samples[-1]
        dt = t1 - t0
        if dt <= 0:
            return 0.0
        return max(0.0, (b1 - b0) / dt)

    def eta(self, done: int, total: int) -> Optional[float]:
        if len(self.samples) < 3:
            return None
        sp = self.speed()
        if sp <= 0:
            return None
        return max(0.0, (total - done) / sp)


def progress_card(*, index: int, count: int, name: str, folder: str,
                  tg_pct: float, drive_pct: float, done: int, total: int,
                  speed: float = 0.0, elapsed: float = 0.0, eta: Optional[float] = None,
                  retries: int = 0, chunk_mb: int = 8, overall_pct: float = 0.0,
                  frame: int = 0, style: str = "glow", paused: bool = False,
                  stage_label: str = "PREPARING TRANSFER", source_label: str = "Telegram",
                  tg_done: Optional[int] = None, drive_done: Optional[int] = None,
                  detail: str = "") -> str:
    """The live card. Every line that can move, moves.

    Redrawn about twice a second by core/animate.py, so the spinner turns, the
    bar head walks, the dots breathe and the runner advances. On a small file
    this is the difference between "nothing happened" and "it is working".
    """
    sp = spinner(frame)
    pl = pulse(frame)
    dt = dots(frame)
    title = "⏸ PAUSE REQUESTED (AFTER CURRENT FILE)" if paused else stage_label
    left = max(0, total - done)
    eta_txt = human_secs(eta) if eta is not None else "—"
    pct_big = overall_pct
    # a ten-cell "fuel gauge" that fills with the overall percentage
    gauge = bar(pct_big, 10, style, frame)
    lines = [
        f"📄 {esc(shorten(name, 42))}",
        f"📁 {esc(shorten(folder, 44))}",
        "",
        f"⬇️ {esc(source_label)} to server",
        f"[{bar(tg_pct, 16, style, frame)}] {tg_pct:5.1f}%" + (f"  {human_size(tg_done)} / {human_size(total)}" if tg_done is not None else ""),
        "⬆️ Server to Google Drive",
        f"[{bar(drive_pct, 16, style, frame + 3)}] {drive_pct:5.1f}%" + (f"  {human_size(drive_done)} / {human_size(total)}" if drive_done is not None else ""),
        "",
        *( [esc(shorten(detail, 52))] if detail else [] ),
        f"Current stage: {human_size(done)} / {human_size(total)}",
        f"Left {human_size(left)}   ⚡ {speed_text(speed)}",
        f"{human_secs(elapsed)}   ETA {eta_txt}",
        f"Retries {retries}   {chunk_mb} MB   {index}/{count}",
        "",
        f"[{gauge}] {pct_big:3.0f}%  Overall Drive progress",
    ]
    # Keep the heading outside <pre>: Telegram renders custom emoji tags in the
    # caption/header, while the ASCII progress bars remain monospaced and legible.
    return f"🚀 <b>{esc(title)}</b> {dt}\n<pre>" + "\n".join(lines) + "</pre>"


def shorten(text: str, n: int) -> str:
    text = text or ""
    return text if len(text) <= n else text[: max(1, n - 1)] + "…"


def job_card(*, files: int, size: int, dest: str, lang: str = "en") -> str:
    from ...strings import t
    return t("job_card", lang, files=files, size=human_size(size), dest=esc(dest or "My Drive"))


def summary_card(*, done: int, skipped: int, failed: int, nbytes: int, elapsed: float,
                 lang: str = "en") -> str:
    from ...strings import t
    return t("done_summary", lang, done=done, skipped=skipped, failed=failed,
             bytes=human_size(nbytes), elapsed=human_secs(elapsed))


def failure_block(reasons: Iterable[tuple[str, str]], *, limit: int = 5,
                  lang: str = "en") -> str:
    """Why the files failed, appended under the summary card.

    "0 done · 1 failed" is technically true and practically useless — it is what
    made a working bot look broken. Every failed item already knows its cause, so
    this prints it: one line per distinct reason, with a count when several files
    died the same way.
    """
    order: list[str] = []
    first: dict[str, str] = {}
    count: dict[str, int] = {}
    for name, reason in reasons:
        reason = (reason or "").strip() or "Unknown error."
        if reason not in count:
            order.append(reason)
            first[reason] = name or "file"
            count[reason] = 0
        count[reason] += 1
    if not order:
        return ""
    head = "❌ <b>Fail kyun hua</b>" if lang == "hinglish" else "❌ <b>Why it failed</b>"
    rows = [head]
    for reason in order[:limit]:
        n = count[reason]
        label = esc(shorten(first[reason], 32)) if n == 1 else f"{n} files"
        rows.append(f"· {label} — {esc(reason)}")
    if len(order) > limit:
        rows.append(f"· … +{len(order) - limit} more")
    return "\n".join(rows)


def links_block(items: Iterable[dict]) -> str:
    rows = []
    for it in items:
        drive = it.get("drive") or {}
        link = drive.get("link") or ""
        name = it.get("final_name") or (it.get("tg") or {}).get("name", "file")
        if link:
            rows.append(f'• <a href="{esc(link)}">{esc(shorten(name, 40))}</a>')
    return "\n".join(rows)
