"""Plans, quotas, settings, sessions, presets, stats — split out of repo.py so no file is a wall of SQL.

Imported and re-exported by `driveup.db.repo`; callers keep using
`repo.<name>` exactly as before.
"""
from __future__ import annotations

import time
import uuid
from typing import Any, Dict, List, Optional

from .sql import Database, dumps, loads
from ..config import BRAND_NAME


def _uid() -> str:
    return uuid.uuid4().hex[:16]


# ── plans / quotas ───────────────────────────────────────────────────────────
async def upsert_plan(db: Database, plan_id: str, *, name: str, price_text: str,
                      duration_days: int, max_file_mb: int, daily_gb: float, concurrent: int,
                      max_files_job: int, features: List[str], sort: int = 0) -> None:
    await db.x("INSERT INTO plans(id,name,price_text,duration_days,max_file_mb,daily_gb,concurrent,"
               "max_files_job,features,sort) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET "
               "name=excluded.name, price_text=excluded.price_text, duration_days=excluded.duration_days, "
               "max_file_mb=excluded.max_file_mb, daily_gb=excluded.daily_gb, "
               "concurrent=excluded.concurrent, max_files_job=excluded.max_files_job, "
               "features=excluded.features, sort=excluded.sort",
               (plan_id, name, price_text, duration_days, max_file_mb, daily_gb, concurrent,
                max_files_job, dumps(features), sort))


async def seed_plans(db: Database, cfg=None) -> None:
    """Idempotent defaults — an operator can edit them in the admin panel.

    Per-file ceilings are seeded from what this deployment can really fetch
    (20 MB on the cloud Bot API, 2000 MB behind a local server) instead of a
    flat 2000 MB that Telegram will always refuse.
    """
    if await get_plan(db, "free"):
        return
    from ..config import effective_max_file_mb
    cap = effective_max_file_mb(cfg) if cfg else 2000
    big = ["large_files_2gb"] if cap > 20 else []
    await upsert_plan(db, "free", name="Free", price_text="—", duration_days=0,
                           max_file_mb=min(cap, getattr(cfg, 'default_max_file_mb', 2000)),
                           daily_gb=getattr(cfg, 'default_daily_gb', 10),
                           concurrent=1, max_files_job=200,
                           features=["web_studio", "custom_naming"], sort=1)
    # Keep legacy plan IDs for existing admin grant flows, while Max is the
    # sole advertised paid plan. Legacy tiers are not shown in the user UI.
    await upsert_plan(db, "pro", name="Pro (legacy)", price_text="Legacy", duration_days=30,
                           max_file_mb=cap, daily_gb=200, concurrent=2, max_files_job=1000,
                           features=["web_studio", "custom_naming", "bulk_jobs",
                                     "shared_drives"] + big, sort=9)
    await upsert_plan(db, "business", name="Business (legacy)", price_text="Legacy",
                           duration_days=30, max_file_mb=cap, daily_gb=750, concurrent=4,
                           max_files_job=5000,
                           features=["web_studio", "custom_naming", "bulk_jobs",
                                     "shared_drives", "presets"] + big, sort=10)
    await upsert_plan(db, "max", name="Max", price_text="₹499 / month",
                           duration_days=30, max_file_mb=cap, daily_gb=750, concurrent=4,
                           max_files_job=5000,
                           features=["web_studio", "custom_naming", "bulk_jobs",
                                     "shared_drives", "presets"] + big, sort=2)



async def get_plan(db: Database, plan_id: str) -> Optional[dict]:
    row = await db.one("SELECT * FROM plans WHERE id=?", (plan_id,))
    if row:
        row["features"] = loads(row.get("features"), [])
    return row


async def all_plans(db: Database) -> List[dict]:
    rows = await db.q("SELECT * FROM plans ORDER BY sort, id")
    for r in rows:
        r["features"] = loads(r.get("features"), [])
    return rows


async def effective_plan(db: Database, user_id: int) -> dict:
    """The plan this user is entitled to right now (grant → else free)."""
    grant = await db.one("SELECT * FROM plan_grants WHERE user_id=?", (user_id,))
    if grant:
        if grant["expires_at"] > time.time():
            plan = await get_plan(db, grant["plan_id"])
            if plan:
                plan["expires_at"] = grant["expires_at"]
                return plan
        else:
            await db.x("DELETE FROM plan_grants WHERE user_id=?", (user_id,))
    plan = await get_plan(db, "free")
    if not plan:
        plan = {"id": "free", "name": "Free", "max_file_mb": 2000, "daily_gb": 20.0,
                "concurrent": 1, "max_files_job": 200, "features": [], "price_text": ""}
    return plan


