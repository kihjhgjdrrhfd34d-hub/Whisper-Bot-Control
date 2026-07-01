import sqlite3
import json
import uuid
from datetime import datetime, timedelta
from config import DATABASE_PATH, DEFAULT_SETTINGS


def get_conn():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                is_banned   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id      TEXT PRIMARY KEY,
                sender_id       INTEGER NOT NULL,
                content         TEXT NOT NULL,
                whisper_type    TEXT NOT NULL,
                target_users    TEXT DEFAULT '[]',
                max_readers     INTEGER DEFAULT 0,
                is_locked       INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (datetime('now')),
                auto_delete_at  TEXT,
                FOREIGN KEY (sender_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS whisper_readers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                whisper_id  TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                read_at     TEXT DEFAULT (datetime('now')),
                UNIQUE(whisper_id, user_id),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE TABLE IF NOT EXISTS curious_ones (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                whisper_id  TEXT NOT NULL,
                user_id     INTEGER NOT NULL,
                tried_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(whisper_id, user_id),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mandatory_channels (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  TEXT NOT NULL UNIQUE,
                channel_name TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT,
                media_type  TEXT,
                file_id     TEXT,
                sent_at     TEXT DEFAULT (datetime('now')),
                sent_by     INTEGER
            );
        """)
        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val)
            )
        conn.commit()


def upsert_user(user_id, username=None, first_name=None, last_name=None):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
        """, (user_id, username, first_name, last_name))
        conn.commit()


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()


def is_banned(user_id):
    with get_conn() as conn:
        row = conn.execute("SELECT is_banned FROM users WHERE user_id=?", (user_id,)).fetchone()
        return row and row["is_banned"] == 1


def ban_user(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_banned=1 WHERE user_id=?", (user_id,))
        conn.commit()


def unban_user(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_banned=0 WHERE user_id=?", (user_id,))
        conn.commit()


def get_all_users(page=0, per_page=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (per_page, page * per_page)
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return rows, total


def search_users(query):
    with get_conn() as conn:
        like = f"%{query}%"
        return conn.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ? OR CAST(user_id AS TEXT)=?",
            (like, like, query)
        ).fetchall()


def create_whisper(sender_id, content, whisper_type, target_users=None, max_readers=0, auto_delete_hours=0):
    wid = str(uuid.uuid4())[:12]
    targets = json.dumps(target_users or [])
    auto_delete_at = None
    if auto_delete_hours > 0:
        auto_delete_at = (datetime.utcnow() + timedelta(hours=auto_delete_hours)).isoformat()
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO whispers (whisper_id, sender_id, content, whisper_type, target_users, max_readers, auto_delete_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (wid, sender_id, content, whisper_type, targets, max_readers, auto_delete_at))
        conn.commit()
    return wid


def get_whisper(whisper_id):
    with get_conn() as conn:
        return conn.execute("SELECT * FROM whispers WHERE whisper_id=?", (whisper_id,)).fetchone()


def update_whisper_content(whisper_id, content):
    with get_conn() as conn:
        conn.execute("UPDATE whispers SET content=? WHERE whisper_id=?", (content, whisper_id))
        conn.commit()


def toggle_whisper_lock(whisper_id):
    with get_conn() as conn:
        w = conn.execute("SELECT is_locked FROM whispers WHERE whisper_id=?", (whisper_id,)).fetchone()
        if w is None:
            return None
        new_state = 0 if w["is_locked"] else 1
        conn.execute("UPDATE whispers SET is_locked=? WHERE whisper_id=?", (new_state, whisper_id))
        conn.commit()
        return new_state


def delete_whisper(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (whisper_id,))
        conn.execute("DELETE FROM curious_ones WHERE whisper_id=?", (whisper_id,))
        conn.execute("DELETE FROM whispers WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def clear_whisper_readers(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def add_reader(whisper_id, user_id):
<<<<<<< HEAD
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO whisper_readers (whisper_id, user_id)
            VALUES (?, ?)
        """, (whisper_id, user_id))
        conn.commit()
=======
    """Insert reader (idempotent). Kept for backward compatibility."""
    add_reader_if_new(whisper_id, user_id)


def add_reader_if_new(whisper_id: str, user_id: int) -> bool:
    """
    Insert the reader record atomically.
    Returns True on first insert, False if already exists.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO whisper_readers (whisper_id, user_id) VALUES (?, ?)",
            (whisper_id, user_id),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    return inserted == 1


def record_whisper_read(whisper_id: str, user_id: int) -> bool:
    """
    Register a reader and conditionally auto-lock the whisper based on its type.

    Rules
    -----
    * everyone    — reader is registered; is_locked is **never** touched.
    * first_three — reader is registered; is_locked is set to 1 **only**
                    when the reader count reaches 3 or more.
    * first_one, custom — no auto-lock (permission gating via can_read_whisper).

    Returns True if reader was newly added, False if already existed.
    """
    is_new = add_reader_if_new(whisper_id, user_id)
    if not is_new:
        return False

    w = get_whisper(whisper_id)
    if not w:
        return True

    wtype = w["whisper_type"]
    if wtype == "everyone":
        return True

    if wtype == "first_three":
        count = reader_count(whisper_id)
        if count >= 3:
            with get_conn() as conn:
                conn.execute(
                    "UPDATE whispers SET is_locked=1 WHERE whisper_id=?",
                    (whisper_id,),
                )
                conn.commit()
        return True

    return True
>>>>>>> 62f1532 (First commit - إضافة نظام الهمسات التدميرية)


def get_readers(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT wr.user_id, u.username, u.first_name FROM whisper_readers wr "
            "LEFT JOIN users u ON u.user_id=wr.user_id WHERE wr.whisper_id=?",
            (whisper_id,)
        ).fetchall()


def reader_count(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_readers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()[0]


def add_curious(whisper_id, user_id):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO curious_ones (whisper_id, user_id)
            VALUES (?, ?)
        """, (whisper_id, user_id))
        conn.commit()


def get_curious_ones(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT co.user_id, u.username, u.first_name FROM curious_ones co "
            "LEFT JOIN users u ON u.user_id=co.user_id WHERE co.whisper_id=?",
            (whisper_id,)
        ).fetchall()


def can_read_whisper(whisper_id, user_id):
    w = get_whisper(whisper_id)
    if not w:
        return False, "not_found"
    if w["is_locked"]:
        return False, "locked"
    if w["sender_id"] == user_id:
        return True, "sender"
    wtype = w["whisper_type"]
    if wtype == "everyone":
        return True, "allowed"
    if wtype == "first_one":
        readers = get_readers(whisper_id)
        if len(readers) == 0 or any(r["user_id"] == user_id for r in readers):
            return True, "allowed"
        return False, "taken"
    if wtype == "first_three":
        readers = get_readers(whisper_id)
        if len(readers) < 3 or any(r["user_id"] == user_id for r in readers):
            return True, "allowed"
        return False, "taken"
    if wtype == "custom":
        targets = json.loads(w["target_users"])
        if user_id in targets or str(user_id) in targets:
            return True, "allowed"
        return False, "not_target"
    return False, "unknown"


def get_setting(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key)


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def get_mandatory_channels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM mandatory_channels").fetchall()


def add_mandatory_channel(channel_id, channel_name=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mandatory_channels (channel_id, channel_name) VALUES (?, ?)",
            (channel_id, channel_name)
        )
        conn.commit()


def remove_mandatory_channel(channel_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM mandatory_channels WHERE channel_id=?", (channel_id,))
        conn.commit()


def get_stats():
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned_users = conn.execute("SELECT COUNT(*) FROM users WHERE is_banned=1").fetchone()[0]
        total_whispers = conn.execute("SELECT COUNT(*) FROM whispers").fetchone()[0]
        total_reads = conn.execute("SELECT COUNT(*) FROM whisper_readers").fetchone()[0]
        today = datetime.utcnow().date().isoformat()
        new_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        whispers_today = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        return {
            "total_users": total_users,
            "banned_users": banned_users,
            "active_users": total_users - banned_users,
            "total_whispers": total_whispers,
            "total_reads": total_reads,
            "new_today": new_today,
            "whispers_today": whispers_today,
        }


def delete_expired_whispers():
    """حذف الهمسات التي انتهت مدتها. تُعيد عدد ما تم حذفه."""
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT whisper_id FROM whispers WHERE auto_delete_at IS NOT NULL AND auto_delete_at <= ?",
            (now,)
        ).fetchall()
        count = len(rows)
        for row in rows:
            wid = row["whisper_id"]
            conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (wid,))
            conn.execute("DELETE FROM curious_ones WHERE whisper_id=?", (wid,))
            conn.execute("DELETE FROM whispers WHERE whisper_id=?", (wid,))
        conn.commit()
    return count


def get_user_stats(user_id):
    """إحصائيات شخصية للمستخدم"""
    with get_conn() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (user_id,)
        ).fetchone()[0]
        received_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE w.sender_id=? AND wr.user_id != ?", (user_id, user_id)
        ).fetchone()[0]
        read_others = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE wr.user_id=? AND w.sender_id != ?", (user_id, user_id)
        ).fetchone()[0]
        curious_count = conn.execute(
            "SELECT COUNT(*) FROM curious_ones co "
            "JOIN whispers w ON w.whisper_id=co.whisper_id "
            "WHERE w.sender_id=?", (user_id,)
        ).fetchone()[0]
        locked_count = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=? AND is_locked=1", (user_id,)
        ).fetchone()[0]
        type_counts = conn.execute(
            "SELECT whisper_type, COUNT(*) as cnt FROM whispers WHERE sender_id=? GROUP BY whisper_type",
            (user_id,)
        ).fetchall()
        types = {row["whisper_type"]: row["cnt"] for row in type_counts}
        return {
            "sent": sent,
            "received_reads": received_reads,
            "read_others": read_others,
            "curious_on_mine": curious_count,
            "locked": locked_count,
            "type_everyone": types.get("everyone", 0),
            "type_first_one": types.get("first_one", 0),
            "type_first_three": types.get("first_three", 0),
            "type_custom": types.get("custom", 0),
        }
