import logging
import sqlite3
import json
import uuid
from datetime import datetime, timedelta, timezone
from config import DATABASE_PATH, DEFAULT_SETTINGS, GROUP_DEFAULT_SETTINGS

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Connection helper
# ─────────────────────────────────────────────────────────────────────────────

def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # WAL mode: concurrent reads don't block writes
    conn.execute("PRAGMA journal_mode=WAL")
    # Keep 8 MB of pages in memory (default is only 2 MB)
    conn.execute("PRAGMA cache_size=-8000")
    # Normal sync: safe after system crash but faster than FULL
    conn.execute("PRAGMA synchronous=NORMAL")
    # Enable foreign key enforcement
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# Schema initialisation + migrations
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     INTEGER PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                is_banned   INTEGER DEFAULT 0,
                started     INTEGER DEFAULT 0
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
                is_destructive  INTEGER DEFAULT 0,
                message_type    TEXT,
                file_id         TEXT,
                caption         TEXT,
                location_lat    REAL,
                location_lon    REAL,
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
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id   TEXT NOT NULL UNIQUE,
                channel_name TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                content    TEXT,
                media_type TEXT,
                file_id    TEXT,
                sent_at    TEXT DEFAULT (datetime('now')),
                sent_by    INTEGER
            );

            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id                 INTEGER PRIMARY KEY,
                public_whispers_enabled INTEGER DEFAULT 1,
                anonymous_enabled       INTEGER DEFAULT 1,
                read_notifications      INTEGER DEFAULT 1,
                auto_delete_minutes     INTEGER DEFAULT 0,
                spam_limit_enabled      INTEGER DEFAULT 1,
                spam_limit_count        INTEGER DEFAULT 5,
                spam_limit_window_seconds INTEGER DEFAULT 60
            );

            CREATE TABLE IF NOT EXISTS whisper_timestamps (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                chat_id     INTEGER NOT NULL,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS pending_media_whispers (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                file_id      TEXT NOT NULL,
                caption      TEXT,
                content      TEXT,
                created_at   TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
        # ── Indexes for hot-path queries ──────────────────────────────────
        conn.executescript("""
            CREATE INDEX IF NOT EXISTS idx_whispers_sender
                ON whispers(sender_id);
            CREATE INDEX IF NOT EXISTS idx_whispers_auto_delete
                ON whispers(auto_delete_at)
                WHERE auto_delete_at IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_wr_whisper
                ON whisper_readers(whisper_id);
            CREATE INDEX IF NOT EXISTS idx_wr_user
                ON whisper_readers(user_id);
            CREATE INDEX IF NOT EXISTS idx_curious_whisper
                ON curious_ones(whisper_id);
            CREATE INDEX IF NOT EXISTS idx_users_username
                ON users(username)
                WHERE username IS NOT NULL;
            CREATE INDEX IF NOT EXISTS idx_users_created
                ON users(created_at);
            CREATE INDEX IF NOT EXISTS idx_wt_lookup
                ON whisper_timestamps(user_id, chat_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_pmw_user
                ON pending_media_whispers(user_id);
        """)
        # seed default settings (won't overwrite existing values)
        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, val),
            )
        conn.commit()
    # Replies schema (additive — safe if tables already exist)
    try:
        from database.replies import init_replies_db
        init_replies_db()
    except Exception:
        pass   # replies module is optional at import time
    _run_migrations()


