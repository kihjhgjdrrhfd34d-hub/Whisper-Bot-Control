import logging
from database.postgres import get_conn as _pg_get_conn

logger = logging.getLogger(__name__)


def init_contact_review_db():
    with _pg_get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_reviews (
                id            SERIAL PRIMARY KEY,
                whisper_id    TEXT NOT NULL UNIQUE,
                sender_id     BIGINT NOT NULL,
                target_id     BIGINT,
                content       TEXT NOT NULL,
                original_type TEXT DEFAULT 'first_one',
                status        TEXT DEFAULT 'pending',
                created_at    TEXT DEFAULT (NOW()),
                reviewed_at   TEXT,
                reviewed_by   BIGINT,
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id),
                FOREIGN KEY (sender_id) REFERENCES users(user_id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cr_status
                ON contact_reviews(status)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cr_whisper
                ON contact_reviews(whisper_id)
        """)
        conn.commit()
    logger.info("contact_reviews table ready (PG)")


def create_contact_review(whisper_id, sender_id, content, target_id=None, original_type="first_one"):
    with _pg_get_conn() as conn:
        conn.execute(
            """
            INSERT INTO contact_reviews
                (whisper_id, sender_id, target_id, content, original_type, status)
            VALUES (%s, %s, %s, %s, %s, 'pending')
            """,
            (whisper_id, sender_id, target_id, content, original_type),
        )
        conn.commit()


def get_pending_review(whisper_id):
    with _pg_get_conn() as conn:
        return conn.execute(
            "SELECT * FROM contact_reviews WHERE whisper_id=%s AND status='pending'",
            (whisper_id,),
        ).fetchone()


def approve_review(whisper_id, reviewed_by):
    with _pg_get_conn() as conn:
        conn.execute(
            """
            UPDATE contact_reviews
            SET status='approved', reviewed_at=NOW(), reviewed_by=%s
            WHERE whisper_id=%s
            """,
            (reviewed_by, whisper_id),
        )
        conn.commit()


def reject_review(whisper_id, reviewed_by):
    with _pg_get_conn() as conn:
        conn.execute(
            """
            UPDATE contact_reviews
            SET status='rejected', reviewed_at=NOW(), reviewed_by=%s
            WHERE whisper_id=%s
            """,
            (reviewed_by, whisper_id),
        )
        conn.commit()


def get_pending_reviews_count():
    with _pg_get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM contact_reviews WHERE status='pending'"
        ).fetchone()
        return row["count"] if row else 0
