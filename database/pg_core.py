"""
database/pg_core.py — PostgreSQL implementation of all functions from database/__init__.py
Shadow adapter: when imported with `from .pg_core import *`, overrides the SQLite versions.
"""

import json
import uuid
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from config import DEFAULT_SETTINGS, GROUP_DEFAULT_SETTINGS
from database.postgres import get_conn, USE_POSTGRES

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Schema initialisation
# ═══════════════════════════════════════════════════════════════════════

def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id     BIGINT PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                created_at  TEXT DEFAULT (NOW()),
                is_banned   INTEGER DEFAULT 0,
                started     INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id      TEXT PRIMARY KEY,
                sender_id       BIGINT NOT NULL,
                content         TEXT NOT NULL,
                whisper_type    TEXT NOT NULL,
                target_users    TEXT DEFAULT '[]',
                max_readers     INTEGER DEFAULT 0,
                is_locked       INTEGER DEFAULT 0,
                created_at      TEXT DEFAULT (NOW()),
                auto_delete_at  TEXT,
                is_destructive  INTEGER DEFAULT 0,
                message_type    TEXT,
                file_id         TEXT,
                caption         TEXT,
                location_lat    DOUBLE PRECISION,
                location_lon    DOUBLE PRECISION,
                FOREIGN KEY (sender_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS whisper_readers (
                id          SERIAL PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                user_id     BIGINT NOT NULL,
                read_at     TEXT DEFAULT (NOW()),
                UNIQUE(whisper_id, user_id),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE TABLE IF NOT EXISTS curious_ones (
                id          SERIAL PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                user_id     BIGINT NOT NULL,
                tried_at    TEXT DEFAULT (NOW()),
                UNIQUE(whisper_id, user_id),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mandatory_channels (
                id           SERIAL PRIMARY KEY,
                channel_id   TEXT NOT NULL UNIQUE,
                channel_name TEXT
            );

            CREATE TABLE IF NOT EXISTS broadcasts (
                id         SERIAL PRIMARY KEY,
                content    TEXT,
                media_type TEXT,
                file_id    TEXT,
                sent_at    TEXT DEFAULT (NOW()),
                sent_by    BIGINT
            );

            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id                 BIGINT PRIMARY KEY,
                public_whispers_enabled INTEGER DEFAULT 1,
                anonymous_enabled       INTEGER DEFAULT 1,
                read_notifications      INTEGER DEFAULT 1,
                auto_delete_minutes     INTEGER DEFAULT 0,
                spam_limit_enabled      INTEGER DEFAULT 1,
                spam_limit_count        INTEGER DEFAULT 5,
                spam_limit_window_seconds INTEGER DEFAULT 60
            );

            CREATE TABLE IF NOT EXISTS whisper_timestamps (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                chat_id     BIGINT NOT NULL,
                created_at  TEXT DEFAULT (NOW()),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );

            CREATE TABLE IF NOT EXISTS pending_media_whispers (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL,
                message_type TEXT NOT NULL,
                file_id      TEXT NOT NULL,
                caption      TEXT,
                content      TEXT,
                created_at   TEXT DEFAULT (NOW()),
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)

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

        for key, val in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                (key, val),
            )
        conn.commit()

    try:
        from database.replies import init_replies_db
        init_replies_db()
    except Exception:
        pass
    _run_migrations()


def _run_migrations():
    with get_conn() as conn:
        tables = {
            r["table_name"] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            ).fetchall()
        }

        if "users" in tables:
            cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='users' AND table_schema='public'"
            ).fetchall()}
            if "started" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN started INTEGER DEFAULT 0")

        if "settings" in tables:
            for key, val in DEFAULT_SETTINGS.items():
                conn.execute(
                    "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
                    (key, val),
                )

        if "whispers" in tables:
            cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='whispers' AND table_schema='public'"
            ).fetchall()}
            for col_name, col_type in [
                ("is_destructive", "INTEGER DEFAULT 0"),
                ("is_closed", "INTEGER DEFAULT 0"),
                ("is_pinned", "INTEGER DEFAULT 0"),
                ("message_type", "TEXT"),
                ("file_id", "TEXT"),
                ("caption", "TEXT"),
                ("location_lat", "DOUBLE PRECISION"),
                ("location_lon", "DOUBLE PRECISION"),
                ("media_type", "TEXT"),
                ("group_chat_id", "BIGINT"),
                ("group_message_id", "INTEGER"),
                ("group_inline_message_id", "TEXT"),
            ]:
                if col_name not in cols:
                    conn.execute(
                        f"ALTER TABLE whispers ADD COLUMN {col_name} {col_type}"
                    )

        existing_cols = set()
        if "whispers" in tables:
            existing_cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='whispers' AND table_schema='public'"
            ).fetchall()}

        if "group_settings" in tables:
            gs_cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='group_settings' AND table_schema='public'"
            ).fetchall()}
            for col_name, col_type in [
                ("spam_limit_enabled", "INTEGER DEFAULT 1"),
                ("spam_limit_count", "INTEGER DEFAULT 5"),
                ("spam_limit_window_seconds", "INTEGER DEFAULT 60"),
            ]:
                if col_name not in gs_cols:
                    conn.execute(
                        f"ALTER TABLE group_settings ADD COLUMN {col_name} {col_type}"
                    )

        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Users