def _run_migrations():
    """Add columns / rows that may be missing from older databases.
    Safe to call on a fresh DB — always checks existence before altering."""
    with get_conn() as conn:
        # Guard: tables must exist before we try to alter them
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        # Migration 1: add `started` column to users if missing
        if "users" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
            if "started" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN started INTEGER DEFAULT 0")

        # Migration 2: ensure all DEFAULT_SETTINGS keys exist in settings table
        if "settings" in tables:
            for key, val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, val),
                )

        # Migration 4: add is_destructive column to whispers
        if "whispers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
            if "is_destructive" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN is_destructive INTEGER DEFAULT 0")

        # Migration 5: add is_closed column to whispers (dashboard close feature)
        if "whispers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
            if "is_closed" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN is_closed INTEGER DEFAULT 0")
            if "is_pinned" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN is_pinned INTEGER DEFAULT 0")

        # Migration 6: add media columns to whispers (v2.1.0 media support)
        if "whispers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
            media_columns = [
                ("message_type", "TEXT"),
                ("file_id", "TEXT"),
                ("caption", "TEXT"),
                ("location_lat", "REAL"),
                ("location_lon", "REAL"),
            ]
            for col_name, col_type in media_columns:
                if col_name not in cols:
                    conn.execute(f"ALTER TABLE whispers ADD COLUMN {col_name} {col_type}")

        # Migration 3: add performance indexes only when tables exist
        existing_tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}

        # Migration 7: pending_media_whispers table (v2.2.0 media wizard)
        if "pending_media_whispers" not in existing_tables:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS pending_media_whispers (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id      INTEGER NOT NULL,
                    message_type TEXT NOT NULL,
                    file_id      TEXT NOT NULL,
                    caption      TEXT,
                    content      TEXT,
                    created_at   TEXT DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(user_id)
                );
                CREATE INDEX IF NOT EXISTS idx_pmw_user
                    ON pending_media_whispers(user_id);
            """)

        # Migration 8: add media_type column to whispers
        if "whispers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
            if "media_type" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN media_type TEXT")

        # Migration 10: group message tracking for open-once behavior
        if "whispers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
            if "group_chat_id" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN group_chat_id INTEGER")
            if "group_message_id" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN group_message_id INTEGER")
            if "group_inline_message_id" not in cols:
                conn.execute("ALTER TABLE whispers ADD COLUMN group_inline_message_id TEXT")

        # Migration 9: add spam-limit columns to group_settings if missing
        if "group_settings" in tables:
            gs_cols = [r[1] for r in conn.execute("PRAGMA table_info(group_settings)").fetchall()]
            if "spam_limit_enabled" not in gs_cols:
                conn.execute("ALTER TABLE group_settings ADD COLUMN spam_limit_enabled INTEGER DEFAULT 1")
            if "spam_limit_count" not in gs_cols:
                conn.execute("ALTER TABLE group_settings ADD COLUMN spam_limit_count INTEGER DEFAULT 5")
            if "spam_limit_window_seconds" not in gs_cols:
                conn.execute("ALTER TABLE group_settings ADD COLUMN spam_limit_window_seconds INTEGER DEFAULT 60")

        index_sql = []
        if "whispers" in existing_tables:
            index_sql += [
                "CREATE INDEX IF NOT EXISTS idx_whispers_sender"
                "    ON whispers(sender_id);",
                "CREATE INDEX IF NOT EXISTS idx_whispers_auto_delete"
                "    ON whispers(auto_delete_at)"
                "    WHERE auto_delete_at IS NOT NULL;",
            ]
        if "whisper_readers" in existing_tables:
            index_sql += [
                "CREATE INDEX IF NOT EXISTS idx_wr_whisper"
                "    ON whisper_readers(whisper_id);",
                "CREATE INDEX IF NOT EXISTS idx_wr_user"
                "    ON whisper_readers(user_id);",
            ]
        if "curious_ones" in existing_tables:
            index_sql.append(
                "CREATE INDEX IF NOT EXISTS idx_curious_whisper"
                "    ON curious_ones(whisper_id);"
            )
        if "users" in existing_tables:
            index_sql += [
                "CREATE INDEX IF NOT EXISTS idx_users_username"
                "    ON users(username)"
                "    WHERE username IS NOT NULL;",
                "CREATE INDEX IF NOT EXISTS idx_users_created"
                "    ON users(created_at);",
            ]
        if index_sql:
            conn.executescript("\n".join(index_sql))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Users
# ─────────────────────────────────────────────────────────────────────────────

def upsert_user(user_id, username=None, first_name=None, last_name=None):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username=excluded.username,
                first_name=excluded.first_name,
                last_name=excluded.last_name
            """,
            (user_id, username, first_name, last_name),
        )
        conn.commit()


def is_new_user(user_id):
    """Return True if the user has never completed /start before."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT started FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row is None:
            return True
        return row["started"] == 0


def mark_user_started(user_id):
    """Mark that the user has completed their first /start."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET started=1 WHERE user_id=?", (user_id,)
        )
        conn.commit()


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=?", (user_id,)
        ).fetchone()


