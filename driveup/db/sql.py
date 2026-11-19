"""SQLite schema + async helpers (aiosqlite).

Every collection from B10 exists here as a table with the same fields, the same
uniqueness and the same lifetime rules. The JSON-ish columns (`settings`,
`destination`, `naming`, `tg`, `drive`) are stored as TEXT and decoded by the
repo helpers, so callers work with dicts — exactly like the Mongo design.
"""
from __future__ import annotations

import json
import time
from typing import Any, Iterable, Optional, Sequence

import aiosqlite

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    name        TEXT NOT NULL DEFAULT '',
    username    TEXT NOT NULL DEFAULT '',
    first_seen  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    lang        TEXT NOT NULL DEFAULT 'hinglish',
    plan_id     TEXT NOT NULL DEFAULT 'free',
    role        TEXT NOT NULL DEFAULT 'user',
    settings    TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS ix_users_role ON users(role);
CREATE INDEX IF NOT EXISTS ix_users_username ON users(username);

CREATE TABLE IF NOT EXISTS drive_accounts (
    user_id            INTEGER PRIMARY KEY,
    google_sub         TEXT NOT NULL DEFAULT '',
    email              TEXT NOT NULL DEFAULT '',
    enc_refresh_token  TEXT NOT NULL DEFAULT '',
    access_token       TEXT NOT NULL DEFAULT '',
    access_expiry      REAL NOT NULL DEFAULT 0,
    scope              TEXT NOT NULL DEFAULT '',
    scope_mode         TEXT NOT NULL DEFAULT 'file',
    needs_reconnect    INTEGER NOT NULL DEFAULT 0,
    quota_limit        INTEGER NOT NULL DEFAULT 0,
    quota_used         INTEGER NOT NULL DEFAULT 0,
    connected_at       REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL,
    status      TEXT NOT NULL DEFAULT 'draft',
    destination TEXT NOT NULL DEFAULT '{}',
    naming      TEXT NOT NULL DEFAULT '{}',
    sequence_mode TEXT NOT NULL DEFAULT 'telegram_order',
    conflict_policy TEXT NOT NULL DEFAULT 'rename',
    strict_order  INTEGER NOT NULL DEFAULT 1,
    share_mode    TEXT NOT NULL DEFAULT 'restricted',
    totals      TEXT NOT NULL DEFAULT '{}',
    source      TEXT NOT NULL DEFAULT '{}',
    status_message TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL,
    started_at  REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0,
    priority    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_jobs_user ON jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_jobs_status ON jobs(status);

CREATE TABLE IF NOT EXISTS job_items (
    id          TEXT PRIMARY KEY,
    job_id      TEXT NOT NULL,
    seq_index   INTEGER NOT NULL DEFAULT 0,
    tg          TEXT NOT NULL DEFAULT '{}',
    final_name  TEXT NOT NULL DEFAULT '',
    state       TEXT NOT NULL DEFAULT 'pending',
    bytes_done  INTEGER NOT NULL DEFAULT 0,
    drive       TEXT NOT NULL DEFAULT '{}',
    attempts    INTEGER NOT NULL DEFAULT 0,
    error       TEXT NOT NULL DEFAULT '',
    started_at  REAL NOT NULL DEFAULT 0,
    finished_at REAL NOT NULL DEFAULT 0,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_items_job ON job_items(job_id, seq_index);
CREATE INDEX IF NOT EXISTS ix_items_state ON job_items(state);

-- One configured Telegram collection channel holds at most one open draft batch.
-- Closing a batch seals it; the next channel post starts a fresh job.
CREATE TABLE IF NOT EXISTS channel_bindings (
    user_id INTEGER PRIMARY KEY,
    channel_id TEXT NOT NULL UNIQUE,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_batches (
    channel_id TEXT PRIMARY KEY,
    owner_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'open',
    notice_message_id INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL,
    FOREIGN KEY(job_id) REFERENCES jobs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS channel_verification_failures (
    channel_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    owner_id INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_channel_verification_owner ON channel_verification_failures(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS channel_rejections (
    channel_id TEXT NOT NULL,
    message_id INTEGER NOT NULL,
    job_id TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY(channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS ix_channel_rejections_job ON channel_rejections(job_id);

CREATE TABLE IF NOT EXISTS user_locks (
    user_id    INTEGER PRIMARY KEY,
    reason     TEXT NOT NULL DEFAULT '',
    by_id      INTEGER NOT NULL DEFAULT 0,
    locked_at  REAL NOT NULL,
    expires_at REAL NOT NULL,
    scope      TEXT NOT NULL DEFAULT 'all'
);
CREATE INDEX IF NOT EXISTS ix_locks_expiry ON user_locks(expires_at);

CREATE TABLE IF NOT EXISTS plans (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    price_text  TEXT NOT NULL DEFAULT '',
    duration_days INTEGER NOT NULL DEFAULT 30,
    max_file_mb INTEGER NOT NULL DEFAULT 2000,
    daily_gb    REAL NOT NULL DEFAULT 20,
    concurrent  INTEGER NOT NULL DEFAULT 1,
    max_files_job INTEGER NOT NULL DEFAULT 200,
    features    TEXT NOT NULL DEFAULT '[]',
    sort        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS plan_grants (
    user_id    INTEGER PRIMARY KEY,
    plan_id    TEXT NOT NULL,
    granted_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_grants_expiry ON plan_grants(expires_at);

CREATE TABLE IF NOT EXISTS quota_usage (
    user_id INTEGER NOT NULL,
    day     TEXT NOT NULL,
    bytes   INTEGER NOT NULL DEFAULT 0,
    files   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, day)
);

CREATE TABLE IF NOT EXISTS admins (
    user_id  INTEGER PRIMARY KEY,
    role     TEXT NOT NULL DEFAULT 'admin',
    added_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    actor  INTEGER NOT NULL DEFAULT 0,
    action TEXT NOT NULL,
    target TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    ip     TEXT NOT NULL DEFAULT '',
    ts     REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit(ts DESC);

CREATE TABLE IF NOT EXISTS user_thumbs (
    user_id   INTEGER PRIMARY KEY,
    jpeg      BLOB NOT NULL,
    saved_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS settings_global (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_states (
    state      TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    code_verifier TEXT NOT NULL DEFAULT '',
    purpose    TEXT NOT NULL DEFAULT 'connect',
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_states_expiry ON oauth_states(expires_at);

CREATE TABLE IF NOT EXISTS sessions (
    sid        TEXT PRIMARY KEY,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    last_seen  REAL NOT NULL,
    expires_at REAL NOT NULL,
    ip         TEXT NOT NULL DEFAULT '',
    ua         TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_sessions_expiry ON sessions(expires_at);

CREATE TABLE IF NOT EXISTS presets (
    id      TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    name    TEXT NOT NULL,
    config  TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_presets_user ON presets(user_id);

CREATE TABLE IF NOT EXISTS favourites (
    user_id   INTEGER NOT NULL,
    folder_id TEXT NOT NULL,
    label     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, folder_id)
);

CREATE TABLE IF NOT EXISTS daily_stats (
    day         TEXT PRIMARY KEY,
    uploads     INTEGER NOT NULL DEFAULT 0,
    bytes       INTEGER NOT NULL DEFAULT 0,
    new_users   INTEGER NOT NULL DEFAULT 0,
    failed      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS error_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    where_  TEXT NOT NULL DEFAULT '',
    detail  TEXT NOT NULL DEFAULT '',
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_err_ts ON error_log(ts DESC);

CREATE TABLE IF NOT EXISTS tg_files (
    file_unique_id TEXT PRIMARY KEY,
    chat_id        INTEGER NOT NULL,
    message_id     INTEGER NOT NULL,
    file_id        TEXT NOT NULL DEFAULT '',
    name           TEXT NOT NULL DEFAULT '',
    size           INTEGER NOT NULL DEFAULT 0,
    mime           TEXT NOT NULL DEFAULT '',
    created_at     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS seen_messages (
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    user_id    INTEGER NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (chat_id, message_id)
);

CREATE TABLE IF NOT EXISTS abuse (
    user_id INTEGER NOT NULL,
    ts      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_abuse ON abuse(user_id, ts);
"""


class Database:
    def __init__(self, path: str) -> None:
        self.path = path
        self.conn: Optional[aiosqlite.Connection] = None

    async def connect(self) -> None:
        import os
        d = os.path.dirname(self.path or "")
        if d:
            os.makedirs(d, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.executescript(SCHEMA)
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn:
            await self.conn.close()
            self.conn = None

    # ── primitives ────────────────────────────────────────────────────────
    async def x(self, sql: str, params: Sequence[Any] = ()) -> None:
        assert self.conn
        await self.conn.execute(sql, tuple(params))
        await self.conn.commit()

    async def q(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        assert self.conn
        cur = await self.conn.execute(sql, tuple(params))
        rows = await cur.fetchall()
        await cur.close()
        return [dict(r) for r in rows]

    async def all(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        """Alias of q(): reads better at call sites that say "give me all rows"."""
        return await self.q(sql, params)

    async def one(self, sql: str, params: Sequence[Any] = ()) -> Optional[dict]:
        rows = await self.q(sql, params)
        return rows[0] if rows else None

    async def scalar(self, sql: str, params: Sequence[Any] = (), default: Any = None) -> Any:
        row = await self.one(sql, params)
        if not row:
            return default
        return next(iter(row.values()))

    async def many_x(self, sql: str, rows: Iterable[Sequence[Any]]) -> None:
        assert self.conn
        await self.conn.executemany(sql, [tuple(r) for r in rows])
        await self.conn.commit()

    async def now(self) -> float:
        return time.time()

    # ── maintenance: TTLs that Mongo would do itself ───────────────────────
    async def sweep(self) -> dict:
        now = time.time()
        out = {}
        out["locks"] = (await self.q("SELECT COUNT(*) c FROM user_locks WHERE expires_at<=?", (now,)))[0]["c"]
        await self.x("DELETE FROM user_locks WHERE expires_at<=?", (now,))
        out["states"] = (await self.q("SELECT COUNT(*) c FROM oauth_states WHERE expires_at<=?", (now,)))[0]["c"]
        await self.x("DELETE FROM oauth_states WHERE expires_at<=?", (now,))
        out["sessions"] = (await self.q("SELECT COUNT(*) c FROM sessions WHERE expires_at<=?", (now,)))[0]["c"]
        await self.x("DELETE FROM sessions WHERE expires_at<=?", (now,))
        out["grants"] = (await self.q("SELECT COUNT(*) c FROM plan_grants WHERE expires_at<=?", (now,)))[0]["c"]
        await self.x("DELETE FROM plan_grants WHERE expires_at<=?", (now,))
        # quota rows older than 90 days
        await self.x("DELETE FROM quota_usage WHERE day < date('now','-90 day')")
        # error log keeps the last 1000 rows
        await self.x("DELETE FROM error_log WHERE id NOT IN "
                     "(SELECT id FROM error_log ORDER BY id DESC LIMIT 1000)")
        return out


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def loads(text: str, default: Any = None) -> Any:
    if not text:
        return {} if default is None else default
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return {} if default is None else default
