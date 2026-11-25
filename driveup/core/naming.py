"""Filename engine — pure functions, no I/O, fully unit-tested.

A template is a string with `{placeholders}`. Rendering is total: an unknown
placeholder becomes an empty string, a missing value never raises. That is
deliberate — a user typo must not kill a 200-file job.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import string
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional

# Drive allows 255 *bytes* per name, not characters.
MAX_NAME_BYTES = 255
ILLEGAL = '<>:"/\\|?*'
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_WS = re.compile(r"\s+")
_EMOJI = re.compile(
    "[" "\U0001F300-\U0001FAFF" "\U00002600-\U000027BF" "\U0001F000-\U0001F2FF"
    "\U0000FE00-\U0000FE0F" "\U0001F1E6-\U0001F1FF" "\U00002B00-\U00002BFF" "]+",
    flags=re.UNICODE)
_KNOWN = {"original", "ext", "seq", "index", "total", "date", "time", "datetime", "size",
          "type", "duration", "resolution", "caption", "sender", "chat", "message_id", "folder"}


# ── natural sort (the zip's _nat_sort_key, kept because it is right) ─────────
SEQUENCE_MODES = ["telegram_order", "name_natural", "size_asc", "size_desc",
                  "date", "manual"]


def nat_key(text: str) -> list:
    """`2 < 10` — split digits out so numeric runs compare as numbers."""
    out: list = []
    for part in re.split(r"(\d+)", text or ""):
        if not part:
            continue
        out.append(int(part) if part.isdigit() else part.lower())
    return out


def natural_sorted(items: Iterable[Any], key=lambda x: x) -> list:
    return sorted(items, key=lambda x: nat_key(str(key(x))))


# ── sanitising ──────────────────────────────────────────────────────────────
def strip_emoji(text: str) -> str:
    return _EMOJI.sub("", text or "")


def sanitize(name: str, *, keep_emoji: bool = False, space: str = " ") -> str:
    """Make a string safe as a Drive filename (not length-limited here)."""
    name = unicodedata.normalize("NFC", name or "")
    if not keep_emoji:
        name = strip_emoji(name)
    name = _CONTROL.sub("", name)
    name = name.replace("／", "-").replace("＼", "-")
    for ch in ILLEGAL:
        name = name.replace(ch, "-")
    name = name.replace("..", ".")             # no path traversal shapes
    if space != " ":
        name = name.replace(" ", space)
    name = _WS.sub(space or " ", name).strip()
    name = name.strip(". ")
    return name


def byte_len(text: str) -> int:
    return len(text.encode("utf-8"))


def truncate_bytes(text: str, limit: int) -> str:
    """Trim to `limit` bytes without splitting a multi-byte character."""
    raw = text.encode("utf-8")
    if len(raw) <= limit:
        return text
    return raw[:limit].decode("utf-8", "ignore")


def fit_name(stem: str, ext: str, limit: int = MAX_NAME_BYTES) -> str:
    """Trim the stem (never the extension) so the whole name fits in `limit` bytes."""
    ext = (("." + ext) if ext and not ext.startswith(".") else ext) or ""
    while byte_len(ext) >= limit and ext:
        ext = ext[:-1]
    room = limit - byte_len(ext)
    return (truncate_bytes(stem, max(1, room)).rstrip(". ") or "file") + ext


def split_ext(name: str) -> tuple[str, str]:
    stem, ext = os.path.splitext(name or "")
    if stem in ("", "."):
        return name or "file", ""
    return stem, ext.lstrip(".")


# ── extension inference ─────────────────────────────────────────────────────
_SIGNATURES = [
    (b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"GIF87a", "gif"),
    (b"GIF89a", "gif"), (b"%PDF-", "pdf"), (b"PK\x03\x04", "zip"), (b"\x1f\x8b", "gz"),
    (b"7z\xbc\xaf\x27\x1c", "7z"), (b"Rar!\x1a\x07", "rar"), (b"ID3", "mp3"),
    (b"fLaC", "flac"), (b"OggS", "ogg"), (b"\x00\x00\x00\x18ftyp", "mp4"),
    (b"\x00\x00\x00\x20ftyp", "mp4"), (b"\x1aE\xdf\xa3", "webm"), (b"BM", "bmp"),
]

_MIME_EXT = {
    "video/mp4": "mp4", "video/x-matroska": "mkv", "video/webm": "webm",
    "video/quicktime": "mov", "video/x-msvideo": "avi", "audio/mpeg": "mp3",
    "audio/mp4": "m4a", "audio/ogg": "ogg", "audio/flac": "flac", "audio/wav": "wav",
    "image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
    "application/pdf": "pdf", "application/zip": "zip", "application/x-7z-compressed": "7z",
    "application/x-rar-compressed": "rar", "application/epub+zip": "epub",
    "text/plain": "txt", "text/csv": "csv", "application/json": "json",
}


def ext_from_mime(mime: str) -> str:
    return _MIME_EXT.get((mime or "").lower().split(";")[0].strip(), "")


def sniff_ext(head: bytes) -> str:
    for sig, ext in _SIGNATURES:
        if head.startswith(sig):
            if ext == "mp4" and len(head) >= 12:
                return "mp4"
            return ext
    return ""


def infer_ext(*, claimed: str, mime: str, head: bytes) -> str:
    """Best extension, in order of trust: magic bytes → mime → what Telegram said."""
    sniffed = sniff_ext(head or b"")
    if sniffed:
        return sniffed
    from_mime = ext_from_mime(mime)
    if from_mime:
        return from_mime
    return (claimed or "").lstrip(".").lower()


# ── options ─────────────────────────────────────────────────────────────────
@dataclass
class NamingOptions:
    template: str = "{original}.{ext}"
    start: int = 1
    step: int = 1
    prefix: str = ""
    suffix: str = ""
    find: str = ""
    replace: str = ""
    regex: bool = False
    case: str = "keep"           # keep | title | lower | upper
    strip_emoji: bool = True
    space: str = " "             # " " | "_" | "-" | "" (collapse only)
    max_bytes: int = MAX_NAME_BYTES
    ext_rule: str = "keep"       # keep | force | infer
    force_ext: str = ""
    keep_emoji: bool = False
    tag: str = ""                # admin global tag, e.g. "@mychannel"
    tag_enabled: bool = False
    caption_first_line: bool = False
    timezone: str = "Asia/Kolkata"

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        return d

    @classmethod
    def from_dict(cls, d: Optional[dict]) -> "NamingOptions":
        d = d or {}
        known = {f for f in cls.__dataclass_fields__}          # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ── rendering ───────────────────────────────────────────────────────────────
def _fmt(value: Any, spec: str) -> str:
    if not spec:
        return str(value)
    try:
        if isinstance(value, (int, float)):
            return format(value, spec)
        if isinstance(value, str) and spec in ("s", ""):
            return value
    except (ValueError, TypeError):
        return str(value)
    return str(value)


def render_template(template: str, values: Dict[str, Any]) -> str:
    """Fill {placeholders} without ever raising. Unknown → empty string."""
    out: List[str] = []
    try:
        parts = list(string.Formatter().parse(template or ""))
    except ValueError:
        return template or ""
    for literal, field_name, spec, _conv in parts:
        out.append(literal)
        if field_name is None:
            continue
        name = field_name.split(".")[0].split("[")[0]
        if name not in values:
            continue
        out.append(_fmt(values[name], spec))
    return "".join(out)


MAX_PATTERN = 200
MAX_SUBJECT = 300


def apply_transform(text: str, opt: NamingOptions) -> str:
    """Find & replace. Plain by default, regex on request.

    ReDoS guard: a user regex runs against a bounded subject (300 chars) and the
    pattern itself is length-capped, so the worst case stays small. `re.sub` has
    no timeout parameter — pretending it does silently disabled the feature.
    """
    if opt.find:
        try:
            if opt.regex:
                if len(opt.find) <= MAX_PATTERN:
                    text = re.sub(opt.find, opt.replace, text[:MAX_SUBJECT])
            else:
                text = text.replace(opt.find, opt.replace)
        except (re.error, TypeError, RecursionError):
            pass
    if opt.case == "title":
        text = text.title()
    elif opt.case == "lower":
        text = text.lower()
    elif opt.case == "upper":
        text = text.upper()
    return text


def build_name(*, original: str, mime: str, seq: int, index: int, total: int,
               opt: NamingOptions, head: bytes = b"", size: int = 0,
               when: Optional[_dt.datetime] = None, caption: str = "", sender: str = "",
               chat: str = "", message_id: int = 0, folder: str = "",
               duration: str = "", resolution: str = "") -> str:
    """The final Drive filename for one item."""
    when = when or _dt.datetime.now()
    stem, ext = split_ext(original or "file")

    if opt.ext_rule == "force":
        ext = (opt.force_ext or ext).lstrip(".")
    elif opt.ext_rule == "infer":
        ext = infer_ext(claimed=ext, mime=mime, head=head) or ext

    values: Dict[str, Any] = {
        "original": stem,
        "ext": ext,
        "seq": seq,
        "index": index,
        "total": total,
        "date": when.strftime("%Y-%m-%d"),
        "time": when.strftime("%H-%M-%S"),
        "datetime": when.strftime("%Y-%m-%d %H-%M-%S"),
        "size": human_size(size),
        "type": "video" if (mime or "").startswith("video/") else "document",
        "duration": duration,
        "resolution": resolution,
        "caption": (caption or "").splitlines()[0] if (opt.caption_first_line and caption) else "",
        "sender": sender,
        "chat": chat,
        "message_id": message_id,
        "folder": folder,
    }

    text = render_template(opt.template, values)
    text = apply_transform(text, opt)

    # the template may or may not include the extension; make sure it is there
    base_ext = "." + ext if ext else ""
    if base_ext and not text.lower().endswith(base_ext.lower()):
        text = f"{text}{base_ext}"

    stem2, ext2 = split_ext(text)
    stem2 = sanitize(stem2, keep_emoji=opt.keep_emoji, space=opt.space)
    if opt.tag_enabled and opt.tag:
        tag = sanitize(opt.tag.strip(), keep_emoji=True)
        stem2 = f"{stem2} {tag}".strip()
    joined = (opt.prefix + stem2 + opt.suffix)
    joined = sanitize(joined, keep_emoji=opt.keep_emoji, space=opt.space)
    return fit_name(joined, ext2.lstrip("."), opt.max_bytes)


def human_size(n: float) -> str:
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.2f} {unit}"
        n /= 1024
    return f"{n:.2f} TB"


def dedupe_names(names: List[str], policy: str = "rename") -> Dict[int, str]:
    """Resolve duplicate names inside one job.

    policy: rename → `name (1).ext`, skip → '' (caller skips), overwrite → keep.
    Returns {index: final_name}; a skipped index maps to ''.
    """
    out: Dict[int, str] = {}
    seen: Dict[str, int] = {}
    for i, name in enumerate(names):
        key = name.lower()
        if key not in seen:
            seen[key] = 1
            out[i] = name
            continue
        if policy == "skip":
            out[i] = ""
            continue
        if policy == "overwrite":
            out[i] = name
            continue
        n = seen[key]
        stem, ext = split_ext(name)
        while True:
            candidate = fit_name(f"{stem} ({n})", ext)
            if candidate.lower() not in seen:
                seen[key] = n + 1
                seen[candidate.lower()] = 1
                out[i] = candidate
                break
            n += 1
    return out


def dedupe_job_names(names: List[str], items: List[dict], job: dict, policy: str = "rename") -> Dict[int, str]:
    """For a manual tree, conflicts only occur among files in the same folder."""
    split = ((job.get("destination") or {}).get("split") or {})
    if split.get("mode") != "tree":
        return dedupe_names(names, policy)
    buckets = {}
    for i, item in enumerate(items):
        fid = (split.get("existing_plan") or {}).get(item["id"])
        root = (job.get("destination") or {}).get("folder_id") or "root"
        key = (("\x00existing", fid) if fid and fid != root else
               tuple((split.get("plan") or {}).get(item["id"], [])))
        buckets.setdefault(key, []).append(i)
    result = {}
    for indexes in buckets.values():
        resolved = dedupe_names([names[i] for i in indexes], policy)
        for local, index in enumerate(indexes):
            result[index] = resolved[local]
    return result


def preview_rows(items: List[dict], opt: NamingOptions, start: int = 1) -> List[dict]:
    """What the Studio's step-3 table shows: original → new (+ conflict flag)."""
    total = len(items)
    names: List[str] = []
    for i, it in enumerate(items, 1):
        names.append(build_name(original=it.get("name", "file"), mime=it.get("mime", ""),
                                seq=start + (i - 1) * opt.step, index=i, total=total, opt=opt,
                                size=it.get("size", 0), caption=it.get("caption", "")))
    # Optional folder keys let manual-tree previews match per-folder conflicts.
    buckets = {}
    for i, it in enumerate(items):
        buckets.setdefault(tuple(it.get("folder_key") or []), []).append(i)
    resolved = {}
    for indexes in buckets.values():
        local = dedupe_names([names[i] for i in indexes], "rename")
        for j, index in enumerate(indexes):
            resolved[index] = local[j]
    seen = set()
    rows = []
    for i, (it, name) in enumerate(zip(items, names), 1):
        final = resolved.get(i - 1) or name
        key = (tuple(it.get("folder_key") or []), name.lower())
        dup = key in seen
        seen.add(key)
        rows.append({"index": i, "original": it.get("name", ""), "final": final,
                     "size": it.get("size", 0), "duplicate": dup,
                     "truncated": byte_len(final) >= opt.max_bytes})
    return rows