def is_banned(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_banned FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
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
            (per_page, page * per_page),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        return rows, total


def search_users(query):
    with get_conn() as conn:
        like = f"%{query}%"
        return conn.execute(
            "SELECT * FROM users WHERE username LIKE ? OR first_name LIKE ?"
            " OR CAST(user_id AS TEXT)=?",
            (like, like, query),
        ).fetchall()


# ─────────────────────────────────────────────────────────────────────────────
# Whispers
# ─────────────────────────────────────────────────────────────────────────────

def create_whisper(
    sender_id, content, whisper_type,
    target_users=None, max_readers=0, auto_delete_hours=0,
    is_destructive=False, group_auto_delete_minutes=0,
    message_type=None, file_id=None, caption=None,
    location_lat=None, location_lon=None,
    media_type=None,
):
    wid = str(uuid.uuid4())[:12]
    targets = json.dumps(target_users or [])
    auto_delete_at = None
    if auto_delete_hours > 0:
        auto_delete_at = (
            datetime.now(timezone.utc) + timedelta(hours=auto_delete_hours)
        ).isoformat()
    elif group_auto_delete_minutes > 0:
        auto_delete_at = (
            datetime.now(timezone.utc) + timedelta(minutes=group_auto_delete_minutes)
        ).isoformat()
    if media_type is None:
        media_type = message_type or "text"
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO whispers
                (whisper_id, sender_id, content, whisper_type,
                 target_users, max_readers, auto_delete_at, is_destructive,
                 message_type, file_id, caption, location_lat, location_lon,
                 media_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (wid, sender_id, content, whisper_type, targets, max_readers,
             auto_delete_at, int(is_destructive),
             message_type, file_id, caption, location_lat, location_lon,
             media_type),
        )
        conn.commit()
    return wid


def get_whisper(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM whispers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()


def update_whisper_content(whisper_id, content):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whispers SET content=? WHERE whisper_id=?", (content, whisper_id)
        )
        conn.commit()


def update_whisper_group_message(whisper_id, chat_id=None, message_id=None, inline_message_id=None):
    """Store the group/channel message location for open-once button editing."""
    with get_conn() as conn:
        if chat_id is not None:
            conn.execute("UPDATE whispers SET group_chat_id=? WHERE whisper_id=?", (chat_id, whisper_id))
        if message_id is not None:
            conn.execute("UPDATE whispers SET group_message_id=? WHERE whisper_id=?", (message_id, whisper_id))
        if inline_message_id is not None:
            conn.execute("UPDATE whispers SET group_inline_message_id=? WHERE whisper_id=?", (inline_message_id, whisper_id))
        conn.commit()


def toggle_whisper_lock(whisper_id):
    with get_conn() as conn:
        w = conn.execute(
            "SELECT is_locked FROM whispers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()
        if w is None:
            return None
        new_state = 0 if w["is_locked"] else 1
        conn.execute(
            "UPDATE whispers SET is_locked=? WHERE whisper_id=?",
            (new_state, whisper_id),
        )
        conn.commit()
        return new_state


def lock_whisper(whisper_id):
    """Set is_locked = 1 on a whisper unconditionally."""
    with get_conn() as conn:
        conn.execute("UPDATE whispers SET is_locked=1 WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def close_whisper(whisper_id):
    """Close a whisper permanently — cannot be read or replied to."""
    with get_conn() as conn:
        conn.execute("UPDATE whispers SET is_closed=1, is_locked=1 WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def is_whisper_closed(whisper_id):
    """Return True if the whisper is closed."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_closed FROM whispers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()
        return bool(row and row["is_closed"])


def toggle_pin_whisper(whisper_id):
    """Toggle the pinned state of a whisper. Returns the new state (0/1)."""
    with get_conn() as conn:
        w = conn.execute(
            "SELECT is_pinned FROM whispers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()
        if w is None:
            return None
        new_state = 0 if w["is_pinned"] else 1
        conn.execute(
            "UPDATE whispers SET is_pinned=? WHERE whisper_id=?",
            (new_state, whisper_id),
        )
        conn.commit()
        return new_state


def get_pinned_whispers(sender_id, limit=10, offset=0):
    """Get pinned whispers for a sender, newest first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? AND is_pinned=1"
            " ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (sender_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=? AND is_pinned=1",
            (sender_id,),
        ).fetchone()[0]
        return rows, total


def get_sender_whispers(sender_id, limit=10, offset=0):
    """Get whispers sent by a user, newest first. Pinned ones first."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=?"
            " ORDER BY is_pinned DESC, created_at DESC LIMIT ? OFFSET ?",
            (sender_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?",
            (sender_id,),
        ).fetchone()[0]
        return rows, total


def delete_whisper(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (whisper_id,))
        conn.execute("DELETE FROM curious_ones WHERE whisper_id=?", (whisper_id,))
        # Delete replies if the table exists (additive feature)
        try:
            conn.execute("DELETE FROM whisper_replies WHERE whisper_id=?", (whisper_id,))
        except Exception:
            pass
        conn.execute("DELETE FROM whispers WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def clear_whisper_readers(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (whisper_id,))
        conn.commit()


def add_reader(whisper_id, user_id):
    """Insert reader (idempotent). Kept for backward compatibility."""
    add_reader_if_new(whisper_id, user_id)


def add_reader_if_new(whisper_id: str, user_id: int) -> bool:
    """
    Insert the reader record atomically.

    Returns:
        True  — first time this user reads this whisper (record inserted).
        False — user already read it before (INSERT OR IGNORE skipped).

    Uses SQLite changes() so the decision is atomic — no race condition
    even under concurrent access with WAL journal mode.

    NOTE: This low-level function does NOT manage the is_locked flag.
    Use record_whisper_read() instead when you want type-aware locking.
    """
    logger.debug("[DB] add_reader_if_new whisper_id=%s user_id=%s", whisper_id, user_id)
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO whisper_readers (whisper_id, user_id) VALUES (?, ?)",
            (whisper_id, user_id),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    logger.debug("[DB] add_reader_if_new -> %s (changes=%s)", inserted == 1, inserted)
    return inserted == 1


def record_whisper_read(whisper_id: str, user_id: int) -> bool:
    """
    Register a reader and conditionally auto-lock the whisper based on its type.

    Rules
    -----
    * everyone  — reader is registered; is_locked is **never** touched.
                  The whisper stays open forever so any new reader can join.
    * first_three — reader is registered; is_locked is set to 1 **only**
                    when the reader count reaches 3 or more.
                    Below 3 the whisper stays open for the next reader.
    * first_one, custom  — no auto-lock (permission gating is handled
                           exclusively by can_read_whisper).

    Returns:
        True  — first time this user reads this whisper.
        False — user already read it before.
    """
    is_new = add_reader_if_new(whisper_id, user_id)
    if not is_new:
        return False

    w = get_whisper(whisper_id)
    if not w:
        return True

    wtype = w["whisper_type"]
    if wtype == "everyone":
        # ── NEVER lock an "everyone" whisper ──────────────────────────────
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

    # first_one, custom — no auto-lock (can_read_whisper gates access)
    return True


def get_readers(whisper_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT wr.user_id, u.username, u.first_name "
            "FROM whisper_readers wr "
            "LEFT JOIN users u ON u.user_id=wr.user_id "
            "WHERE wr.whisper_id=?",
            (whisper_id,),
        ).fetchall()]


def reader_count(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_readers WHERE whisper_id=?",
            (whisper_id,),
        ).fetchone()[0]


def add_curious(whisper_id, user_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO curious_ones (whisper_id, user_id) VALUES (?, ?)",
            (whisper_id, user_id),
        )
        conn.commit()


def get_curious_ones(whisper_id):
    """Return curious users with tried_at timestamp included."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT co.user_id, co.tried_at, u.username, u.first_name "
            "FROM curious_ones co "
            "LEFT JOIN users u ON u.user_id=co.user_id "
            "WHERE co.whisper_id=? "
            "ORDER BY co.tried_at ASC",
            (whisper_id,),
        ).fetchall()


def can_read_whisper(whisper_id, user_id):
    w = get_whisper(whisper_id)
    if not w:
        return False, "not_found"
    w = dict(w)
    if w.get("is_closed", 0):
        return False, "locked"

    wtype = w["whisper_type"]

    if wtype == "everyone":
        if w["is_locked"]:
            return False, "locked"
        if w["sender_id"] == user_id:
            return True, "sender"
        return True, "allowed"

    if wtype == "first_one":
        if w["sender_id"] == user_id:
            return True, "sender"
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


# ─────────────────────────────────────────────────────────────────────────────
# Settings
# ─────────────────────────────────────────────────────────────────────────────

def get_setting(key):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=?", (key,)
        ).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key)