async def grant_plan(db: Database, user_id: int, plan_id: str, days: int) -> dict:
    now = time.time()
    expires = now + max(1, days) * 86400
    await db.x("INSERT INTO plan_grants(user_id,plan_id,granted_at,expires_at) VALUES(?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET plan_id=excluded.plan_id, "
               "granted_at=excluded.granted_at, expires_at=excluded.expires_at",
               (user_id, plan_id, now, expires))
    return {"user_id": user_id, "plan_id": plan_id, "expires_at": expires}


async def revoke_plan(db: Database, user_id: int) -> bool:
    row = await db.one("SELECT user_id FROM plan_grants WHERE user_id=?", (user_id,))
    await db.x("DELETE FROM plan_grants WHERE user_id=?", (user_id,))
    return bool(row)


async def expiring_plans(db: Database, days: int = 7) -> List[dict]:
    return await db.q("SELECT g.*, p.name FROM plan_grants g LEFT JOIN plans p ON p.id=g.plan_id "
                      "WHERE g.expires_at BETWEEN ? AND ? ORDER BY g.expires_at",
                      (time.time(), time.time() + days * 86400))


async def quota_used(db: Database, user_id: int) -> dict:
    day = time.strftime("%Y-%m-%d")
    row = await db.one("SELECT * FROM quota_usage WHERE user_id=? AND day=?", (user_id, day))
    return row or {"user_id": user_id, "day": day, "bytes": 0, "files": 0}


async def add_quota(db: Database, user_id: int, nbytes: int, files: int = 1) -> None:
    day = time.strftime("%Y-%m-%d")
    await db.x("INSERT INTO quota_usage(user_id,day,bytes,files) VALUES(?,?,?,?) ON CONFLICT(user_id,day) "
               "DO UPDATE SET bytes=bytes+excluded.bytes, files=files+excluded.files",
               (user_id, day, nbytes, files))
    await db.x("INSERT INTO daily_stats(day,uploads,bytes) VALUES(date('now','localtime'),?,?) "
               "ON CONFLICT(day) DO UPDATE SET uploads=uploads+1, bytes=bytes+?",
               (files, nbytes, nbytes))


# ── audit / errors ───────────────────────────────────────────────────────────
async def audit(db: Database, actor: int, action: str, target: str = "", detail: Any = "",
                ip: str = "") -> None:
    await db.x("INSERT INTO audit(actor,action,target,detail,ip,ts) VALUES(?,?,?,?,?,?)",
               (actor, action, str(target), dumps(detail) if not isinstance(detail, str) else detail,
                ip, time.time()))


async def list_audit(db: Database, limit: int = 50, search: str = "") -> List[dict]:
    if search:
        like = f"%{search}%"
        return await db.q("SELECT * FROM audit WHERE action LIKE ? OR target LIKE ? OR detail LIKE ? "
                          "ORDER BY ts DESC LIMIT ?", (like, like, like, limit))
    return await db.q("SELECT * FROM audit ORDER BY ts DESC LIMIT ?", (limit,))


async def log_error(db: Database, where: str, detail: str) -> None:
    await db.x("INSERT INTO error_log(where_,detail,ts) VALUES(?,?,?)",
               (where, detail[:2000], time.time()))


async def recent_errors(db: Database, limit: int = 50) -> List[dict]:
    return await db.q("SELECT * FROM error_log ORDER BY ts DESC LIMIT ?", (limit,))


# ── settings_global ──────────────────────────────────────────────────────────
DEFAULT_GLOBAL = {
    "maintenance": False,
    "maintenance_msg": "🚀 ZC Drive Uploader is being upgraded. Back in a few minutes.",
    "registration": True,
    "welcome_text": "",
    "filename_tag": "",
    "filename_tag_enabled": False,
    "progress_style": "glow",
    "force_sub_channels": "",
    "log_channel_id": "",
    "dump_channel_id": "",
    "lock_channel_id": "",
    "support_contact": "@ZCQXx",
    "brand_name": BRAND_NAME,
    "plan_price_text": "Max · ₹499 / month",
    "default_max_file_mb": 2000,
    "allowed_types": "",
    "chunk_mb": 8,
    "animated_emoji": True,
    "features": {"web_studio": True, "bulk_jobs": True, "shared_drives": True,
                 "large_files_2gb": True, "custom_naming": True, "presets": True},
}


