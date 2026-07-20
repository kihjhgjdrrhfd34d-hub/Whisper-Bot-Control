import sqlite3
from contextlib import contextmanager
from config import DATABASE_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_envelope_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_drafts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL,
                content         TEXT NOT NULL,
                category        TEXT DEFAULT '',
                template_name   TEXT DEFAULT '',
                envelope_style  TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wd_user
                ON whisper_drafts(user_id);
        """)
        conn.commit()


def create_draft(user_id, content, category='', template_name='', envelope_style=''):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO whisper_drafts
               (user_id, content, category, template_name, envelope_style)
               VALUES (?, ?, ?, ?, ?)""",
            (user_id, content, category, template_name, envelope_style),
        )
        conn.commit()


def get_draft(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_drafts WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_draft(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_drafts WHERE user_id=?", (user_id,))
        conn.commit()


from database.postgres import USE_POSTGRES
if USE_POSTGRES:
    from database.pg_envelope import *
