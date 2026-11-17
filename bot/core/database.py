import sqlite3
import os
import json
import time
from threading import Lock

# Support Render Persistent Disk via Config.DB_PATH, fallback to ./bot.db
try:
    from config import Config
    DB_PATH = Config.DB_PATH
except Exception:
    DB_PATH = os.getenv("DB_PATH", "bot.db")

_lock = Lock()

def _conn():
    # Ensure parent dir exists (for Render disk)
    parent = os.path.dirname(os.path.abspath(DB_PATH))
    if parent and not os.path.exists(parent):
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    # WAL mode for concurrent access (bot + web threads)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn

def init_db():
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            is_authorized INTEGER DEFAULT 0,
            token_path TEXT,
            default_folder_id TEXT DEFAULT 'root',
            default_folder_name TEXT DEFAULT 'My Drive',
            auto_share INTEGER DEFAULT 1,
            auto_rename TEXT DEFAULT '',
            is_banned INTEGER DEFAULT 0,
            ban_reason TEXT DEFAULT '',
            total_uploads INTEGER DEFAULT 0,
            total_bytes INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS uploads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            file_name TEXT,
            file_size INTEGER,
            drive_file_id TEXT,
            drive_link TEXT,
            folder_id TEXT,
            status TEXT DEFAULT 'completed',
            source TEXT DEFAULT 'telegram',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            source_type TEXT,
            source_data TEXT,
            file_name TEXT,
            status TEXT DEFAULT 'queued',
            progress REAL DEFAULT 0,
            message_id INTEGER,
            chat_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS automation (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            channel_id INTEGER,
            folder_id TEXT,
            folder_name TEXT,
            auto_share INTEGER DEFAULT 1,
            rename_pattern TEXT DEFAULT '',
            is_active INTEGER DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(user_id, channel_id)
        );
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        CREATE TABLE IF NOT EXISTS broadcasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER,
            message TEXT,
            sent_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_uploads_user ON uploads(user_id);
        CREATE INDEX IF NOT EXISTS idx_queue_user ON queue(user_id);
        """)
        # Migrations: add columns if missing (for existing DBs)
        for col, ddl in [
            ("is_banned", "ALTER TABLE users ADD COLUMN is_banned INTEGER DEFAULT 0"),
            ("ban_reason", "ALTER TABLE users ADD COLUMN ban_reason TEXT DEFAULT ''"),
            ("total_uploads", "ALTER TABLE users ADD COLUMN total_uploads INTEGER DEFAULT 0"),
            ("total_bytes", "ALTER TABLE users ADD COLUMN total_bytes INTEGER DEFAULT 0"),
        ]:
            try:
                cur.execute(f"SELECT {col} FROM users LIMIT 1")
            except Exception:
                try:
                    cur.execute(ddl)
                except Exception:
                    pass
        conn.commit()
        conn.close()

def get_user(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

def upsert_user(user_id: int, username="", first_name=""):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE user_id=?", (user_id,))
        if cur.fetchone():
            cur.execute("UPDATE users SET username=?, first_name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (username, first_name, user_id))
        else:
            cur.execute("INSERT INTO users (user_id, username, first_name) VALUES (?,?,?)", (user_id, username, first_name))
        conn.commit()
        conn.close()

def set_authorized(user_id: int, token_path: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_authorized=1, token_path=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (token_path, user_id))
        conn.commit()
        conn.close()

def set_default_folder(user_id: int, folder_id: str, folder_name: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET default_folder_id=?, default_folder_name=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (folder_id, folder_name, user_id))
        conn.commit()
        conn.close()

def get_default_folder(user_id: int):
    u = get_user(user_id)
    if u:
        return u.get("default_folder_id") or "root", u.get("default_folder_name") or "My Drive"
    return "root", "My Drive"

def set_auto_share(user_id: int, enabled: bool):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET auto_share=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (1 if enabled else 0, user_id))
        conn.commit()
        conn.close()

def set_auto_rename(user_id: int, pattern: str):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET auto_rename=?, updated_at=CURRENT_TIMESTAMP WHERE user_id=?", (pattern, user_id))
        conn.commit()
        conn.close()

def is_banned(user_id: int):
    u = get_user(user_id)
    return bool(u and u.get("is_banned"))

def ban_user(user_id: int, reason=""):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_banned=1, ban_reason=? WHERE user_id=?", (reason, user_id))
        conn.commit()
        conn.close()

def unban_user(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("UPDATE users SET is_banned=0, ban_reason='' WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

def get_all_users(limit=1000):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users ORDER BY created_at DESC LIMIT ?", (limit,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

def get_global_stats():
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as users FROM users")
        users = cur.fetchone()["users"]
        cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(file_size),0) as s FROM uploads")
        row = cur.fetchone()
        cur.execute("SELECT COUNT(*) as c FROM uploads WHERE status='completed'")
        completed = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c FROM automation WHERE is_active=1")
        autos = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) as c FROM users WHERE is_authorized=1")
        connected = cur.fetchone()["c"]
        conn.close()
        return {"users": users, "connected": connected, "total_files": row["c"], "total_bytes": row["s"], "completed": completed, "automations": autos}

def add_upload(user_id, file_name, file_size, drive_file_id, drive_link, folder_id, source="telegram"):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO uploads (user_id, file_name, file_size, drive_file_id, drive_link, folder_id, source) VALUES (?,?,?,?,?,?,?)",
                    (user_id, file_name, file_size, drive_file_id, drive_link, folder_id, source))
        cur.execute("UPDATE users SET total_uploads=COALESCE(total_uploads,0)+1, total_bytes=COALESCE(total_bytes,0)+? WHERE user_id=?", (file_size or 0, user_id))
        conn.commit()
        conn.close()

def get_recent_uploads(user_id: int, limit=20):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM uploads WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, limit))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

def get_stats(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as c, COALESCE(SUM(file_size),0) as s FROM uploads WHERE user_id=?", (user_id,))
        row = cur.fetchone()
        conn.close()
        return {"total_files": row["c"], "total_bytes": row["s"]}

# Queue
def add_to_queue(user_id, source_type, source_data, file_name="", chat_id=0, message_id=0):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO queue (user_id, source_type, source_data, file_name, chat_id, message_id) VALUES (?,?,?,?,?,?)",
                    (user_id, source_type, source_data, file_name, chat_id, message_id))
        qid = cur.lastrowid
        conn.commit()
        conn.close()
        return qid

def get_queue(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM queue WHERE user_id=? ORDER BY created_at ASC", (user_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

def update_queue(qid: int, status=None, progress=None):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        if status and progress is not None:
            cur.execute("UPDATE queue SET status=?, progress=? WHERE id=?", (status, progress, qid))
        elif status:
            cur.execute("UPDATE queue SET status=? WHERE id=?", (status, qid))
        elif progress is not None:
            cur.execute("UPDATE queue SET progress=? WHERE id=?", (progress, qid))
        conn.commit()
        conn.close()

def clear_queue(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM queue WHERE user_id=?", (user_id,))
        conn.commit()
        conn.close()

# Automation
def set_automation(user_id, channel_id, folder_id, folder_name, auto_share=1, rename_pattern=""):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT OR REPLACE INTO automation (user_id, channel_id, folder_id, folder_name, auto_share, rename_pattern) VALUES (?,?,?,?,?,?)",
                    (user_id, channel_id, folder_id, folder_name, auto_share, rename_pattern))
        conn.commit()
        conn.close()

def get_automation_by_channel(channel_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM automation WHERE channel_id=? AND is_active=1", (channel_id,))
        row = cur.fetchone()
        conn.close()
        return dict(row) if row else None

def get_automations(user_id: int):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("SELECT * FROM automation WHERE user_id=?", (user_id,))
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows

def delete_automation(user_id, channel_id):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM automation WHERE user_id=? AND channel_id=?", (user_id, channel_id))
        conn.commit()
        conn.close()

def log_broadcast(admin_id, message, sent, failed):
    with _lock:
        conn = _conn()
        cur = conn.cursor()
        cur.execute("INSERT INTO broadcasts (admin_id, message, sent_count, failed_count) VALUES (?,?,?,?)", (admin_id, message[:1000], sent, failed))
        conn.commit()
        conn.close()

init_db()