async def get_global(db: Database) -> dict:
    rows = await db.q("SELECT key,value FROM settings_global")
    out = dict(DEFAULT_GLOBAL)
    for r in rows:
        try:
            out[r["key"]] = loads(r["value"], r["value"])
        except Exception:
            out[r["key"]] = r["value"]
    # features merge so new flags appear without a migration
    feats = dict(DEFAULT_GLOBAL["features"])
    if isinstance(out.get("features"), dict):
        feats.update(out["features"])
    out["features"] = feats
    return out


async def set_global(db: Database, patch: Dict[str, Any]) -> dict:
    for k, v in patch.items():
        await db.x("INSERT INTO settings_global(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE "
                   "SET value=excluded.value", (k, dumps(v)))
    return await get_global(db)


# ── sessions ─────────────────────────────────────────────────────────────────
async def create_session(db: Database, sid: str, user_id: int, ttl: int, ip: str = "",
                         ua: str = "") -> None:
    now = time.time()
    await db.x("INSERT INTO sessions(sid,user_id,created_at,last_seen,expires_at,ip,ua) "
               "VALUES(?,?,?,?,?,?,?)", (sid, user_id, now, now, now + ttl, ip, ua[:200]))


async def get_session(db: Database, sid: str) -> Optional[dict]:
    row = await db.one("SELECT * FROM sessions WHERE sid=?", (sid,))
    if not row:
        return None
    if row["expires_at"] <= time.time():
        await db.x("DELETE FROM sessions WHERE sid=?", (sid,))
        return None
    return row


async def delete_session(db: Database, sid: str) -> None:
    await db.x("DELETE FROM sessions WHERE sid=?", (sid,))


async def touch_session(db: Database, sid: str, extend: int = 0) -> None:
    if extend:
        await db.x("UPDATE sessions SET last_seen=?, expires_at=expires_at+? WHERE sid=?",
                   (time.time(), extend, sid))
    else:
        await db.x("UPDATE sessions SET last_seen=? WHERE sid=?", (time.time(), sid))


async def kill_user_sessions(db: Database, user_id: int) -> int:
    n = await db.scalar("SELECT COUNT(*) FROM sessions WHERE user_id=?", (user_id,), 0)
    await db.x("DELETE FROM sessions WHERE user_id=?", (user_id,))
    return int(n)


# ── oauth states ─────────────────────────────────────────────────────────────
async def create_state(db: Database, state: str, user_id: int, verifier: str,
                       purpose: str = "connect", ttl: int = 600) -> None:
    now = time.time()
    await db.x("INSERT INTO oauth_states(state,user_id,code_verifier,purpose,created_at,expires_at) "
               "VALUES(?,?,?,?,?,?)", (state, user_id, verifier, purpose, now, now + ttl))


async def take_state(db: Database, state: str) -> Optional[dict]:
    row = await db.one("SELECT * FROM oauth_states WHERE state=?", (state,))
    if not row:
        return None
    await db.x("DELETE FROM oauth_states WHERE state=?", (state,))
    if row["expires_at"] <= time.time():
        return None
    return row


# ── presets / favourites ─────────────────────────────────────────────────────
async def save_preset(db: Database, user_id: int, name: str, config: dict) -> str:
    pid = _uid()
    await db.x("INSERT INTO presets(id,user_id,name,config,created_at) VALUES(?,?,?,?,?)",
               (pid, user_id, name, dumps(config), time.time()))
    return pid


async def list_presets(db: Database, user_id: int) -> List[dict]:
    rows = await db.q("SELECT * FROM presets WHERE user_id=? ORDER BY created_at DESC", (user_id,))
    for r in rows:
        r["config"] = loads(r.get("config"), {})
    return rows


async def delete_preset(db: Database, user_id: int, preset_id: str) -> bool:
    row = await db.one("SELECT id FROM presets WHERE id=? AND user_id=?", (preset_id, user_id))
    await db.x("DELETE FROM presets WHERE id=? AND user_id=?", (preset_id, user_id))
    return bool(row)


