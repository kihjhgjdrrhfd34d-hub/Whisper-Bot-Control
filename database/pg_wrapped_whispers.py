import json
import logging
from database.postgres import get_conn as _pg_get_conn

logger = logging.getLogger(__name__)


def init_wrapped_whispers_db():
    with _pg_get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS ww_drafts (
                id                SERIAL PRIMARY KEY,
                user_id           BIGINT NOT NULL,
                cover_code        TEXT DEFAULT '',
                character_code    TEXT DEFAULT '',
                content           TEXT DEFAULT '',
                target_chat_id    BIGINT,
                target_chat_title TEXT DEFAULT '',
                whisper_type      TEXT,
                target_users      TEXT DEFAULT '[]',
                max_readers       INTEGER DEFAULT 0,
                step              INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (NOW()),
                updated_at        TEXT DEFAULT (NOW())
            );

            CREATE INDEX IF NOT EXISTS idx_ww_drafts_user
                ON ww_drafts(user_id);

            CREATE TABLE IF NOT EXISTS ww_inline_packages (
                id              TEXT PRIMARY KEY,
                user_id         BIGINT NOT NULL,
                cover_code      TEXT DEFAULT '',
                character_code  TEXT DEFAULT '',
                content         TEXT DEFAULT '',
                created_at      TEXT DEFAULT (NOW())
            );

            CREATE INDEX IF NOT EXISTS idx_ww_packages_user
                ON ww_inline_packages(user_id);
        """)
        conn.commit()
    logger.info("Wrapped whispers DB initialised (PG)")


def create_draft(user_id):
    delete_draft(user_id)
    with _pg_get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO ww_drafts (user_id) VALUES (%s) RETURNING *",
            (user_id,),
        )
        conn.commit()
        row = cur.fetchone()
        return dict(row) if row else None


def get_draft(user_id):
    with _pg_get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ww_drafts WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_draft_step(user_id, step):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET step=%s, updated_at=NOW() WHERE user_id=%s",
            (step, user_id),
        )
        conn.commit()


def update_draft_cover(user_id, cover_code):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET cover_code=%s, step=2, updated_at=NOW() WHERE user_id=%s",
            (cover_code, user_id),
        )
        conn.commit()


def update_draft_character(user_id, character_code):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET character_code=%s, step=3, updated_at=NOW() WHERE user_id=%s",
            (character_code, user_id),
        )
        conn.commit()


def update_draft_content(user_id, content):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET content=%s, step=4, updated_at=NOW() WHERE user_id=%s",
            (content, user_id),
        )
        conn.commit()


def update_draft_target(user_id, chat_id, chat_title):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET target_chat_id=%s, target_chat_title=%s, step=5, updated_at=NOW() WHERE user_id=%s",
            (chat_id, chat_title, user_id),
        )
        conn.commit()


def update_draft_type(user_id, whisper_type, target_users=None, max_readers=0):
    targets = json.dumps(target_users or [])
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE ww_drafts SET whisper_type=%s, target_users=%s, max_readers=%s, step=6, updated_at=NOW() WHERE user_id=%s",
            (whisper_type, targets, max_readers, user_id),
        )
        conn.commit()


def delete_draft(user_id):
    with _pg_get_conn() as conn:
        conn.execute("DELETE FROM ww_drafts WHERE user_id=%s", (user_id,))
        conn.commit()


def create_inline_package(user_id, cover_code, character_code, content):
    import uuid
    pkg_id = str(uuid.uuid4())[:8].upper()
    with _pg_get_conn() as conn:
        conn.execute(
            "INSERT INTO ww_inline_packages (id, user_id, cover_code, character_code, content) VALUES (%s, %s, %s, %s, %s)",
            (pkg_id, user_id, cover_code, character_code, content),
        )
        conn.commit()
    return pkg_id


def get_inline_package(package_id):
    with _pg_get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM ww_inline_packages WHERE id=%s",
            (package_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_inline_package(package_id):
    with _pg_get_conn() as conn:
        conn.execute("DELETE FROM ww_inline_packages WHERE id=%s", (package_id,))
        conn.commit()


def update_whisper_cover_character(whisper_id, cover_code, character_code):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whispers SET cover_code=%s, character_code=%s WHERE whisper_id=%s",
            (cover_code, character_code, whisper_id),
        )
        conn.commit()
