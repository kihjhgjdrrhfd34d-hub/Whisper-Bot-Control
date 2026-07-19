"""
database/replies.py — Whisper Replies data layer.

Each reply belongs to exactly ONE whisper.  Replies cannot exist without
their parent whisper and are cascade-deleted when the whisper is deleted.

Schema
------
whisper_replies
    reply_id       TEXT PK   — short UUID (12 chars, like whisper_id)
    whisper_id     TEXT FK   — parent whisper
    sender_id      INTEGER   — Telegram user_id of the reply sender
    content        TEXT      — text body (may be empty for media-only replies)
    media_type     TEXT      — NULL | photo | video | voice | audio | document | sticker
    file_id        TEXT      — Telegram file_id for media replies
    created_at     TEXT      — UTC ISO timestamp

reply_reads
    id             INTEGER PK AUTOINCREMENT
    reply_id       TEXT FK
    user_id        INTEGER
    UNIQUE(reply_id, user_id)

Design notes
------------
- reply_id uses the same 12-char UUID strategy as whisper_id.
- Deleting a whisper cascades to its replies (FK ON DELETE CASCADE).
- Anonymity: the reply stores sender_id for routing but never exposes it
  to the other party.  The handler layer enforces anonymity.
"""

import uuid
import logging
from database import get_conn

logger = logging.getLogger(__name__)

# Maximum replies per whisper (anti-spam guard)
MAX_REPLIES_PER_WHISPER = 50

# Supported media types (subset that Telegram bots can forward)
SUPPORTED_MEDIA = {"photo", "video", "voice", "audio", "document", "sticker", "animation", "contact", "location"}


# ─────────────────────────────────────────────────────────────────────────────
# Schema initialisation
# ─────────────────────────────────────────────────────────────────────────────

def init_replies_db() -> None:
    """Create reply tables if they don't exist.  Safe to call repeatedly."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_replies (
                reply_id    TEXT PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                sender_id   INTEGER NOT NULL,
                parent_reply_id TEXT,
                content     TEXT NOT NULL DEFAULT '',
                media_type  TEXT,
                file_id     TEXT,
                created_at  TEXT DEFAULT (datetime('now')),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reply_reads (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                reply_id  TEXT NOT NULL,
                user_id   INTEGER NOT NULL,
                read_at   TEXT DEFAULT (datetime('now')),
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
    logger.debug("Replies schema initialised.")


def _migrate_add_parent_reply_id() -> None:
    """Add parent_reply_id column to existing whisper_replies tables."""
    with get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "whisper_replies" in tables:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whisper_replies)"
            ).fetchall()]
            if "parent_reply_id" not in cols:
                conn.execute(
                    "ALTER TABLE whisper_replies ADD COLUMN parent_reply_id TEXT"
                )
                conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# CRUD
# ─────────────────────────────────────────────────────────────────────────────

def create_reply(
    whisper_id: str,
    sender_id: int,
    content: str = "",
    media_type=None,
    file_id=None,
    parent_reply_id=None,
):
    """
    Insert a new reply and return its reply_id.

    parent_reply_id chains this reply to a previous one for bi-directional
    conversation routing (the reply will be routed to the sender of the
    parent reply instead of the original whisper sender).

    Returns None if:
    - the parent whisper does not exist
    - the whisper has reached MAX_REPLIES_PER_WHISPER
    - media_type is provided but not supported
    """
    if media_type and media_type not in SUPPORTED_MEDIA:
        logger.warning(f"create_reply: unsupported media_type={media_type!r}")
        return None

    with get_conn() as conn:
        # Verify parent whisper exists
        parent = conn.execute(
            "SELECT whisper_id FROM whispers WHERE whisper_id=?", (whisper_id,)
        ).fetchone()
        if not parent:
            logger.debug(f"create_reply: whisper {whisper_id!r} not found")
            return None

        # Enforce reply cap
        cnt = conn.execute(
            "SELECT COUNT(*) FROM whisper_replies WHERE whisper_id=?",
            (whisper_id,),
        ).fetchone()[0]
        if cnt >= MAX_REPLIES_PER_WHISPER:
            logger.debug(f"create_reply: reply cap reached for {whisper_id!r}")
            return None

        rid = str(uuid.uuid4())[:12]
        conn.execute(
            """
            INSERT INTO whisper_replies
                (reply_id, whisper_id, sender_id, parent_reply_id, content, media_type, file_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (rid, whisper_id, sender_id, parent_reply_id, content or "", media_type, file_id),
        )
        conn.commit()
    return rid