async def add_favourite(db: Database, user_id: int, folder_id: str, label: str = "") -> None:
    await db.x("INSERT INTO favourites(user_id,folder_id,label,created_at) VALUES(?,?,?,?) "
               "ON CONFLICT(user_id,folder_id) DO UPDATE SET label=excluded.label",
               (user_id, folder_id, label, time.time()))


async def list_favourites(db: Database, user_id: int) -> List[dict]:
    return await db.q("SELECT * FROM favourites WHERE user_id=? ORDER BY created_at DESC", (user_id,))


async def delete_favourite(db: Database, user_id: int, folder_id: str) -> bool:
    row = await db.one("SELECT folder_id FROM favourites WHERE user_id=? AND folder_id=?",
                       (user_id, folder_id))
    await db.x("DELETE FROM favourites WHERE user_id=? AND folder_id=?", (user_id, folder_id))
    return bool(row)


# ── tg file cache (search / dedupe) ──────────────────────────────────────────
async def remember_file(db: Database, *, file_unique_id: str, chat_id: int, message_id: int,
                        file_id: str, name: str, size: int, mime: str) -> None:
    await db.x("INSERT INTO tg_files(file_unique_id,chat_id,message_id,file_id,name,size,mime,created_at)"
               " VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(file_unique_id) DO UPDATE SET "
               "file_id=excluded.file_id, name=excluded.name, size=excluded.size",
               (file_unique_id, chat_id, message_id, file_id, name, size, mime, time.time()))


async def seen(db: Database, chat_id: int, message_id: int) -> bool:
    row = await db.one("SELECT 1 AS x FROM seen_messages WHERE chat_id=? AND message_id=?",
                       (chat_id, message_id))
    return bool(row)


async def mark_seen(db: Database, chat_id: int, message_id: int, user_id: int) -> None:
    await db.x("INSERT OR IGNORE INTO seen_messages(chat_id,message_id,user_id,created_at) "
               "VALUES(?,?,?,?)", (chat_id, message_id, user_id, time.time()))


# ── stats ────────────────────────────────────────────────────────────────────
async def daily_rows(db: Database, days: int = 14) -> List[dict]:
    return await db.q("SELECT * FROM daily_stats ORDER BY day DESC LIMIT ?", (days,))


async def user_stats(db: Database, user_id: int) -> dict:
    row = await db.one(
        "SELECT COUNT(*) files, COALESCE(SUM(json_extract(tg,'$.size')),0) bytes, "
        "COALESCE(SUM(CASE WHEN json_extract(tg,'$.mime') LIKE 'video/%' THEN 1 ELSE 0 END),0) videos, "
        "COALESCE(SUM(CASE WHEN json_extract(tg,'$.mime') NOT LIKE 'video/%' THEN 1 ELSE 0 END),0) docs "
        "FROM job_items i JOIN jobs j ON j.id=i.job_id WHERE j.user_id=? AND i.state='done'",
        (user_id,))
    return row or {"files": 0, "bytes": 0, "videos": 0, "docs": 0}


async def global_stats(db: Database) -> dict:
    now = time.time()
    day = time.strftime("%Y-%m-%d")
    out = {}
    out["users"] = int(await db.scalar("SELECT COUNT(*) FROM users", (), 0))
    out["active_24h"] = int(await db.scalar("SELECT COUNT(*) FROM users WHERE last_seen>=?",
                                            (now - 86400,), 0))
    for st in ("running", "queued", "paused", "done", "failed", "cancelled", "draft"):
        out[f"jobs_{st}"] = int(await db.scalar("SELECT COUNT(*) FROM jobs WHERE status=?", (st,), 0))
    row = await db.one("SELECT * FROM daily_stats WHERE day=?", (day,))
    out["uploads_today"] = int(row["uploads"]) if row else 0
    out["bytes_today"] = int(row["bytes"]) if row else 0
    out["locked"] = int(await db.scalar("SELECT COUNT(*) FROM user_locks WHERE expires_at>?",
                                        (now,), 0))
    out["plans_active"] = int(await db.scalar("SELECT COUNT(*) FROM plan_grants WHERE expires_at>?",
                                              (now,), 0))
    out["errors_today"] = int(await db.scalar("SELECT COUNT(*) FROM error_log WHERE ts>=?",
                                              (now - 86400,), 0))
    return out
