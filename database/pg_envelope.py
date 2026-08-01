from contextlib import contextmanager
from database.postgres import get_conn as _pg_get_conn, USE_POSTGRES


@contextmanager
def get_conn():
    conn = _pg_get_conn()
    try:
        yield conn
    finally:
        conn.close()


def init_envelope_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_drafts (
                id              SERIAL PRIMARY KEY,
                user_id         BIGINT NOT NULL,
                content         TEXT NOT NULL,
                category        TEXT DEFAULT '',
                template_name   TEXT DEFAULT '',
                envelope_style  TEXT DEFAULT '',
                target_chat_id  BIGINT DEFAULT 0,
                status          TEXT DEFAULT 'draft',
                conditions_data TEXT DEFAULT '',
                created_at      TEXT DEFAULT (NOW())
            );
            CREATE INDEX IF NOT EXISTS idx_wd_user
                ON whisper_drafts(user_id);
        """)
        conn.execute("ALTER TABLE whisper_drafts ADD COLUMN IF NOT EXISTS target_chat_id BIGINT DEFAULT 0")
        conn.execute("ALTER TABLE whisper_drafts ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'draft'")
        conn.execute("ALTER TABLE whisper_drafts ADD COLUMN IF NOT EXISTS conditions_data TEXT DEFAULT ''")
        conn.commit()


def create_draft(user_id, content, category='', template_name='', envelope_style='', conditions_data=''):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_drafts"
            " (user_id, content, category, template_name, envelope_style, conditions_data)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (user_id, content, category, template_name, envelope_style, conditions_data),
        )
        conn.commit()


def get_draft(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_drafts WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def delete_draft(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_drafts WHERE user_id=%s", (user_id,))
        conn.commit()


def update_draft_target(user_id, chat_id):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_drafts SET target_chat_id=%s, status='pending' WHERE user_id=%s",
            (chat_id, user_id),
        )
        conn.commit()


def get_pending_draft(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_drafts WHERE user_id=%s AND status='pending' ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None