def get_all_settings(keys: list) -> dict:
    """
    Batch-fetch multiple settings in ONE DB connection.
    Returns {key: value} using DEFAULT_SETTINGS as fallback.
    Use instead of multiple get_setting() calls in keyboard builders.
    """
    result = {k: DEFAULT_SETTINGS.get(k) for k in keys}
    placeholders = ",".join("?" * len(keys))
    with get_conn() as conn:
        rows = conn.execute(
            f"SELECT key, value FROM settings WHERE key IN ({placeholders})",
            keys,
        ).fetchall()
    for row in rows:
        result[row["key"]] = row["value"]
    return result


def set_setting(key, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, value),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Group Settings
# ─────────────────────────────────────────────────────────────────────────────

def ensure_group_settings(chat_id):
    """Create default group settings row if it doesn't exist yet."""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO group_settings (chat_id) VALUES (?)",
            (chat_id,),
        )
        conn.commit()


def get_group_settings(chat_id):
    """Return all settings for a group as a dict. Creates defaults if missing."""
    ensure_group_settings(chat_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM group_settings WHERE chat_id=?", (chat_id,)
        ).fetchone()
        return dict(row)


def update_group_setting(chat_id, key, value):
    """Update a single group setting by key. Creates defaults if group missing."""
    if key not in GROUP_DEFAULT_SETTINGS:
        raise ValueError(f"Invalid group setting key: {key}")
    ensure_group_settings(chat_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE group_settings SET {key}=? WHERE chat_id=?",
            (value, chat_id),
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Whisper spam rate limiting
# ─────────────────────────────────────────────────────────────────────────────

SPAM_BLOCK_MESSAGE = (
    "⏳ لقد تجاوزت الحد المسموح. انتظر قليلاً قبل إرسال همسة جديدة."
)


def record_whisper_timestamp(user_id: int, chat_id: int) -> None:
    """Record the current time as a whisper creation event for rate-limit tracking."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_timestamps (user_id, chat_id) VALUES (?, ?)",
            (user_id, chat_id),
        )
        conn.commit()


def check_whisper_rate_limit(user_id: int, chat_id: int) -> tuple:
    """
    Check whether the user is within the per-group whisper rate limit.

    Returns:
        (allowed: bool, count: int)
        * allowed — True if the user can create another whisper.
        * count   — number of whispers created within the current window.

    The function reads the group's spam_limit_enabled / spam_limit_count /
    spam_limit_window_seconds settings.  If anti-spam is disabled for the
    group, it always returns (True, 0).
    """
    gs = get_group_settings(chat_id)
    if not gs.get("spam_limit_enabled", 1):
        return True, 0

    limit = gs.get("spam_limit_count", 5)
    window = gs.get("spam_limit_window_seconds", 60)

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM whisper_timestamps "
            "WHERE user_id=? AND chat_id=? AND created_at >= ?",
            (user_id, chat_id, cutoff),
        ).fetchone()[0]
    return count < limit, count


# ─────────────────────────────────────────────────────────────────────────────
# Mandatory channels
# ─────────────────────────────────────────────────────────────────────────────

def get_mandatory_channels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM mandatory_channels").fetchall()


def add_mandatory_channel(channel_id, channel_name=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO mandatory_channels (channel_id, channel_name)"
            " VALUES (?, ?)",
            (channel_id, channel_name),
        )
        conn.commit()


def remove_mandatory_channel(channel_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM mandatory_channels WHERE channel_id=?", (channel_id,)
        )
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Statistics
# ─────────────────────────────────────────────────────────────────────────────

def get_stats():
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        banned_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_banned=1"
        ).fetchone()[0]
        total_whispers = conn.execute("SELECT COUNT(*) FROM whispers").fetchone()[0]
        total_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers"
        ).fetchone()[0]
        today = datetime.now(timezone.utc).date().isoformat()
        new_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        whispers_today = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        return {
            "total_users":    total_users,
            "banned_users":   banned_users,
            "active_users":   total_users - banned_users,
            "total_whispers": total_whispers,
            "total_reads":    total_reads,
            "new_today":      new_today,
            "whispers_today": whispers_today,
        }


def get_user_stats(user_id):
    """Personal statistics for one user."""
    with get_conn() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (user_id,)
        ).fetchone()[0]
        received_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE w.sender_id=? AND wr.user_id != ?",
            (user_id, user_id),
        ).fetchone()[0]
        read_others = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE wr.user_id=? AND w.sender_id != ?",
            (user_id, user_id),
        ).fetchone()[0]
        curious_count = conn.execute(
            "SELECT COUNT(*) FROM curious_ones co "
            "JOIN whispers w ON w.whisper_id=co.whisper_id "
            "WHERE w.sender_id=?",
            (user_id,),
        ).fetchone()[0]
        locked_count = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=? AND is_locked=1",
            (user_id,),
        ).fetchone()[0]
        type_counts = conn.execute(
            "SELECT whisper_type, COUNT(*) as cnt FROM whispers "
            "WHERE sender_id=? GROUP BY whisper_type",
            (user_id,),
        ).fetchall()
        types = {row["whisper_type"]: row["cnt"] for row in type_counts}
        return {
            "sent":            sent,
            "received_reads":  received_reads,
            "read_others":     read_others,
            "curious_on_mine": curious_count,
            "locked":          locked_count,
            "type_everyone":   types.get("everyone", 0),
            "type_first_one":  types.get("first_one", 0),
            "type_first_three": types.get("first_three", 0),
            "type_custom":     types.get("custom", 0),
        }


def get_group_stats():
    """Aggregate statistics for the entire group (all whispers + readers)."""
    with get_conn() as conn:
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(hours=24)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        whispers_last_24h = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= ?", (day_ago,)
        ).fetchone()[0]

        whispers_last_7d = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= ?", (week_ago,)
        ).fetchone()[0]

        total_whispers = conn.execute(
            "SELECT COUNT(*) FROM whispers"
        ).fetchone()[0]

        most_used = conn.execute(
            "SELECT whisper_type, COUNT(*) as cnt FROM whispers"
            " GROUP BY whisper_type ORDER BY cnt DESC LIMIT 1"
        ).fetchone()
        most_used_whisper_type = most_used["whisper_type"] if most_used else None

        top_readers_raw = conn.execute(
            "SELECT wr.user_id, u.username, u.first_name, COUNT(*) as read_count"
            " FROM whisper_readers wr"
            " LEFT JOIN users u ON u.user_id = wr.user_id"
            " GROUP BY wr.user_id"
            " ORDER BY read_count DESC LIMIT 5"
        ).fetchall()
        top_readers = [dict(r) for r in top_readers_raw]

        total_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers"
        ).fetchone()[0]

        average_reads_per_whisper = round(total_reads / total_whispers, 2) if total_whispers else 0.0

        return {
            "whispers_last_24h": whispers_last_24h,
            "whispers_last_7d": whispers_last_7d,
            "most_used_whisper_type": most_used_whisper_type,
            "top_readers": top_readers,
            "average_reads_per_whisper": average_reads_per_whisper,
        }


def get_detailed_stats():
    """Extended read-only statistics for the advanced admin dashboard."""
    with get_conn() as conn:
        # ── Users ──────────────────────────────────────────────────────────
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        today = datetime.now(timezone.utc).date().isoformat()
        new_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        week_ago = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        try:
            active_users = conn.execute(
                "SELECT COUNT(DISTINCT user_id) FROM ("
                "  SELECT sender_id AS user_id FROM whispers WHERE created_at >= ?"
                "  UNION"
                "  SELECT user_id FROM whisper_readers WHERE read_at >= ?"
                ")",
                (week_ago, week_ago),
            ).fetchone()[0]
        except Exception:
            active_users = 0

        # ── Whispers ───────────────────────────────────────────────────────
        total_whispers = conn.execute("SELECT COUNT(*) FROM whispers").fetchone()[0]
        whispers_today = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= ?", (today,)
        ).fetchone()[0]
        type_rows = conn.execute(
            "SELECT whisper_type, COUNT(*) as cnt FROM whispers GROUP BY whisper_type"
        ).fetchall()
        types = {row["whisper_type"]: row["cnt"] for row in type_rows}

        # ── Reads ──────────────────────────────────────────────────────────
        total_reads = conn.execute("SELECT COUNT(*) FROM whisper_readers").fetchone()[0]
        avg_reads = round(total_reads / total_whispers, 2) if total_whispers else 0.0

        # ── Likes / Dislikes (enterprise tables — may not exist) ───────────
        total_likes = 0
        total_dislikes = 0
        try:
            total_likes = conn.execute(
                "SELECT COUNT(*) FROM whisper_favorites"
            ).fetchone()[0]
        except Exception:
            pass
        try:
            total_dislikes = conn.execute(
                "SELECT COUNT(*) FROM whisper_dislikes"
            ).fetchone()[0]
        except Exception:
            pass
        interaction_rate = 0.0
        denom = total_reads or total_whispers
        if denom:
            interaction_rate = round((total_likes + total_dislikes) / denom * 100, 2)

        # ── System ─────────────────────────────────────────────────────────
        db_type = "PostgreSQL" if USE_POSTGRES else "SQLite"
        conn_status = "✅ متصل"

        return {
            "total_users":          total_users,
            "new_today":            new_today,
            "active_users":         active_users,
            "total_whispers":       total_whispers,
            "whispers_today":       whispers_today,
            "type_everyone":        types.get("everyone", 0),
            "type_first_one":       types.get("first_one", 0),
            "type_first_three":     types.get("first_three", 0),
            "type_custom":          types.get("custom", 0),
            "total_reads":          total_reads,
            "avg_reads_per_whisper": avg_reads,
            "total_likes":          total_likes,
            "total_dislikes":       total_dislikes,
            "interaction_rate":     interaction_rate,
            "db_type":              db_type,
            "conn_status":          conn_status,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Auto-delete scheduler helper
# ─────────────────────────────────────────────────────────────────────────────

def delete_expired_whispers():
    """Delete whispers whose auto_delete_at has passed. Returns count deleted."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT whisper_id FROM whispers "
            "WHERE auto_delete_at IS NOT NULL AND auto_delete_at <= ?",
            (now,),
        ).fetchall()
        count = len(rows)
        for row in rows:
            wid = row["whisper_id"]
            conn.execute("DELETE FROM whisper_readers WHERE whisper_id=?", (wid,))
            conn.execute("DELETE FROM curious_ones WHERE whisper_id=?", (wid,))
            conn.execute("DELETE FROM whispers WHERE whisper_id=?", (wid,))
        conn.commit()
    return count


