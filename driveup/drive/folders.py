"""Folder helpers used by the bot and the Studio.

`DriveClient` speaks to Google; this module holds the *policy*: which roots a
user may pick, how a path is rendered, and how the "default folder" setting is
resolved. Keeping it separate means the bot's `/folders` and the Studio tree
show exactly the same thing.
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from .client import DriveClient

ROOT_LABEL = "My Drive"


def allowed_roots(cfg) -> List[dict]:
    """The roots offered to a user, filtered by their plan features."""
    roots = [{"id": "root", "name": ROOT_LABEL, "kind": "drive"}]
    if getattr(cfg, "allow_shared_drives", True):
        roots.append({"id": "shared", "name": "Shared drives", "kind": "group"})
    return roots


def in_allowed_roots(folder_id: str, roots: Iterable[dict]) -> bool:
    """`root` is always allowed; a real id must live under a listed root."""
    ids = {r["id"] for r in roots}
    return folder_id in ids or folder_id == "root"


def pretty_path(parts: Iterable[str]) -> str:
    """`["My Drive", "Courses", "Mod 1"] -> "My Drive › Courses › Mod 1"."""
    return " › ".join(p for p in parts if p) or ROOT_LABEL


def resolve_default(user_settings: Optional[dict], global_settings: Optional[dict]) -> dict:
    """Per-user default folder wins over the global one, then My Drive."""
    u = (user_settings or {}).get("default_folder") or {}
    g = (global_settings or {}).get("default_folder") or {}
    pick = u if u.get("folder_id") else g
    if pick and pick.get("folder_id"):
        return {"folder_id": pick["folder_id"], "path": pick.get("path") or ROOT_LABEL}
    return {"folder_id": "root", "path": ROOT_LABEL}


async def folder_choices(client: DriveClient, parent: str = "root",
                         limit: int = 50) -> List[dict]:
    """Sub-folders for the bot's inline keyboard (flat, one level)."""
    rows = await client.list_folders(parent, page_size=limit)
    return [{"id": r["id"], "name": r["name"]} for r in rows][:limit]


async def describe(client: DriveClient, folder_id: str) -> dict:
    """`{id, name, path}` for a folder id, safe for `root` and shared drives."""
    if folder_id in ("", "root", None):
        return {"id": "root", "name": ROOT_LABEL, "path": ROOT_LABEL}
    try:
        path, name = await client.path_of(folder_id)
    except Exception:                                    # offline / revoked
        return {"id": folder_id, "name": "Folder", "path": ROOT_LABEL}
    return {"id": folder_id, "name": name or "Folder", "path": path or ROOT_LABEL}


async def ensure_child(client: DriveClient, parent: str, name: str) -> dict:
    """Return the folder `name` under `parent`, creating it once if needed.

    Drive happily creates duplicates, so a job that is retried after a crash
    would otherwise leave `Mod 1` and `Mod 1 (1)` behind.
    """
    found = await client.exists_named(name, parent or "root")
    if found and found.get("mimeType", "").endswith("folder"):
        return {"id": found["id"], "name": found.get("name", name), "created": False}
    made = await client.create_folder(name, parent or "root")
    return {"id": made["id"], "name": made.get("name", name), "created": True}
