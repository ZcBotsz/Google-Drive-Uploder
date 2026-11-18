"""Typed repo helpers — one function per query the app needs."""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .sql import Database, dumps, loads


def _uid() -> str:
    return uuid.uuid4().hex[:16]


# ── users ────────────────────────────────────────────────────────────────────
async def upsert_user(db: Database, user_id: int, name: str = "", username: str = "",
                      lang: str = "") -> dict:
    now = time.time()
    row = await db.one("SELECT * FROM users WHERE user_id=?", (user_id,))
    if row is None:
        await db.x("INSERT INTO users(user_id,name,username,first_seen,last_seen,lang) VALUES(?,?,?,?,?,?)",
                   (user_id, name, username, now, now, lang or "hinglish"))
        await db.x("INSERT INTO daily_stats(day,new_users) VALUES(date('now','localtime'),1) "
                   "ON CONFLICT(day) DO UPDATE SET new_users=new_users+1")
    else:
        await db.x("UPDATE users SET name=?, username=?, last_seen=?, lang=? WHERE user_id=?",
                   (name or row["name"], username or row["username"], now, lang or row["lang"], user_id))
    got = await db.one("SELECT * FROM users WHERE user_id=?", (user_id,))
    got["settings"] = loads(got.get("settings"), {})
    return got


async def get_user(db: Database, user_id: int) -> Optional[dict]:
    row = await db.one("SELECT * FROM users WHERE user_id=?", (user_id,))
    if row:
        row["settings"] = loads(row.get("settings"), {})
    return row


async def set_user_settings(db: Database, user_id: int, patch: Dict[str, Any]) -> dict:
    row = await db.one("SELECT settings FROM users WHERE user_id=?", (user_id,))
    cur = loads(row["settings"], {}) if row else {}
    cur.update(patch)
    await db.x("UPDATE users SET settings=? WHERE user_id=?", (dumps(cur), user_id))
    return cur


async def set_setting(db: Database, user_id: int, key: str, value: Any) -> None:
    await set_user_settings(db, user_id, {key: value})


async def set_lang(db: Database, user_id: int, lang: str) -> None:
    await db.x("UPDATE users SET lang=? WHERE user_id=?", (lang, user_id))