# ─────────────────────────────────────────────────────────────────────────────
# Pending media whispers (Media Wizard v2.2.0)
# ─────────────────────────────────────────────────────────────────────────────

def store_pending_media(user_id, message_type, file_id, caption=None, content=None):
    """Store a pending media whisper. Returns the pending_id."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO pending_media_whispers
               (user_id, message_type, file_id, caption, content)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, message_type, file_id, caption, content),
        )
        conn.commit()
        return conn.execute("SELECT last_insert_rowid()").fetchone()[0]


def get_pending_media(user_id):
    """Return the most recent pending media for a user, or None."""
    with get_conn() as conn:
        return conn.execute(
            """SELECT * FROM pending_media_whispers
               WHERE user_id=? ORDER BY id DESC LIMIT 1""",
            (user_id,),
        ).fetchone()


def get_pending_media_by_id(pending_id):
    """Return a pending media record by its ID."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_media_whispers WHERE id=?",
            (pending_id,),
        ).fetchone()


def delete_pending_media(user_id):
    """Delete all pending media for a user."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE user_id=?",
            (user_id,),
        )
        conn.commit()


def delete_pending_media_by_id(pending_id):
    """Delete a specific pending media record."""
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE id=?",
            (pending_id,),
        )
        conn.commit()


def cleanup_stale_pending_media(hours=1):
    """Delete pending media older than the given number of hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE created_at <= ?",
            (cutoff,),
        )
        conn.commit()


# ── PostgreSQL shadow adapter ────────────────────────────────────────────
from database.postgres import USE_POSTGRES
if USE_POSTGRES:
    from database.pg_core import *  # noqa: F401, F403
