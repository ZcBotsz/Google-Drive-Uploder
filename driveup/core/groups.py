"""Split a job into sub-folders (B5 "per-file or per-group destinations").

The Studio lets a user say "put videos in `Videos/`, PDFs in `PDFs/`" or
"split by season — `Season 1/`, `Season 2/`". DriveUp keeps that decision in
the job's `destination` dict so it survives a restart:

    destination = {
        "folder_id": "root",
        "path": "My Drive",
        "split": {"mode": "type" | "date" | "custom", "rules": {...}}
    }

`plan()` is pure: it turns one item into the list of folder *names* it belongs
under. `FolderResolver` turns those names into ids, creating each folder at most
once per job (Drive happily makes `Videos` twice, which is exactly the bug a
retry would otherwise hit).
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata
from typing import Dict, List, Optional

from ..drive.client import DriveClient

TYPE_GROUPS = {
    "videos": {"mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v", "ts", "mpg", "mpeg"},
    "images": {"jpg", "jpeg", "png", "gif", "webp", "bmp", "heic", "heif", "tiff", "svg"},
    "audio": {"mp3", "m4a", "aac", "flac", "wav", "ogg", "opus", "wma"},
    "documents": {"pdf", "doc", "docx", "txt", "rtf", "odt", "md", "epub"},
    "archives": {"zip", "rar", "7z", "tar", "gz", "bz2", "xz", "iso"},
    "code": {"py", "js", "ts", "json", "html", "css", "sql", "sh", "yml", "yaml", "toml"},
}


def _ext(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def type_bucket(name: str) -> str:
    """`lecture.MP4` → `Videos`. Unknown extensions land in `Other`."""
    e = _ext(name)
    for bucket, exts in TYPE_GROUPS.items():
        if e in exts:
            return bucket.capitalize()
    return "Other"


# Order matters: the most specific season syntaxes are tried first, so
# "S02E10" is Season 2 (not "episode 10") and "3x04" is Season 3.
_SEASON_PATTERNS = [
    re.compile(r"\bS(\d{1,2})[\s._-]*E\d{1,3}\b", re.I),          # S02E10
    re.compile(r"\bSeason[\s._-]*(\d{1,2})\b", re.I),              # Season 2
    re.compile(r"\b(\d{1,2})x\d{1,3}\b", re.I),                   # 3x04
    re.compile(r"\bS(\d{1,2})\b", re.I),                           # S03
]


def season_bucket(name: str) -> str:
    """`show.S02E10.mkv` → `Season 2`. `episode 5.mp4` → `Season 1`."""
    for rx in _SEASON_PATTERNS:
        m = rx.search(name)
        if m:
            return f"Season {int(m.group(1))}"
    return "Season 1"


def safe_folder_name(raw: str, limit: int = 120) -> str:
    """Drive allows most things; slashes and control chars still break paths."""
    s = unicodedata.normalize("NFC", raw or "")
    s = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s).strip(" .")
    return (s or "Other")[:limit]


def plan(destination: Optional[dict], items: List[dict]) -> Dict[str, List[str]]:
    """`{item_id: ["Videos"]}` — the folder chain each item belongs under.

    `split.mode` may be:
      · ``type``   — Videos / Images / Audio / Documents / Archives / Code / Other
      · ``date``   — `YYYY-MM` from the item's timestamp (or today)
      · ``custom`` — first matching regex in ``rules`` wins, else ``else``
      · ``season`` — `Season N` parsed from the filename
    Items are expected to expose ``final_name`` (or ``tg.name``) and ``ts``.
    """
    split = ((destination or {}).get("split") or {})
    mode = (split.get("mode") or "none").lower()
    if mode in ("none", "", "off"):
        return {it["id"]: [] for it in items}

    rules = split.get("rules") or []
    compiled = []
    for r in rules:
        try:
            compiled.append((re.compile(r.get("match", ""), re.I),
                             safe_folder_name(r.get("folder", "Other"))))
        except re.error:
            continue
    fallback = safe_folder_name(split.get("else", "Other"))

    out: Dict[str, List[str]] = {}
    for it in items:
        name = it.get("final_name") or (it.get("tg") or {}).get("name", "")
        if mode == "type":
            chain = [type_bucket(name)]
        elif mode == "season":
            chain = [season_bucket(name)]
        elif mode == "date":
            ts = it.get("ts") or (it.get("tg") or {}).get("date") or 0
            try:
                d = dt.datetime.fromtimestamp(float(ts)) if ts else dt.datetime.now()
            except (ValueError, OSError, OverflowError):
                d = dt.datetime.now()
            chain = [d.strftime("%Y-%m")]
        elif mode == "custom":
            chain = [fallback]
            for rx, folder in compiled:
                if rx.search(name):
                    chain = [folder]
                    break
        else:
            chain = []
        out[it["id"]] = chain
    return out


class FolderResolver:
    """Cache of `path → folder id` for one job, creating folders lazily."""

    def __init__(self, client: DriveClient, root_id: str) -> None:
        self.client = client
        self.root_id = root_id or "root"
        self._ids: Dict[str, str] = {"": self.root_id}

    async def resolve(self, chain: List[str]) -> dict:
        """`["Videos", "Season 2"]` → `{"folder_id": id, "path": "My Drive › Videos › Season 2"}`."""
        if not chain:
            return {"folder_id": self.root_id, "path": ""}
        parent = self.root_id
        parts: List[str] = []
        for name in chain:
            key = "/".join(parts + [name])
            fid = self._ids.get(key)
            if not fid:
                found = await self.client.exists_named(name, parent)
                if found and str(found.get("mimeType", "")).endswith("folder"):
                    fid = found["id"]
                else:
                    fid = (await self.client.create_folder(name, parent))["id"]
                self._ids[key] = fid
            parent = fid
            parts.append(name)
        return {"folder_id": parent, "path": " › ".join(parts)}