async def count_users(db: Database, since: float = 0) -> int:
    if since:
        return int(await db.scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?", (since,), 0))
    return int(await db.scalar("SELECT COUNT(*) FROM users", (), 0))


async def list_users(db: Database, search: str = "", limit: int = 20, offset: int = 0,
                     order: str = "last_seen") -> List[dict]:
    order = order if order in ("last_seen", "first_seen", "user_id") else "last_seen"
    where, params = "", []
    if search:
        where = "WHERE CAST(user_id AS TEXT) LIKE ? OR name LIKE ? OR username LIKE ?"
        like = f"%{search.lstrip('@')}%"
        params = [like, like, like]
    rows = await db.q(f"SELECT * FROM users {where} ORDER BY {order} DESC LIMIT ? OFFSET ?",
                      (*params, limit, offset))
    for r in rows:
        r["settings"] = loads(r.get("settings"), {})
    return rows


# ── roles ────────────────────────────────────────────────────────────────────
async def role_of(db: Database, user_id: int, owner_id: int = 0) -> str:
    if owner_id and user_id == owner_id:
        return "owner"
    row = await db.one("SELECT role FROM admins WHERE user_id=?", (user_id,))
    if row:
        return row["role"] or "admin"
    return "user"


async def is_admin(db: Database, user_id: int, owner_id: int = 0) -> bool:
    return (await role_of(db, user_id, owner_id)) in ("owner", "admin", "support")


async def all_rows(db: Database, sql: str, params: tuple = ()) -> List[dict]:
    return await db.all(sql, params)


async def add_admin(db: Database, user_id: int, role: str = "admin") -> None:
    await db.x("INSERT INTO admins(user_id,role,added_at) VALUES(?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET role=excluded.role", (user_id, role, time.time()))
    await db.x("UPDATE users SET role=? WHERE user_id=?", (role, user_id))


async def remove_admin(db: Database, user_id: int) -> None:
    await db.x("DELETE FROM admins WHERE user_id=?", (user_id,))
    await db.x("UPDATE users SET role='user' WHERE user_id=?", (user_id,))


async def list_admins(db: Database) -> List[dict]:
    return await db.q("SELECT * FROM admins ORDER BY added_at")


# ── drive accounts ───────────────────────────────────────────────────────────
async def ensure_user(db: Database, user_id: int, name: str = "", username: str = "") -> None:
    """Every row that references a user must be able to create it first."""
    row = await db.one("SELECT user_id FROM users WHERE user_id=?", (user_id,))
    if not row:
        await upsert_user(db, user_id, name, username)


async def save_drive_account(db: Database, user_id: int, *, email: str, enc_refresh: str,
                             access: str, expiry: float, scope: str, scope_mode: str,
                             google_sub: str = "", quota_limit: int = 0,
                             quota_used: int = 0) -> None:
    await ensure_user(db, user_id)
    await db.x(
        "INSERT INTO drive_accounts(user_id,google_sub,email,enc_refresh_token,access_token,"
        "access_expiry,scope,scope_mode,quota_limit,quota_used,connected_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET "
        "email=excluded.email, enc_refresh_token=excluded.enc_refresh_token, "
        "access_token=excluded.access_token, access_expiry=excluded.access_expiry, "
        "scope=excluded.scope, scope_mode=excluded.scope_mode, needs_reconnect=0, "
        "quota_limit=excluded.quota_limit, quota_used=excluded.quota_used, "
        "google_sub=CASE WHEN excluded.google_sub<>'' THEN excluded.google_sub ELSE google_sub END",
        (user_id, google_sub, email, enc_refresh, access, expiry, scope, scope_mode,
         quota_limit, quota_used, time.time()))


async def get_drive_account(db: Database, user_id: int) -> Optional[dict]:
    return await db.one("SELECT * FROM drive_accounts WHERE user_id=?", (user_id,))


async def drop_drive_account(db: Database, user_id: int) -> None:
    await db.x("DELETE FROM drive_accounts WHERE user_id=?", (user_id,))


async def mark_needs_reconnect(db: Database, user_id: int, flag: bool = True) -> None:
    await db.x("UPDATE drive_accounts SET needs_reconnect=? WHERE user_id=?",
               (1 if flag else 0, user_id))



# ── split modules, re-exported ──────────────────────────────────────────────
# Everything below used to live in this file. `repo.get_job(...)` still works;
# the SQL just lives in smaller, readable modules now.
from .repo_jobs import (                                   # noqa: E402,F401
    create_job, get_job, update_job, jobs_for_user,
    running_jobs, recover_interrupted, recompute_totals, add_item,
    get_item, items_of, next_pending_item, set_item,
    delete_item, reorder_items, retry_failed, lock_user,
    unlock_user, get_lock, list_locks, bump_abuse,
)
from .repo_stats import (                                  # noqa: E402,F401
    upsert_plan, seed_plans, get_plan, all_plans,
    effective_plan, grant_plan, revoke_plan, expiring_plans,
    quota_used, add_quota, audit, list_audit,
    log_error, recent_errors, get_global, set_global,
    create_session, get_session, delete_session, touch_session,
    kill_user_sessions, create_state, take_state, save_preset,
    list_presets, delete_preset, add_favourite, list_favourites,
    delete_favourite, remember_file, seen, mark_seen,
    daily_rows, user_stats, global_stats,
)


# ── custom upload thumbnails ───────────────────────────────────────────────
async def set_user_thumb(db: Database, user_id: int, jpeg: bytes) -> bool:
    """Store (or clear, with b"") a user's thumbnail. Returns True if it existed."""
    import time as _t
    had = await db.one("SELECT 1 AS x FROM user_thumbs WHERE user_id=?", (user_id,))
    if not jpeg:
        await db.x("DELETE FROM user_thumbs WHERE user_id=?", (user_id,))
        return bool(had)
    await db.x("INSERT INTO user_thumbs(user_id,jpeg,saved_at) VALUES(?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET jpeg=excluded.jpeg, "
               "saved_at=excluded.saved_at", (user_id, jpeg, _t.time()))
    return True


async def get_user_thumb(db: Database, user_id: int) -> bytes:
    row = await db.one("SELECT jpeg FROM user_thumbs WHERE user_id=?", (user_id,))
    return bytes(row["jpeg"]) if row and row.get("jpeg") else b""