def get_reply(reply_id: str):
    """Return a single reply row or None."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM whisper_replies WHERE reply_id=?", (reply_id,)
        ).fetchone()


def get_reply_sender(reply_id: str):
    """Return the sender_id of a reply, or None if not found."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT sender_id FROM whisper_replies WHERE reply_id=?", (reply_id,)
        ).fetchone()
        return row["sender_id"] if row else None


def whisper_id_from_reply(reply_id: str):
    """Return the whisper_id associated with a reply, or None."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT whisper_id FROM whisper_replies WHERE reply_id=?", (reply_id,)
        ).fetchone()
        return row["whisper_id"] if row else None


def get_replies(whisper_id: str) -> list:
    """Return all replies for a whisper, oldest first."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM whisper_replies"
            " WHERE whisper_id=? ORDER BY created_at ASC",
            (whisper_id,),
        ).fetchall()]


def count_replies(whisper_id: str) -> int:
    """Return total reply count for a whisper."""
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_replies WHERE whisper_id=?",
            (whisper_id,),
        ).fetchone()[0]


def delete_replies_for_whisper(whisper_id: str) -> int:
    """Delete all replies for a whisper.  Called when a whisper is deleted."""
    with get_conn() as conn:
        cur = conn.execute(
            "DELETE FROM whisper_replies WHERE whisper_id=?", (whisper_id,)
        )
        conn.commit()
    return cur.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Read tracking
# ─────────────────────────────────────────────────────────────────────────────

def mark_reply_read(reply_id: str, user_id: int) -> bool:
    """
    Mark a reply as read by a user.
    Returns True on first read, False if already read.
    """
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO reply_reads (reply_id, user_id) VALUES (?, ?)",
            (reply_id, user_id),
        )
        inserted = conn.execute("SELECT changes()").fetchone()[0]
        conn.commit()
    return inserted == 1


# ─────────────────────────────────────────────────────────────────────────────
# Permission helpers
# ─────────────────────────────────────────────────────────────────────────────

def can_reply_to_whisper(whisper_id: str, user_id: int):
    """
    Decide whether user_id may send a reply to whisper_id.

    Rules
    -----
    - Whisper must exist.
    - Whisper must not be closed.
    - When locked manually (non-destructive): all replies blocked.
    - When locked destructively: sender and existing readers may reply.
    - user_id must be the sender OR an authorised reader.
    - Reply cap must not be exceeded.
    - Ban check is the caller's responsibility.

    Returns (True, "ok") or (False, reason_string).
    """
    with get_conn() as conn:
        w = conn.execute(
            "SELECT sender_id, is_locked, is_closed, is_destructive"
            " FROM whispers WHERE whisper_id=?",
            (whisper_id,),
        ).fetchone()

    if not w:
        return False, "whisper_not_found"

    w = dict(w)
    if w.get("is_closed", 0):
        return False, "whisper_locked"

    # ── Manual lock (non-destructive): block everyone ───────────────
    if w["is_locked"] and not w.get("is_destructive", 0):
        return False, "whisper_locked"

    # ── Determine if user is a participant ──────────────────────────
    is_sender = (user_id == w["sender_id"])
    is_reader = False
    if not is_sender:
        with get_conn() as conn:
            is_reader = conn.execute(
                "SELECT 1 FROM whisper_readers"
                " WHERE whisper_id=? AND user_id=?",
                (whisper_id, user_id),
            ).fetchone() is not None

    # ── Destructive lock: only participants may reply ───────────────
    if w["is_locked"] and not is_sender and not is_reader:
        return False, "whisper_locked"

    # ── Non-participants cannot reply (even when unlocked) ──────────
    if not is_sender and not is_reader:
        return False, "not_participant"

    current = count_replies(whisper_id)
    if current >= MAX_REPLIES_PER_WHISPER:
        return False, "reply_cap_reached"

    return True, "ok"


def get_whisper_participants(whisper_id: str) -> dict:
    """
    Return {'sender_id': int, 'reader_ids': [int, ...]} for a whisper.
    Used by the handler to route replies without revealing IDs to users.
    Returns {} if the whisper does not exist.
    """
    with get_conn() as conn:
        w = conn.execute(
            "SELECT sender_id FROM whispers WHERE whisper_id=?",
            (whisper_id,),
        ).fetchone()
        if not w:
            return {}
        readers = conn.execute(
            "SELECT user_id FROM whisper_readers WHERE whisper_id=?",
            (whisper_id,),
        ).fetchall()
    return {
        "sender_id": w["sender_id"],
        "reader_ids": [r["user_id"] for r in readers],
    }


# ── PostgreSQL shadow adapter ────────────────────────────────────────────
try:
    from database.postgres import USE_POSTGRES
    if USE_POSTGRES:
        from database.pg_replies import *  # noqa: F401, F403
except ImportError:
    pass
