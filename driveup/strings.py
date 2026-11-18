"""All user-facing text, in one table. Logic never contains a sentence.

`t(key, lang, **kw)` looks the key up in the chosen language and falls back to
English, then to the key itself (so a missing string is visible, never a crash).

Branding, welcome text, prices and support handles are *settings*, not code —
an operator edits them from the admin panel without touching this file.
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict

# ── small caps ───────────────────────────────────────────────────────────────
_SC = str.maketrans({
    "a": "ᴀ", "b": "ʙ", "c": "ᴄ", "d": "ᴅ", "e": "ᴇ", "f": "ꜰ", "g": "ɢ", "h": "ʜ",
    "i": "ɪ", "j": "ᴊ", "k": "ᴋ", "l": "ʟ", "m": "ᴍ", "n": "ɴ", "o": "ᴏ", "p": "ᴘ",
    "q": "ǫ", "r": "ʀ", "s": "ꜱ", "t": "ᴛ", "u": "ᴜ", "v": "ᴠ", "w": "ᴡ", "x": "x",
    "y": "ʏ", "z": "ᴢ",
})


def sc(text: str) -> str:
    """ꜱᴍᴀʟʟ ᴄᴀᴘꜱ — the heading style from the existing bot."""
    return text.lower().translate(_SC)


def esc(text: Any) -> str:
    """HTML-escape for Telegram parse_mode=HTML and for the web."""
    return html.escape("" if text is None else str(text), quote=False)


def attr(text: Any) -> str:
    """Escape for an HTML attribute (quotes included)."""
    return html.escape("" if text is None else str(text), quote=True)


_TAG_RE = re.compile(r"<[^>]+>")


def strip_tags(text: str) -> str:
    return _TAG_RE.sub("", text)


# ── the table ────────────────────────────────────────────────────────────────
STRINGS: Dict[str, Dict[str, str]] = {
    # start / help
    "welcome": {
        "en": ("<b>{brand}</b> (DriveUp) — Telegram → Google Drive, in the order you choose.\n\n"
               "Send me files (documents, videos, audio, photos, albums, forwarded posts) and I "
               "put a job card in front of you. From there you pick the folder, the names and the "
               "upload order — in chat or in the <b>Upload Studio</b>.\n\n"
               "First step: /connect to link your Google Drive."),
        "hinglish": ("<b>{brand}</b> (DriveUp) — Telegram se Google Drive, aapke chune hue order me.\n\n"
                     "Files bhejo (document, video, audio, photo, album, forwarded post) — main ek "
                     "<b>job card</b> bana dunga. Wahin se folder, naam aur upload order choose karo — "
                     "chat me ya <b>Upload Studio</b> me.\n\n"
                     "Pehla step: /connect se Google Drive jodo."),
    },
    "help": {
        "en": ("<b>Commands</b>\n"
               "/connect — link Google Drive   /disconnect — remove it\n"
               "/account — account + free space   /folders — recent folders\n"
               "/setfolder — default destination   /naming — filename template\n"
               "/sequence — upload order   /queue — my jobs   /history — last uploads\n"
               "/mystats — my totals   /plan — my plan and quota   /settings — preferences\n"
               "/studio — open the Upload Studio   /cancel — stop the running job\n\n"
               "Send files any time — a job card appears automatically."),
        "hinglish": ("<b>Commands</b>\n"
                     "/connect — Google Drive jodo   /disconnect — hatao\n"
                     "/account — account + free space   /folders — recent folders\n"
                     "/setfolder — default destination   /naming — filename template\n"
                     "/sequence — upload order   /queue — mere jobs   /history — pichhle uploads\n"
                     "/mystats — mere totals   /plan — plan aur quota   /settings — preferences\n"
                     "/studio — Upload Studio kholo   /cancel — chalta job roko\n\n"
                     "Kabhi bhi files bhejo — job card khud aa jayega."),
    },
    # account
    "connected": {"en": "✅ Connected as <b>{email}</b> · {free} free of {total}",
                  "hinglish": "✅ <b>{email}</b> se jud gaya · {total} me se {free} free"},
    "not_connected": {"en": "🔌 Google Drive is not connected yet. Use /connect.",
                      "hinglish": "🔌 Google Drive abhi juda nahi hai. /connect use karo."},
    "needs_reconnect": {"en": "⚠️ Google access expired. Use /connect again (one tap).",
                        "hinglish": "⚠️ Google access expire ho gaya. /connect dobara karo (ek tap)."},
    "disconnected": {"en": "🔌 Disconnected. The token was revoked at Google and deleted here.",
                     "hinglish": "🔌 Disconnect ho gaya. Token Google par revoke aur yahan delete."},
    # job card
    "job_card": {"en": "<b>📦 New job</b> — {files} file(s) · {size}\n📁 {dest}\n\nWhat next?",
                 "hinglish": "<b>📦 Naya job</b> — {files} file(s) · {size}\n📁 {dest}\n\nAage kya?"},
    "job_ready": {"en": "✅ <b>Job started</b> — {files} file(s) · {size}\n📁 {dest}",
                  "hinglish": "✅ <b>Job start</b> — {files} file(s) · {size}\n📁 {dest}"},
    "no_files": {"en": "Send me at least one file first.",
                 "hinglish": "Pehle kam se kam ek file bhejo."},
    # limits
    "too_big": {"en": "❌ <b>{name}</b> is {size} — your limit is {limit}.",
                "hinglish": "❌ <b>{name}</b> {size} ka hai — aapki limit {limit} hai."},
    "file_limit_hint": {
        "en": "Large files need a local Bot API server (TELEGRAM_API_URL + API_ID/API_HASH). "
              "Bot API cloud download stops at 20 MB.",
        "hinglish": "Badi files ke liye local Bot API server chahiye (TELEGRAM_API_URL + API_ID/API_HASH). "
                    "Bot API cloud download 20 MB par rukta hai.",
    },
    "quota_hit": {"en": "⏳ Daily quota reached ({used} of {limit}). Resets at midnight ({tz}).",
                  "hinglish": "⏳ Aaj ka quota khatam ({used} / {limit}). Raat 12 baje ({tz}) reset hoga."},
    # progress
    "progress_title": {"en": "UPLOADING TO DRIVE", "hinglish": "UPLOADING TO DRIVE"},
    "stage_tg": {"en": "Telegram → Server", "hinglish": "Telegram → Server"},
    "stage_drive": {"en": "Server → Drive", "hinglish": "Server → Drive"},
    "overall": {"en": "Overall", "hinglish": "Overall"},
    "left": {"en": "Left", "hinglish": "Bacha"},
    "retries": {"en": "Retries", "hinglish": "Retries"},
    "chunk": {"en": "Chunk", "hinglish": "Chunk"},
    "paused": {"en": "⏸ Paused", "hinglish": "⏸ Ruk gaya"},
    "resumed": {"en": "▶️ Resumed", "hinglish": "▶️ Chalu"},
    "cancelled": {"en": "🚫 Cancelled", "hinglish": "🚫 Cancel"},
    # summary
    "done_summary": {
        "en": ("🏁 <b>Upload finished</b>\n✅ {done} done · ⚠️ {skipped} skipped · ❌ {failed} failed\n"
               "📦 {bytes} uploaded in {elapsed}"),
        "hinglish": ("🏁 <b>Upload poora</b>\n✅ {done} done · ⚠️ {skipped} skipped · ❌ {failed} failed\n"
                     "📦 {bytes} upload hua {elapsed} me"),
    },
    "failed_item": {"en": "❌ <b>{name}</b>\n{reason}",
                    "hinglish": "❌ <b>{name}</b>\n{reason}"},
    # drive errors
    "drive_full": {"en": "💾 Google Drive is full — free some space or use another account.",
                   "hinglish": "💾 Google Drive bhar gaya — jagah khaali karo ya doosra account use karo."},
    "drive_daily": {"en": "📉 Drive's 750 GB/day upload cap hit. The job is paused — resume tomorrow.",
                    "hinglish": "📉 Drive ka 750 GB/day cap lag gaya. Job pause hai — kal resume karo."},
    "rate_limited": {"en": "🐢 Google is rate-limiting us. Retrying in {secs}s…",
                     "hinglish": "🐢 Google rate-limit kar raha hai. {secs}s me retry…"},
    "auth_expired": {"en": "🔑 Google access expired mid-job. Job paused — /connect and resume.",
                     "hinglish": "🔑 Job ke beech Google access expire. Job pause — /connect karke resume karo."},
    # locks
    "locked": {"en": "🔒 <b>Account locked</b>\n{reason}\nUntil: <b>{until}</b>\n\nContact: {support}",
               "hinglish": "🔒 <b>Account locked</b>\n{reason}\nKab tak: <b>{until}</b>\n\nContact: {support}"},
    "maintenance": {"en": "🛠 <b>Maintenance</b>\n{msg}",
                    "hinglish": "🛠 <b>Maintenance</b>\n{msg}"},
    "force_sub": {"en": "🔔 Join these to continue:\n{channels}\n\nThen tap “I joined”.",
                  "hinglish": "🔔 Continue karne ke liye in channels me join karo:\n{channels}\n\nPhir “I joined” dabao."},
    "feature_locked": {"en": "🔒 <b>{feature}</b> is a premium feature.\n{plan}",
                       "hinglish": "🔒 <b>{feature}</b> premium feature hai.\n{plan}"},
    "sub_ok": {"en": "✅ Thanks — you're in.", "hinglish": "✅ Shukriya — ho gaya."},
    "sub_still": {"en": "❌ Not joined yet. Join, then tap again.",
                  "hinglish": "❌ Abhi join nahi kiya. Join karke dobara dabao."},
    # buttons
    "btn_folder": {"en": "📂 Choose folder", "hinglish": "📂 Folder chuno"},
    "btn_naming": {"en": "✏️ Naming", "hinglish": "✏️ Naming"},
    "btn_sequence": {"en": "🔢 Sequence", "hinglish": "🔢 Sequence"},
    "btn_start": {"en": "🚀 Start", "hinglish": "🚀 Start"},
    "btn_studio": {"en": "🌐 Open Upload Studio", "hinglish": "🌐 Upload Studio kholo"},
    "btn_cancel": {"en": "❌ Cancel", "hinglish": "❌ Cancel"},

    "registration_closed": {"en": "🚪 Registration is closed right now.",
                            "hinglish": "🚪 Abhi registration band hai."},
    "no_files_yet": {"en": "📥 Send me a video, photo or file to start a job — "
                           "or open /studio for drag-and-drop.",
                     "hinglish": "📥 Shuru karne ke liye video, photo ya file bhejo — "
                                 "ya /studio kholo drag-and-drop ke liye."},
    "connect_first": {"en": "🔑 Connect Google Drive first: /connect",
                      "hinglish": "🔑 Pehle Google Drive connect karo: /connect"},
    "job_started": {"en": "🚀 Upload started — {files} file(s) to Drive.",
                    "hinglish": "🚀 Upload shuru — {files} file Drive par ja rahi hai."},
    "job_done_links": {"en": "🔗 <b>Your links</b>", "hinglish": "🔗 <b>Links</b>"},
    "queue_empty": {"en": "📭 Nothing in the queue.", "hinglish": "📭 Queue khaali hai."},
    "no_history": {"en": "🗂 No uploads yet.", "hinglish": "🗂 Abhi koi upload nahi hua."},
    "mystats": {
        "en": ("📊 <b>Your stats</b>\nJobs: <b>{jobs}</b>\nFiles: <b>{files}</b>\n"
               "Uploaded: <b>{bytes}</b>\nMember since: {since}"),
        "hinglish": ("📊 <b>Aapke stats</b>\nJobs: <b>{jobs}</b>\nFiles: <b>{files}</b>\n"
                     "Upload: <b>{bytes}</b>\nMember since: {since}"),
    },
    "plan_line": {"en": "💳 <b>{name}</b> — {price}\nFile limit: {file_mb} MB · "
                        "Daily: {daily_gb} GB · Parallel: {concurrent}",
                  "hinglish": "💳 <b>{name}</b> — {price}\nFile limit: {file_mb} MB · "
                              "Daily: {daily_gb} GB · Parallel: {concurrent}"},
    # generic
    "cancelled_ok": {"en": "🚫 Cancel sent.", "hinglish": "🚫 Cancel bheja."},
    "nothing_running": {"en": "Nothing is running.", "hinglish": "Kuch chal nahi raha."},
    "error": {"en": "⚠️ {msg}", "hinglish": "⚠️ {msg}"},
}

LANG_ALIASES = {"hi": "hinglish", "hinglish": "hinglish", "en": "en", "english": "en"}


def t(key: str, lang: str = "en", **kw: Any) -> str:
    """Look up a string. Unknown key → the key itself (visible, not fatal)."""
    block = STRINGS.get(key)
    if not block:
        return key
    lang = LANG_ALIASES.get((lang or "en").lower(), "en")
    text = block.get(lang) or block.get("en") or key
    if kw:
        text = text.format(**kw)
    return text


def langs() -> list[str]:
    return ["en", "hinglish"]


def missing_keys() -> list[str]:
    """Keys that lack a translation — used by the test suite."""
    out = []
    for k, block in STRINGS.items():
        for lg in langs():
            if lg not in block or not block[lg].strip():
                out.append(f"{k}:{lg}")
    return out


def unknown_placeholders() -> list[str]:
    """A {placeholder} used in a string that .format() would explode on."""
    import string
    bad = []
    for k, block in STRINGS.items():
        for lg, text in block.items():
            for _, field_name, spec, _ in string.Formatter().parse(text):
                if field_name is None:
                    continue
                if not spec and field_name not in _KNOWN_FIELDS:
                    bad.append(f"{k}:{lg}:{{{field_name}}}")
    return bad


_KNOWN_FIELDS = {
    "brand", "email", "free", "total", "files", "size", "dest", "name", "limit", "used", "tz",
    "done", "skipped", "failed", "bytes", "elapsed", "reason", "secs", "until", "support", "msg",
    "channels", "feature", "plan", "count", "pct", "speed", "eta", "order", "folder", "seq",
    "jobs", "since", "price", "file_mb", "daily_gb", "concurrent", "until",
    "reason", "support", "feature", "plan", "channels", "msg", "secs", "status",
    "count", "eta", "speed", "idx", "pct", "label", "value", "when", "role",
}
