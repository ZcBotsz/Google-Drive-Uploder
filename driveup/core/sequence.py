"""Upload order — pure functions, unit-tested.

The order is stored as `seq_index` on every item; Strict mode uploads one by one
in that exact order, non-strict mode uploads in parallel and the numbering
prefix (from the naming template) guarantees the final order on Drive.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .naming import nat_key

MODES = ("telegram_order", "name_natural", "size_asc", "size_desc", "date", "manual")


def sort_items(items: List[dict], mode: str, manual_order: Optional[List[str]] = None) -> List[dict]:
    """Return a NEW list in the requested order. `items` are dicts with
    id, name, size, msg_id (and optional 'date')."""
    items = list(items)
    if mode == "manual" and manual_order:
        rank = {iid: n for n, iid in enumerate(manual_order)}
        return sorted(items, key=lambda it: (rank.get(it.get("id", ""), 10 ** 9), nat_key(it.get("name", ""))))
    if mode == "name_natural":
        return sorted(items, key=lambda it: nat_key(it.get("name", "")))
    if mode == "size_asc":
        return sorted(items, key=lambda it: (it.get("size", 0), nat_key(it.get("name", ""))))
    if mode == "size_desc":
        return sorted(items, key=lambda it: (-it.get("size", 0), nat_key(it.get("name", ""))))
    if mode == "date":
        return sorted(items, key=lambda it: (it.get("date", it.get("msg_id", 0)), it.get("id", "")))
    # telegram_order (and any unknown mode)
    return sorted(items, key=lambda it: (it.get("msg_id", 0), it.get("id", "")))


def apply_mode(items: List[dict], mode: str, manual_order: Optional[List[str]] = None) -> List[dict]:
    """Sorted list with fresh 1-based seq_index values (mutates copies)."""
    ordered = sort_items(items, mode, manual_order)
    out = []
    for n, it in enumerate(ordered, 1):
        c = dict(it)
        c["seq_index"] = n
        out.append(c)
    return out


def move(items: List[dict], item_id: str, to_index: int) -> List[dict]:
    """Drag-and-drop: move one id to a 0-based position."""
    ids = [it.get("id") for it in items]
    if item_id not in ids:
        return list(items)
    cur = ids.index(item_id)
    to_index = max(0, min(len(ids) - 1, to_index))
    ids.pop(cur)
    ids.insert(to_index, item_id)
    by_id: Dict[str, dict] = {it["id"]: it for it in items}
    result = [by_id[i] for i in ids]
    for n, it in enumerate(result, 1):
        it["seq_index"] = n
    return result


def move_top(items: List[dict], item_id: str) -> List[dict]:
    return move(items, item_id, 0)


def move_bottom(items: List[dict], item_id: str) -> List[dict]:
    return move(items, item_id, len(items) - 1)


def reverse(items: List[dict]) -> List[dict]:
    out = list(reversed(items))
    for n, it in enumerate(out, 1):
        it["seq_index"] = n
    return out


def parse_range(text: str) -> List[int]:
    """`100-150` or `100,102,104-106` → explicit message-id list (Flow 3)."""
    out: List[int] = []
    for chunk in (text or "").replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk[1:]:
            a, _, b = chunk.partition("-") if not chunk.startswith("-") else ("0" + chunk, "", "")
            try:
                start, end = int(a), int(b or a)
            except ValueError:
                continue
            if start > end:
                start, end = end, start
            if end - start > 5000:      # never build an absurd list
                end = start + 5000
            out.extend(range(start, end + 1))
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                pass
    # de-dupe, keep order
    seen, res = set(), []
    for n in out:
        if n not in seen:
            seen.add(n)
            res.append(n)
    return res


def parse_message_link(text: str) -> Optional[dict]:
    """`https://t.me/c/123456/100-150` or `t.me/c/123/100` → {chat, ids}."""
    raw = (text or "").strip().replace("https://", "").replace("http://", "")
    if not raw.startswith("t.me/"):
        return None
    parts = raw.split("/")
    if len(parts) < 3:
        return None
    if parts[1] == "c" and len(parts) >= 4:
        chat = int("-100" + parts[2]) if not parts[2].startswith("-100") else int(parts[2])
        ids = parse_range(parts[3])
        return {"chat_id": chat, "ids": ids} if ids else None
    if parts[1] not in ("c", "s"):
        # public channel: /<username>/<id>
        return {"username": parts[1], "ids": parse_range(parts[2])}
    return None
