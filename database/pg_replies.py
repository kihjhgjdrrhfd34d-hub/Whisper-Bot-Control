"""
database/pg_replies.py — PostgreSQL implementation of all functions from database/replies.py
"""

import uuid
import logging
from database.postgres import get_conn, USE_POSTGRES

logger = logging.getLogger(__name__)

MAX_REPLIES_PER_WHISPER = 50

SUPPORTED_MEDIA = {"photo", "video", "voice", "audio", "document", "sticker", "animation", "contact", "location"}


def init_replies_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_replies (
                reply_id    TEXT PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                sender_id   BIGINT NOT NULL,
                parent_reply_id TEXT,
                content     TEXT NOT NULL DEFAULT '',
                media_type  TEXT,
                file_id     TEXT,
                created_at  TEXT DEFAULT (NOW()),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reply_reads (
                id        SERIAL PRIMARY KEY,
                reply_id  TEXT NOT NULL,
                user_id   BIGINT NOT NULL,
                read_at   TEXT DEFAULT (NOW()),
                UNIQUE(reply_id, user_id),
                FOREIGN KEY (reply_id) REFERENCES whisper_replies(reply_id)
                    ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_replies_whisper
                ON whisper_replies(whisper_id);
            CREATE INDEX IF NOT EXISTS idx_replies_sender
                ON whisper_replies(sender_id);
            CREATE INDEX IF NOT EXISTS idx_reply_reads_reply
                ON reply_reads(reply_id);
        """)
        conn.commit()
    _migrate_add_parent_reply_id()
    logger.debug("Replies schema initialised (PostgreSQL).")


def _migrate_add_parent_reply_id() -> None:
    with get_conn() as conn:
        cols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='whisper_replies' AND table_schema='public'"
        ).fetchall()}
        if "parent_reply_id" not in cols:
            conn.execute(
                "ALTER TABLE whisper_replies ADD COLUMN parent_reply_id TEXT"
            )
            conn.commit()


def create_reply(
    whisper_id: str,
    sender_id: int,
    content: str = "",
    media_type=None,
    file_id=None,
    parent_reply_id=None,
):
    if media_type and media_type not in SUPPORTED_MEDIA:
        logger.warning(f"create_reply: unsupported media_type={media_type!r}")
        return None

    with get_conn() as conn:
        parent = conn.execute(
            "SELECT whisper_id FROM whispers WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()
        if not parent:
            logger.debug(f"create_reply: whisper {whisper_id!r} not found")
            return None

        cnt = conn.execute(
            "SELECT COUNT(*) FROM whisper_replies WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchone()["count"]
        if cnt >= MAX_REPLIES_PER_WHISPER:
            logger.debug(f"create_reply: reply cap reached for {whisper_id!r}")
            return None

        rid = str(uuid.uuid4())[:12]
        conn.execute(
            """
            INSERT INTO whisper_replies
                (reply_id, whisper_id, sender_id, parent_reply_id, content, media_type, file_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (rid, whisper_id, sender_id, parent_reply_id, content or "", media_type, file_id),
        )
        conn.commit()
    return rid


def get_reply(reply_id: str):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM whisper_replies WHERE reply_id=%s", (reply_id,)
        ).fetchone()


def get_reply_sender(reply_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sender_id FROM whisper_replies WHERE reply_id=%s", (reply_id,)
        ).fetchone()
        return row["sender_id"] if row else None


def whisper_id_from_reply(reply_id: str):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT whisper_id FROM whisper_replies WHERE reply_id=%s", (reply_id,)
        ).fetchone()
        return row["whisper_id"] if row else None


def get_replies(whisper_id: str) -> list:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM whisper_replies"
            " WHERE whisper_id=%s ORDER BY created_at ASC",
            (whisper_id,),
        ).fetchall()]


def count_replies(whisper_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_replies WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchone()["count"]


def delete_replies_for_whisper(whisper_id: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM whisper_replies WHERE whisper_id=%s", (whisper_id,)
        )
        conn.commit()
    return cur.rowcount


def mark_reply_read(reply_id: str, user_id: int) -> bool:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reply_reads (reply_id, user_id) VALUES (%s, %s)"
            " ON CONFLICT (reply_id, user_id) DO NOTHING",
            (reply_id, user_id),
        )
        inserted = cur.rowcount
        conn.commit()
    return inserted == 1


def can_reply_to_whisper(whisper_id: str, user_id: int):
    with get_conn() as conn:
        w = conn.execute(
            "SELECT sender_id, is_locked, is_closed FROM whispers WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchone()

    if not w:
        return False, "whisper_not_found"

    w = dict(w)
    if w.get("is_closed", 0):
        return False, "whisper_locked"

    if w["is_locked"]:
        return False, "whisper_locked"

    if user_id != w["sender_id"]:
        with get_conn() as conn:
            is_reader = conn.execute(
                "SELECT 1 FROM whisper_readers"
                " WHERE whisper_id=%s AND user_id=%s",
                (whisper_id, user_id),
            ).fetchone()
        if not is_reader:
            return False, "not_participant"

    current = count_replies(whisper_id)
    if current >= MAX_REPLIES_PER_WHISPER:
        return False, "reply_cap_reached"

    return True, "ok"


def get_whisper_participants(whisper_id: str) -> dict:
    with get_conn() as conn:
        w = conn.execute(
            "SELECT sender_id FROM whispers WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchone()
        if not w:
            return {}
        readers = conn.execute(
            "SELECT user_id FROM whisper_readers WHERE whisper_id=%s",
            (whisper_id,),
        ).fetchall()
    return {
        "sender_id": w["sender_id"],
        "reader_ids": [r["user_id"] for r in readers],
    }
