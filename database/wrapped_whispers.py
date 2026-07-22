import json
import logging
import os
from database import get_conn

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data")
COVERS_PATH = os.path.join(DATA_DIR, "wrapped_covers.json")
CHARACTERS_PATH = os.path.join(DATA_DIR, "wrapped_characters.json")


def _load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.error(f"Failed to load {path}: {exc}")
        return []


def get_all_covers():
    return _load_json(COVERS_PATH)


def get_available_covers(user_xp=0):
    covers = get_all_covers()
    return [c for c in covers if c.get("min_xp", 0) <= user_xp]


def get_cover(code):
    covers = get_all_covers()
    for c in covers:
        if c["code"] == code:
            return c
    return None


def get_all_characters():
    return _load_json(CHARACTERS_PATH)


def get_available_characters(user_xp=0):
    chars = get_all_characters()
    return [c for c in chars if c.get("min_xp", 0) <= user_xp]


def get_character(code):
    chars = get_all_characters()
    for c in chars:
        if c["code"] == code:
            return c
    return None


def init_wrapped_whispers_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ww_drafts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                cover_code        TEXT DEFAULT '',
                character_code    TEXT DEFAULT '',
                content           TEXT DEFAULT '',
                target_chat_id    INTEGER,
                target_chat_title TEXT DEFAULT '',
                whisper_type      TEXT,
                target_users      TEXT DEFAULT '[]',
                max_readers       INTEGER DEFAULT 0,
                step              INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_ww_drafts_user
                ON ww_drafts(user_id);

            CREATE TABLE IF NOT EXISTS ww_inline_packages (
                id              TEXT PRIMARY KEY,
                user_id         INTEGER NOT NULL,
                cover_code      TEXT DEFAULT '',
                character_code  TEXT DEFAULT '',
                content         TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_ww_packages_user
                ON ww_inline_packages(user_id);
        """)
        conn.commit()
    logger.info("Wrapped whispers DB initialised (ww_drafts + ww_inline_packages)")


def create_draft(user_id):
    delete_draft(user_id)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ww_drafts (user_id) VALUES (?)",
            (user_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM ww_drafts WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def get_draft(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ww_drafts WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_draft_step(user_id, step):
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET step=?, updated_at=datetime('now') WHERE user_id=?",
            (step, user_id),
        )
        conn.commit()


def update_draft_cover(user_id, cover_code):
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET cover_code=?, step=2, updated_at=datetime('now') WHERE user_id=?",
            (cover_code, user_id),
        )
        conn.commit()


def update_draft_character(user_id, character_code):
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET character_code=?, step=3, updated_at=datetime('now') WHERE user_id=?",
            (character_code, user_id),
        )
        conn.commit()


def update_draft_content(user_id, content):
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET content=?, step=4, updated_at=datetime('now') WHERE user_id=?",
            (content, user_id),
        )
        conn.commit()


def update_draft_target(user_id, chat_id, chat_title):
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET target_chat_id=?, target_chat_title=?, step=5, updated_at=datetime('now') WHERE user_id=?",
            (chat_id, chat_title, user_id),
        )
        conn.commit()


def update_draft_type(user_id, whisper_type, target_users=None, max_readers=0):
    targets = json.dumps(target_users or [])
    with get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET whisper_type=?, target_users=?, max_readers=?, step=6, updated_at=datetime('now') WHERE user_id=?",
            (whisper_type, targets, max_readers, user_id),
        )
        conn.commit()


def delete_draft(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM ww_drafts WHERE user_id=?", (user_id,))
        conn.commit()


def create_inline_package(user_id, cover_code, character_code, content):
    import uuid
    pkg_id = str(uuid.uuid4())[:8].upper()
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ww_inline_packages (id, user_id, cover_code, character_code, content) VALUES (?, ?, ?, ?, ?)",
            (pkg_id, user_id, cover_code, character_code, content),
        )
        conn.commit()
    return pkg_id


def get_inline_package(package_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ww_inline_packages WHERE id=?",
            (package_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_inline_package(package_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM ww_inline_packages WHERE id=?", (package_id,))
        conn.commit()


def cleanup_stale_inline_packages(hours=1):
    from datetime import datetime, timezone, timedelta
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM ww_inline_packages WHERE created_at <= ?",
            (cutoff,),
        )
        conn.commit()


def update_whisper_cover_character(whisper_id, cover_code, character_code):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whispers SET cover_code=?, character_code=? WHERE whisper_id=?",
            (cover_code, character_code, whisper_id),
        )
        conn.commit()


from database.postgres import USE_POSTGRES
if USE_POSTGRES:
    try:
        from database.pg_wrapped_whispers import *
    except ImportError:
        pass
