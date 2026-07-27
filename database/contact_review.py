import logging
from database import get_conn

logger = logging.getLogger(__name__)


def init_contact_review_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS contact_reviews (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                whisper_id    TEXT NOT NULL UNIQUE,
                sender_id     INTEGER NOT NULL,
                target_id     INTEGER,
                content       TEXT NOT NULL,
                original_type TEXT DEFAULT 'first_one',
                status        TEXT DEFAULT 'pending',
                created_at    TEXT DEFAULT (datetime('now')),
                reviewed_at   TEXT,
                reviewed_by   INTEGER,
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
    logger.info("✅ contact_reviews table ready.")


def create_contact_review(whisper_id, sender_id, content, target_id=None, original_type="first_one"):
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO contact_reviews
                (whisper_id, sender_id, target_id, content, original_type, status)
            VALUES (?, ?, ?, ?, ?, 'pending')
            """,
            (whisper_id, sender_id, target_id, content, original_type),
        )
        conn.commit()


def get_pending_review(whisper_id):
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM contact_reviews WHERE whisper_id=? AND status='pending'",
            (whisper_id,),
        ).fetchone()


def approve_review(whisper_id, reviewed_by):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE contact_reviews
            SET status='approved', reviewed_at=datetime('now'), reviewed_by=?
            WHERE whisper_id=?
            """,
            (reviewed_by, whisper_id),
        )
        conn.commit()


def reject_review(whisper_id, reviewed_by):
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE contact_reviews
            SET status='rejected', reviewed_at=datetime('now'), reviewed_by=?
            WHERE whisper_id=?
            """,
            (reviewed_by, whisper_id),
        )
        conn.commit()


def get_pending_reviews_count():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) FROM contact_reviews WHERE status='pending'"
        ).fetchone()
        return row[0] if row else 0
