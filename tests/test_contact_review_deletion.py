"""
Regression tests: whisper deletion must not be blocked by a contact_review
row, and must clean up that row with the whisper.

History
-------
contact_reviews carries a FOREIGN KEY to whispers(whisper_id) WITHOUT ON
DELETE CASCADE, and delete_whisper()/delete_expired_whispers() never deleted
contact_reviews before deleting the parent row.  With PRAGMA foreign_keys=ON
the parent DELETE raised "IntegrityError: FOREIGN KEY constraint failed", so
a whisper that had a contact review could not be deleted:
  * manual dashboard/API delete of such a whisper crashed and stayed in DB;
  * delete_expired_whispers aborted at the FIRST such whisper, leaving the
    whole batch of expired whispers behind.
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.contact_review import (
    init_contact_review_db,
    create_contact_review,
    get_pending_review,
)


def _boot():
    db.init_db()
    init_contact_review_db()


def _expire(wid, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE whispers SET auto_delete_at=? WHERE whisper_id=?", (ts, wid))
        conn.commit()


def _future(wid, hours_ahead):
    ts = (datetime.now(timezone.utc) + timedelta(hours=hours_ahead)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE whispers SET auto_delete_at=? WHERE whisper_id=?", (ts, wid))
        conn.commit()


def _count_reviews(wid):
    with db.get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM contact_reviews WHERE whisper_id=?", (wid,)
        ).fetchone()[0]


def _make_with_review(sender_id):
    """Return (wid, target_id) for a whisper that also has a contact review."""
    wid = db.create_whisper(
        sender_id, "contact secret", "everyone",
        target_users=[sender_id],
    )
    db.upsert_user(sender_id, "sender", "Sender", None)
    create_contact_review(wid, sender_id, "review content", target_id=sender_id)
    return wid


class TestDeleteContactReviewWhisper(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(9001, "sender", "Sender", None)

    def test_delete_whisper_without_review_still_works(self):
        wid = db.create_whisper(9001, "normal", "everyone")
        self.assertIsNotNone(db.get_whisper(wid))

        db.delete_whisper(wid)  # must NOT raise IntegrityError

        self.assertIsNone(db.get_whisper(wid))

    def test_delete_whisper_with_review_succeeds(self):
        wid = _make_with_review(9001)
        self.assertIsNotNone(get_pending_review(wid))

        db.delete_whisper(wid)  # must NOT raise IntegrityError

        self.assertIsNone(db.get_whisper(wid))

    def test_delete_removes_contact_review_row(self):
        wid = _make_with_review(9001)
        self.assertEqual(_count_reviews(wid), 1)

        db.delete_whisper(wid)

        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(_count_reviews(wid), 0)
        self.assertIsNone(get_pending_review(wid))

    def test_delete_expired_with_review_succeeds(self):
        wid = _make_with_review(9001)
        _expire(wid, hours_ago=2)

        deleted = db.delete_expired_whispers()  # must NOT raise IntegrityError

        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(_count_reviews(wid), 0)

    def test_expired_cleanup_does_not_abort_on_review_whisper(self):
        wid_normal = db.create_whisper(9001, "normal", "everyone")
        wid_review = _make_with_review(9001)
        _expire(wid_normal, hours_ago=2)
        _expire(wid_review, hours_ago=2)

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 2)
        self.assertIsNone(db.get_whisper(wid_normal))
        self.assertIsNone(db.get_whisper(wid_review))

    def test_still_valid_review_whisper_survives_cleanup(self):
        wid = _make_with_review(9001)
        _future(wid, hours_ahead=2)

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 0)
        self.assertIsNotNone(db.get_whisper(wid))
        self.assertEqual(_count_reviews(wid), 1)


class TestDeleteContactReviewWhisperPostgres(unittest.TestCase):
    """Same invariants against PostgreSQL — skipped when DATABASE_URL is unset."""

    PG_URL = os.getenv("DATABASE_URL")

    @classmethod
    def setUpClass(cls):
        if not cls.PG_URL:
            raise unittest.SkipTest("DATABASE_URL not set; PostgreSQL not exercised")

    def setUp(self):
        from database import postgres as pg
        with pg.get_conn() as conn:
            conn.execute("DELETE FROM whispers")
            conn.execute("DELETE FROM users")
            conn.commit()
        db.init_db()
        init_contact_review_db()
        db.upsert_user(9011, "sender", "Sender", None)

    def test_delete_whisper_with_review_succeeds_and_removes_row(self):
        wid = _make_with_review(9011)
        self.assertIsNotNone(get_pending_review(wid))

        db.delete_whisper(wid)

        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(_count_reviews(wid), 0)

    def test_delete_expired_with_review_succeeds(self):
        wid = _make_with_review(9011)
        _expire(wid, hours_ago=2)

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(_count_reviews(wid), 0)


if __name__ == "__main__":
    unittest.main()
