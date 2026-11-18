"""Inline keyboards. Callback data is `v1:action:arg` and never exceeds 64 bytes.

Every callback that carries a job or item id is ownership-checked in the
middleware before the handler runs (see bot/client.py), so a crafted callback
cannot touch another user's job.
"""
from __future__ import annotations

from typing import Iterable, Optional

from .ui.buttons import auto as B
from aiogram.types import InlineKeyboardMarkup as KM

from ..strings import t
from .ui.buttons import danger, primary, success, styled

MAX_CB = 64


def cb(action: str, arg: str = "") -> str:
    data = f"v1:{action}" + (f":{arg}" if arg else "")
    return data[:MAX_CB]


def job_card_kb(job_id: str, *, has_files: bool = True, lang: str = "en",
                studio_url: str = "") -> KM:
    rows = [
        [B(text=t("btn_folder", lang), callback_data=cb("folder", job_id)),
         B(text=t("btn_naming", lang), callback_data=cb("naming", job_id))],
        [B(text=t("btn_sequence", lang), callback_data=cb("seq", job_id))],
    ]
    if has_files:
        rows.append([success(t("btn_start", lang), callback_data=cb("start", job_id))])
    if studio_url:
        rows.append([primary(t("btn_studio", lang), url=studio_url)])
    rows.append([danger(t("btn_cancel", lang), callback_data=cb("drop", job_id))])
    return KM(inline_keyboard=rows)


def progress_kb(job_id: str, *, paused: bool = False, studio_url: str = "",
                lang: str = "en") -> KM:
    rows = [[
        (success("▶️ Resume", callback_data=cb("pauseres", job_id)) if paused
         else primary("⏸ Pause", callback_data=cb("pauseres", job_id))),
        danger("❌ Cancel", callback_data=cb("cancel", job_id)),
    ]]
    if studio_url:
        rows.append([primary("🌐 Studio", url=studio_url)])
    return KM(inline_keyboard=rows)


def connect_kb(url: str, lang: str = "en") -> KM:
    return KM(inline_keyboard=[[success("🔗 Connect Google Drive", url=url)]])


def force_sub_kb(channels: Iterable[str], lang: str = "en") -> KM:
    rows = [[B(text=f"📢 {c}", url=f"https://t.me/{c.lstrip('@')}")] for c in channels if c]
    rows.append([B(text="✅ I joined", callback_data=cb("subok"))])
    return KM(inline_keyboard=rows)


def confirm_kb(action: str, arg: str = "", lang: str = "en") -> KM:
    return KM(inline_keyboard=[[
        success("✅ Yes", callback_data=cb(action, arg)),
        B(text="✖️ No", callback_data=cb("noop")),
    ]])


def queue_kb(job_id: str, status: str) -> KM:
    if status in ("running", "queued"):
        rows = [[primary("⏸ Pause", callback_data=cb("pauseres", job_id)),
                 danger("❌ Cancel", callback_data=cb("cancel", job_id))]]
    elif status == "paused":
        rows = [[success("▶️ Resume", callback_data=cb("pauseres", job_id)),
                 danger("❌ Cancel", callback_data=cb("cancel", job_id))]]
    elif status == "done":
        rows = [[B(text="↻ Retry failed", callback_data=cb("retry", job_id))]]
    else:
        rows = [[B(text="↻ Retry", callback_data=cb("retry", job_id))]]
    return KM(inline_keyboard=rows)


def naming_kb(lang: str = "en", job_id: str = "") -> KM:
    presets = ["{original}.{ext}", "{seq:03d} - {original}.{ext}",
               "Module 1 - {index:02d} {original}", "{date} {original}.{ext}"]
    rows = [[B(text=p, callback_data=cb(f"ntpl{i}", job_id))] for i, p in enumerate(presets)]
    rows.append([B(text="✍️ Custom template", callback_data=cb("ntplcustom", job_id))])
    return KM(inline_keyboard=rows)


def sequence_kb(mode: str, job_id: str = "") -> KM:
    modes = [("telegram_order", "📨 Telegram order"), ("name_natural", "🔤 Name (A→Z)"),
             ("size_asc", "📉 Size ↑"), ("size_desc", "📈 Size ↓"), ("date", "🕒 Date")]
    rows = [[(success("✅ " + label, callback_data=cb("seqmode", m + (f":{job_id}" if job_id else "")))
              if mode == m else
              B(text=label, callback_data=cb("seqmode", m + (f":{job_id}" if job_id else ""))))]
            for m, label in modes]
    rows.append([primary("🌐 Drag & drop in Studio", callback_data=cb("openstudio"))])
    return KM(inline_keyboard=rows)