# ═══════════════════════════════════════════════════════════════════════

def upsert_user(user_id, username=None, first_name=None, last_name=None):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT(user_id) DO UPDATE SET
                username=EXCLUDED.username,
                first_name=EXCLUDED.first_name,
                last_name=EXCLUDED.last_name
            """,
            (user_id, username, first_name, last_name),
        )
        conn.commit()


def is_new_user(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT started FROM users WHERE user_id=%s", (user_id,)
        ).fetchone()
        if row is None:
            return True
        return row["started"] == 0


def mark_user_started(user_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE users SET started=1 WHERE user_id=%s", (user_id,)
        )
        conn.commit()


def get_user(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id=%s", (user_id,)
        ).fetchone()


def is_banned(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_banned FROM users WHERE user_id=%s", (user_id,)
        ).fetchone()
        return bool(row and row["is_banned"] == 1)


def ban_user(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_banned=1 WHERE user_id=%s", (user_id,))
        conn.commit()


def unban_user(user_id):
    with get_conn() as conn:
        conn.execute("UPDATE users SET is_banned=0 WHERE user_id=%s", (user_id,))
        conn.commit()


def get_all_users(page=0, per_page=20):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM users ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (per_page, page * per_page),
        ).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM users").fetchone()["count"]
        return rows, total


def search_users(query):
    with get_conn() as conn:
        like = f"%{query}%"
        return conn.execute(
            "SELECT * FROM users WHERE username LIKE %s OR first_name LIKE %s"
            " OR CAST(user_id AS TEXT)=%s",
            (like, like, query),
        ).fetchall()


# ═══════════════════════════════════════════════════════════════════════
# Whispers
# ═══════════════════════════════════════════════════════════════════════

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
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
            "SELECT * FROM whispers WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()


def update_whisper_content(whisper_id, content):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whispers SET content=%s WHERE whisper_id=%s", (content, whisper_id)
        )
        conn.commit()


def update_whisper_group_message(whisper_id, chat_id=None, message_id=None, inline_message_id=None):
    with get_conn() as conn:
        if chat_id is not None:
            conn.execute("UPDATE whispers SET group_chat_id=%s WHERE whisper_id=%s", (chat_id, whisper_id))
        if message_id is not None:
            conn.execute("UPDATE whispers SET group_message_id=%s WHERE whisper_id=%s", (message_id, whisper_id))
        if inline_message_id is not None:
            conn.execute("UPDATE whispers SET group_inline_message_id=%s WHERE whisper_id=%s", (inline_message_id, whisper_id))
        conn.commit()


def toggle_whisper_lock(whisper_id):
    with get_conn() as conn:
        w = conn.execute(
            "SELECT is_locked FROM whispers WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()
        if w is None:
            return None
        new_state = 0 if w["is_locked"] else 1
        conn.execute(
            "UPDATE whispers SET is_locked=%s WHERE whisper_id=%s",
            (new_state, whisper_id),
        )
        conn.commit()
        return new_state


def lock_whisper(whisper_id):
    with get_conn() as conn:
        conn.execute("UPDATE whispers SET is_locked=1 WHERE whisper_id=%s", (whisper_id,))
        conn.commit()


def close_whisper(whisper_id):
    with get_conn() as conn:
        conn.execute("UPDATE whispers SET is_closed=1, is_locked=1 WHERE whisper_id=%s", (whisper_id,))
        conn.commit()


def is_whisper_closed(whisper_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT is_closed FROM whispers WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()
        return bool(row and row["is_closed"])


def toggle_pin_whisper(whisper_id):
    with get_conn() as conn:
        w = conn.execute(
            "SELECT is_pinned FROM whispers WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()
        if w is None:
            return None
        new_state = 0 if w["is_pinned"] else 1
        conn.execute(
            "UPDATE whispers SET is_pinned=%s WHERE whisper_id=%s",
            (new_state, whisper_id),
        )
        conn.commit()
        return new_state


def get_pinned_whispers(sender_id, limit=10, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=%s AND is_pinned=1"
            " ORDER BY created_at DESC LIMIT %s OFFSET %s",
            (sender_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=%s AND is_pinned=1",
            (sender_id,),
        ).fetchone()["count"]
        return rows, total


def get_sender_whispers(sender_id, limit=10, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=%s"
            " ORDER BY is_pinned DESC, created_at DESC LIMIT %s OFFSET %s",
            (sender_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=%s",
            (sender_id,),
        ).fetchone()["count"]
        return rows, total


def delete_whisper(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=%s", (whisper_id,))
        conn.execute("DELETE FROM curious_ones WHERE whisper_id=%s", (whisper_id,))
        try:
            conn.execute("DELETE FROM whisper_replies WHERE whisper_id=%s", (whisper_id,))
        except Exception:
            pass
        conn.execute("DELETE FROM whispers WHERE whisper_id=%s", (whisper_id,))
        conn.commit()


def clear_whisper_readers(whisper_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers WHERE whisper_id=%s", (whisper_id,))
        conn.commit()


def add_reader(whisper_id, user_id):
    add_reader_if_new(whisper_id, user_id)


def add_reader_if_new(whisper_id: str, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO whisper_readers (whisper_id, user_id) VALUES (%s, %s)"
            " ON CONFLICT (whisper_id, user_id) DO NOTHING",
            (whisper_id, user_id),
        )
        inserted = cur.rowcount
        conn.commit()
    return inserted == 1


def record_whisper_read(whisper_id: str, user_id: int) -> bool:
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
                    "UPDATE whispers SET is_locked=1 WHERE whisper_id=%s",
                    (whisper_id,),
                )
                conn.commit()
        return True

    return True


def get_readers(whisper_id):
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT wr.user_id, u.username, u.first_name "
            "FROM whisper_readers wr "
            "LEFT JOIN users u ON u.user_id=wr.user_id "
            "WHERE wr.whisper_id=%s",
            (whisper_id,),
        ).fetchall()]


def reader_count(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_readers WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchone()["count"]


def add_curious(whisper_id, user_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO curious_ones (whisper_id, user_id) VALUES (%s, %s)"
            " ON CONFLICT (whisper_id, user_id) DO NOTHING",
            (whisper_id, user_id),
        )
        conn.commit()


def get_curious_ones(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT co.user_id, co.tried_at, u.username, u.first_name "
            "FROM curious_ones co "
            "LEFT JOIN users u ON u.user_id=co.user_id "
            "WHERE co.whisper_id=%s "
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


# ═══════════════════════════════════════════════════════════════════════
# Settings
# ═══════════════════════════════════════════════════════════════════════

def get_setting(key):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key=%s", (key,)
        ).fetchone()
        return row["value"] if row else DEFAULT_SETTINGS.get(key)


def get_all_settings(keys: list) -> dict:
    result = {k: DEFAULT_SETTINGS.get(k) for k in keys}
    placeholders = ",".join("%s" for _ in keys)
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
            "INSERT INTO settings (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            (key, value),
        )
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Group Settings
# ═══════════════════════════════════════════════════════════════════════

def ensure_group_settings(chat_id):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO group_settings (chat_id) VALUES (%s)"
            " ON CONFLICT (chat_id) DO NOTHING",
            (chat_id,),
        )
        conn.commit()


def get_group_settings(chat_id):
    ensure_group_settings(chat_id)
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM group_settings WHERE chat_id=%s", (chat_id,)
        ).fetchone()
        return dict(row)


def update_group_setting(chat_id, key, value):
    if key not in GROUP_DEFAULT_SETTINGS:
        raise ValueError(f"Invalid group setting key: {key}")
    ensure_group_settings(chat_id)
    with get_conn() as conn:
        conn.execute(
            f"UPDATE group_settings SET {key}=%s WHERE chat_id=%s",
            (value, chat_id),
        )
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Whisper spam rate limiting
# ═══════════════════════════════════════════════════════════════════════

SPAM_BLOCK_MESSAGE = (
    "⏳ لقد تجاوزت الحد المسموح. انتظر قليلاً قبل إرسال همسة جديدة."
)


def record_whisper_timestamp(user_id: int, chat_id: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_timestamps (user_id, chat_id) VALUES (%s, %s)",
            (user_id, chat_id),
        )
        conn.commit()


def check_whisper_rate_limit(user_id: int, chat_id: int) -> tuple:
    gs = get_group_settings(chat_id)
    if not gs.get("spam_limit_enabled", 1):
        return True, 0

    limit = gs.get("spam_limit_count", 5)
    window = gs.get("spam_limit_window_seconds", 60)

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window)).strftime("%Y-%m-%d %H:%M:%S")
    with get_conn() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM whisper_timestamps "
            "WHERE user_id=%s AND chat_id=%s AND created_at >= %s",
            (user_id, chat_id, cutoff),
        ).fetchone()["count"]
    return count < limit, count


# ═══════════════════════════════════════════════════════════════════════
# Mandatory channels
# ═══════════════════════════════════════════════════════════════════════

def get_mandatory_channels():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM mandatory_channels").fetchall()


def add_mandatory_channel(channel_id, channel_name=""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO mandatory_channels (channel_id, channel_name)"
            " VALUES (%s, %s)"
            " ON CONFLICT (channel_id) DO NOTHING",
            (channel_id, channel_name),
        )
        conn.commit()


def remove_mandatory_channel(channel_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM mandatory_channels WHERE channel_id=%s", (channel_id,)
        )
        conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# Statistics
# ═══════════════════════════════════════════════════════════════════════

def get_stats():
    with get_conn() as conn:
        total_users = conn.execute("SELECT COUNT(*) FROM users").fetchone()["count"]
        banned_users = conn.execute(
            "SELECT COUNT(*) FROM users WHERE is_banned=1"
        ).fetchone()["count"]
        total_whispers = conn.execute("SELECT COUNT(*) FROM whispers").fetchone()["count"]
        total_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers"
        ).fetchone()["count"]
        today = datetime.now(timezone.utc).date().isoformat()
        new_today = conn.execute(
            "SELECT COUNT(*) FROM users WHERE created_at >= %s", (today,)
        ).fetchone()["count"]
        whispers_today = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= %s", (today,)
        ).fetchone()["count"]
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
    with get_conn() as conn:
        sent = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=%s", (user_id,)
        ).fetchone()["count"]
        received_reads = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE w.sender_id=%s AND wr.user_id != %s",
            (user_id, user_id),
        ).fetchone()["count"]
        read_others = conn.execute(
            "SELECT COUNT(*) FROM whisper_readers wr "
            "JOIN whispers w ON w.whisper_id=wr.whisper_id "
            "WHERE wr.user_id=%s AND w.sender_id != %s",
            (user_id, user_id),
        ).fetchone()["count"]
        curious_count = conn.execute(
            "SELECT COUNT(*) FROM curious_ones co "
            "JOIN whispers w ON w.whisper_id=co.whisper_id "
            "WHERE w.sender_id=%s",
            (user_id,),
        ).fetchone()["count"]
        locked_count = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE sender_id=%s AND is_locked=1",
            (user_id,),
        ).fetchone()["count"]
        type_counts = conn.execute(
            "SELECT whisper_type, COUNT(*) as cnt FROM whispers "
            "WHERE sender_id=%s GROUP BY whisper_type",
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
    with get_conn() as conn:
        now = datetime.now(timezone.utc)
        day_ago = (now - timedelta(hours=24)).isoformat()
        week_ago = (now - timedelta(days=7)).isoformat()

        whispers_last_24h = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= %s", (day_ago,)
        ).fetchone()["count"]

        whispers_last_7d = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE created_at >= %s", (week_ago,)
        ).fetchone()["count"]

        total_whispers = conn.execute(
            "SELECT COUNT(*) FROM whispers"
        ).fetchone()["count"]

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
        ).fetchone()["count"]

        average_reads_per_whisper = round(total_reads / total_whispers, 2) if total_whispers else 0.0

        return {
            "whispers_last_24h": whispers_last_24h,
            "whispers_last_7d": whispers_last_7d,
            "most_used_whisper_type": most_used_whisper_type,
            "top_readers": top_readers,
            "average_reads_per_whisper": average_reads_per_whisper,
        }


# ═══════════════════════════════════════════════════════════════════════
# Auto-delete scheduler helper
# ═══════════════════════════════════════════════════════════════════════

def delete_expired_whispers():
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT whisper_id FROM whispers "
            "WHERE auto_delete_at IS NOT NULL AND auto_delete_at <= %s",
            (now,),
        ).fetchall()
        count = len(rows)
        for row in rows:
            wid = row["whisper_id"]
            conn.execute("DELETE FROM whisper_readers WHERE whisper_id=%s", (wid,))
            conn.execute("DELETE FROM curious_ones WHERE whisper_id=%s", (wid,))
            conn.execute("DELETE FROM whispers WHERE whisper_id=%s", (wid,))
        conn.commit()
    return count


# ═══════════════════════════════════════════════════════════════════════
# Pending media whispers
# ═══════════════════════════════════════════════════════════════════════

def store_pending_media(user_id, message_type, file_id, caption=None, content=None):
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO pending_media_whispers"
            " (user_id, message_type, file_id, caption, content)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (user_id, message_type, file_id, caption, content),
        )
        conn.commit()
        return cur.fetchone()[0]


def get_pending_media(user_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_media_whispers"
            " WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()


def get_pending_media_by_id(pending_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM pending_media_whispers WHERE id=%s",
            (pending_id,),
        ).fetchone()


def delete_pending_media(user_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE user_id=%s",
            (user_id,),
        )
        conn.commit()


def delete_pending_media_by_id(pending_id):
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE id=%s",
            (pending_id,),
        )
        conn.commit()


def cleanup_stale_pending_media(hours=1):
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM pending_media_whispers WHERE created_at <= %s",
            (cutoff,),
        )
        conn.commit()
