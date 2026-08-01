import sqlite3
from contextlib import contextmanager
from config import DATABASE_PATH


@contextmanager
def get_conn():
    conn = sqlite3.connect(DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA cache_size=-8000")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
                target_chat_id  INTEGER DEFAULT 0,
                status          TEXT DEFAULT 'draft',
                conditions_data TEXT DEFAULT '',
                created_at      TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_wd_user
                ON whisper_drafts(user_id);
        """)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(whisper_drafts)").fetchall()]
        if "target_chat_id" not in cols:
            conn.execute("ALTER TABLE whisper_drafts ADD COLUMN target_chat_id INTEGER DEFAULT 0")
        if "status" not in cols:
            conn.execute("ALTER TABLE whisper_drafts ADD COLUMN status TEXT DEFAULT 'draft'")
        if "conditions_data" not in cols:
            conn.execute("ALTER TABLE whisper_drafts ADD COLUMN conditions_data TEXT DEFAULT ''")
        conn.commit()


def create_draft(user_id, content, category='', template_name='', envelope_style='', conditions_data=''):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO whisper_drafts
               (user_id, content, category, template_name, envelope_style, conditions_data)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (user_id, content, category, template_name, envelope_style, conditions_data),
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


def update_draft_target(user_id, chat_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_drafts SET target_chat_id=?, status='pending' WHERE user_id=?",
            (chat_id, user_id),
        )
        conn.commit()


def get_pending_draft(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_drafts WHERE user_id=? AND status='pending' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


from database.postgres import USE_POSTGRES
if USE_POSTGRES:
    from database.pg_envelope import *
