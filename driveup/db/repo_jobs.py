"""Jobs, items, locks, abuse — split out of repo.py so no file is a wall of SQL.

Imported and re-exported by `driveup.db.repo`; callers keep using
`repo.<name>` exactly as before.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .sql import Database, dumps, loads


def _uid() -> str:
    return uuid.uuid4().hex[:16]


# ── jobs ─────────────────────────────────────────────────────────────────────
async def create_job(db: Database, user_id: int, *, destination: dict, naming: dict,
                     sequence_mode: str = "telegram_order", conflict_policy: str = "rename",
                     strict_order: bool = True, share_mode: str = "restricted",
                     source: Optional[dict] = None, status: str = "draft") -> str:
    jid = _uid()
    await db.x(
        "INSERT INTO jobs(id,user_id,status,destination,naming,sequence_mode,conflict_policy,"
        "strict_order,share_mode,source,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (jid, user_id, status, dumps(destination), dumps(naming), sequence_mode,
         conflict_policy, 1 if strict_order else 0, share_mode, dumps(source or {}), time.time()))
    return jid


async def get_job(db: Database, job_id: str) -> Optional[dict]:
    row = await db.one("SELECT * FROM jobs WHERE id=?", (job_id,))
    if row:
        for k in ("destination", "naming", "totals", "source", "status_message"):
            row[k] = loads(row.get(k), {})
        row["strict_order"] = bool(row["strict_order"])
    return row


async def update_job(db: Database, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    parts, params = [], []
    for k, v in fields.items():
        if k in ("destination", "naming", "totals", "source", "status_message"):
            v = dumps(v)
        elif k == "strict_order":
            v = 1 if v else 0
        parts.append(f"{k}=?")
        params.append(v)
    params.append(job_id)
    await db.x(f"UPDATE jobs SET {', '.join(parts)} WHERE id=?", params)


async def jobs_for_user(db: Database, user_id: int, limit: int = 10) -> List[dict]:
    rows = await db.q("SELECT * FROM jobs WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                      (user_id, limit))
    for r in rows:
        for k in ("destination", "naming", "totals", "source", "status_message"):
            r[k] = loads(r.get(k), {})
        r["strict_order"] = bool(r["strict_order"])
    return rows


async def running_jobs(db: Database, statuses=("queued", "running", "paused")) -> List[dict]:
    qmarks = ",".join("?" for _ in statuses)
    rows = await db.q(f"SELECT * FROM jobs WHERE status IN ({qmarks}) "
                      "ORDER BY priority DESC, created_at ASC", statuses)
    for r in rows:
        for k in ("destination", "naming", "totals", "source", "status_message"):
            r[k] = loads(r.get(k), {})
        r["strict_order"] = bool(r["strict_order"])
    return rows


async def recover_interrupted(db: Database) -> int:
    """After a crash: running → queued, mid-item states → pending (keep the session)."""
    await db.x("UPDATE jobs SET status='queued' WHERE status='running'")
    await db.x("UPDATE job_items SET state='pending' WHERE state IN ('downloading','uploading','verifying')")
    return int(await db.scalar("SELECT COUNT(*) FROM jobs WHERE status='queued'", (), 0))


async def recompute_totals(db: Database, job_id: str) -> dict:
    rows = await db.q("SELECT state, COUNT(*) c, COALESCE(SUM(bytes_done),0) b, "
                      "COALESCE(SUM(json_extract(tg,'$.size')),0) s "
                      "FROM job_items WHERE job_id=? GROUP BY state", (job_id,))
    by = {r["state"]: r for r in rows}
    total = await db.one("SELECT COUNT(*) c, COALESCE(SUM(json_extract(tg,'$.size')),0) s "
                         "FROM job_items WHERE job_id=?", (job_id,))
    totals = {
        "files": int(total["c"] or 0),
        "bytes": int(total["s"] or 0),
        "done": int(by.get("done", {}).get("c", 0)),
        "failed": int(by.get("failed", {}).get("c", 0)),
        "skipped": int(by.get("skipped", {}).get("c", 0)),
        "bytes_done": sum(int(by.get(s, {}).get("b", 0)) for s in ("done", "uploading", "verifying")),
    }
    await update_job(db, job_id, totals=totals)
    return totals


# ── job items ────────────────────────────────────────────────────────────────
async def add_item(db: Database, job_id: str, *, file_id: str, file_unique_id: str,
                   msg_id: int, size: int, mime: str, name: str, chat_id: int = 0,
                   seq_index: Optional[int] = None, caption: str = "") -> str:
    iid = _uid()
    if seq_index is None:
        seq_index = int(await db.scalar(
            "SELECT COALESCE(MAX(seq_index),0)+1 FROM job_items WHERE job_id=?", (job_id,), 1))
    tg = {"file_id": file_id, "file_unique_id": file_unique_id, "msg_id": msg_id,
          "size": size, "mime": mime, "name": name, "chat_id": chat_id, "caption": caption}
    await db.x("INSERT INTO job_items(id,job_id,seq_index,tg,final_name) VALUES(?,?,?,?,?)",
               (iid, job_id, seq_index, dumps(tg), name))
    return iid


async def get_item(db: Database, item_id: str) -> Optional[dict]:
    row = await db.one("SELECT * FROM job_items WHERE id=?", (item_id,))
    if row:
        row["tg"] = loads(row["tg"], {})
        row["drive"] = loads(row["drive"], {})
    return row


async def items_of(db: Database, job_id: str) -> List[dict]:
    rows = await db.q("SELECT * FROM job_items WHERE job_id=? ORDER BY seq_index", (job_id,))
    for r in rows:
        r["tg"] = loads(r["tg"], {})
        r["drive"] = loads(r["drive"], {})
    return rows


async def next_pending_item(db: Database, job_id: str) -> Optional[dict]:
    rows = await db.q("SELECT * FROM job_items WHERE job_id=? AND state IN "
                      "('pending','downloading','uploading','verifying') ORDER BY seq_index LIMIT 1",
                      (job_id,))
    if not rows:
        return None
    r = rows[0]
    r["tg"] = loads(r["tg"], {})
    r["drive"] = loads(r["drive"], {})
    return r


async def set_item(db: Database, item_id: str, **fields: Any) -> None:
    if not fields:
        return
    if fields.get("state") == "done":
        fields.setdefault("finished_at", time.time())
    parts, params = [], []
    for k, v in fields.items():
        if k in ("tg", "drive"):
            v = dumps(v)
        parts.append(f"{k}=?")
        params.append(v)
    params.append(item_id)
    await db.x(f"UPDATE job_items SET {', '.join(parts)} WHERE id=?", params)


async def delete_item(db: Database, item_id: str) -> None:
    await db.x("DELETE FROM job_items WHERE id=?", (item_id,))


async def reorder_items(db: Database, job_id: str, order: List[str]) -> None:
    for idx, iid in enumerate(order, 1):
        await db.x("UPDATE job_items SET seq_index=? WHERE id=? AND job_id=?", (idx, iid, job_id))


async def retry_failed(db: Database, job_id: str) -> int:
    rows = await db.q("SELECT id FROM job_items WHERE job_id=? AND state='failed'", (job_id,))
    for r in rows:
        await db.x("UPDATE job_items SET state='pending', error='', attempts=0, bytes_done=0, "
                   "drive=? WHERE id=?", (dumps({}), r["id"]))
    return len(rows)


# ── locks ────────────────────────────────────────────────────────────────────
async def lock_user(db: Database, user_id: int, *, reason: str, by_id: int, minutes: int,
                    scope: str = "all") -> dict:
    now = time.time()
    expires = now + max(1, minutes) * 60
    await db.x("INSERT INTO user_locks(user_id,reason,by_id,locked_at,expires_at,scope) "
               "VALUES(?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET reason=excluded.reason, "
               "by_id=excluded.by_id, locked_at=excluded.locked_at, "
               "expires_at=excluded.expires_at, scope=excluded.scope",
               (user_id, reason, by_id, now, expires, scope))
    return await db.one("SELECT * FROM user_locks WHERE user_id=?", (user_id,))


async def unlock_user(db: Database, user_id: int) -> bool:
    row = await db.one("SELECT user_id FROM user_locks WHERE user_id=?", (user_id,))
    await db.x("DELETE FROM user_locks WHERE user_id=?", (user_id,))
    return bool(row)


async def get_lock(db: Database, user_id: int) -> Optional[dict]:
    row = await db.one("SELECT * FROM user_locks WHERE user_id=?", (user_id,))
    if row and row["expires_at"] <= time.time():
        await db.x("DELETE FROM user_locks WHERE user_id=?", (user_id,))
        return None
    return row


async def list_locks(db: Database, include_expired: bool = False) -> List[dict]:
    if include_expired:
        return await db.q("SELECT * FROM user_locks ORDER BY expires_at DESC")
    return await db.q("SELECT * FROM user_locks WHERE expires_at>? ORDER BY expires_at",
                      (time.time(),))


# ── abuse window ─────────────────────────────────────────────────────────────
async def bump_abuse(db: Database, user_id: int, window: int) -> int:
    now = time.time()
    await db.x("INSERT INTO abuse(user_id,ts) VALUES(?,?)", (user_id, now))
    await db.x("DELETE FROM abuse WHERE ts<?", (now - window * 3,))
    return int(await db.scalar("SELECT COUNT(*) FROM abuse WHERE user_id=? AND ts>=?",
                               (user_id, now - window), 0))


