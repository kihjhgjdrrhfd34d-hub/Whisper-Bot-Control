"""
database/pg_personal.py — PostgreSQL implementation of all functions from database/personal.py
"""

import uuid
from contextlib import contextmanager
from database.postgres import get_conn as _pg_get_conn, USE_POSTGRES


@contextmanager
def get_conn():
    conn = _pg_get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_personal_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS personal_whispers (
                id              SERIAL PRIMARY KEY,
                whisper_id      TEXT NOT NULL UNIQUE,
                sender_id       BIGINT NOT NULL,
                recipient_id    BIGINT NOT NULL,
                content         TEXT NOT NULL,
                is_read         INTEGER DEFAULT 0,
                read_at         TEXT,
                created_at      TEXT DEFAULT (NOW())
            );
            CREATE INDEX IF NOT EXISTS idx_pw_sender
                ON personal_whispers(sender_id);
            CREATE INDEX IF NOT EXISTS idx_pw_recipient
                ON personal_whispers(recipient_id);
        """)
        conn.commit()


def create_personal_whisper(sender_id, recipient_id, content):
    wid = str(uuid.uuid4())[:12]
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO personal_whispers"
            " (whisper_id, sender_id, recipient_id, content)"
            " VALUES (%s, %s, %s, %s)",
            (wid, sender_id, recipient_id, content),
        )
        conn.commit()
    return wid


def get_personal_whisper(whisper_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT pw.*, u.username AS sender_username, u.first_name AS sender_first_name "
            "FROM personal_whispers pw "
            "LEFT JOIN users u ON u.user_id = pw.sender_id "
            "WHERE pw.whisper_id=%s",
            (whisper_id,),
        ).fetchone()
        if row is not None:
            row = dict(row)
            row["sender_name"] = row.get("sender_first_name") or row.get("sender_username") or str(row["sender_id"])
        return row


def get_user_inbox(user_id, limit=10, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pw.*, u.username AS sender_username, u.first_name AS sender_first_name "
            "FROM personal_whispers pw "
            "LEFT JOIN users u ON u.user_id = pw.sender_id "
            "WHERE pw.recipient_id = %s "
            "ORDER BY pw.created_at DESC "
            "LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM personal_whispers WHERE recipient_id=%s",
            (user_id,),
        ).fetchone()["count"]
        result = []
        for row in rows:
            row = dict(row)
            row["sender_name"] = row.get("sender_first_name") or row.get("sender_username") or str(row["sender_id"])
            result.append(row)
        return result, total


def get_user_sent(user_id, limit=10, offset=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT pw.*, u.username AS recipient_username, u.first_name AS recipient_first_name "
            "FROM personal_whispers pw "
            "LEFT JOIN users u ON u.user_id = pw.recipient_id "
            "WHERE pw.sender_id = %s "
            "ORDER BY pw.created_at DESC "
            "LIMIT %s OFFSET %s",
            (user_id, limit, offset),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) FROM personal_whispers WHERE sender_id=%s",
            (user_id,),
        ).fetchone()["count"]
        result = []
        for row in rows:
            row = dict(row)
            row["recipient_name"] = row.get("recipient_first_name") or row.get("recipient_username") or str(row["recipient_id"])
            result.append(row)
        return result, total


def mark_as_read(whisper_id, user_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE personal_whispers "
            "SET is_read = 1, read_at = NOW() "
            "WHERE whisper_id = %s AND recipient_id = %s",
            (whisper_id, user_id),
        )
        conn.commit()


def count_unread(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM personal_whispers WHERE recipient_id=%s AND is_read=0",
            (user_id,),
        ).fetchone()
        return row["count"] if row else 0
