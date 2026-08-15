"""
Regression tests: whisper deletion must not be blocked by conditional
whisper data (whisper_conditions / condition_attempts), and must clean up
that data with the whisper.

History
-------
delete_whisper() and delete_expired_whispers() deleted whisper_readers,
curious_ones and whisper_replies manually but NEVER deleted
whisper_conditions / condition_attempts before deleting the parent row.
Those two tables carry a FOREIGN KEY to whispers WITHOUT ON DELETE CASCADE,
so with PRAGMA foreign_keys=ON the parent DELETE raised
"IntegrityError: FOREIGN KEY constraint failed":
  * manual dashboard/API delete of a conditional whisper crashed and the
    whisper stayed in the DB;
  * delete_expired_whispers aborted at the FIRST conditional whisper, so the
    whole batch of expired whispers (including normal ones) was left behind.
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
from database.replies import init_replies_db, create_reply, get_replies
from database.whisper_conditions import (
    init_whisper_conditions_db,
    add_whisper_condition,
    record_condition_attempt,
    get_whisper_conditions,
    get_condition_attempts,
)


def _boot():
    db.init_db()
    init_replies_db()
    init_whisper_conditions_db()


def _expire(wid, hours_ago):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    with db.get_conn() as conn:
        conn.execute("UPDATE whispers SET auto_delete_at=? WHERE whisper_id=?", (ts, wid))
        conn.commit()


def _make_conditional(sender_id, reader_id):
    """Create a conditional whisper fully populated with related data."""
    wid = db.create_whisper(
        sender_id, "conditional secret",
        "conditional_password", target_users=[reader_id],
    )
    db.upsert_user(reader_id, "reader", "Reader", None)
    add_whisper_condition(wid, "password", {"pwd": "x"})
    record_condition_attempt(wid, reader_id, "password", True, {"result": "success"})
    db.add_reader(wid, reader_id)
    create_reply(wid, reader_id, "a reply")
    return wid


class TestDeleteConditionalWhisper(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(7001, "sender", "Sender", None)

    def test_manual_delete_removes_conditional_data(self):
        wid = _make_conditional(7001, 7002)
        self.assertIsNotNone(db.get_whisper(wid))
        self.assertEqual(len(get_whisper_conditions(wid)), 1)

        db.delete_whisper(wid)  # must NOT raise IntegrityError

        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(get_whisper_conditions(wid), [])
        self.assertEqual(get_condition_attempts(wid), [])
        self.assertEqual(get_replies(wid), [])
        self.assertEqual(db.reader_count(wid), 0)

    def test_delete_expired_removes_conditional_whisper(self):
        wid = _make_conditional(7001, 7002)
        _expire(wid, hours_ago=2)

        deleted = db.delete_expired_whispers()  # must NOT raise IntegrityError

        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(get_whisper_conditions(wid), [])

    def test_expired_cleanup_does_not_abort_on_conditional_whisper(self):
        # One conditional expired whisper must not prevent normal expired
        # whispers from being cleaned up in the same run.
        wid_normal = db.create_whisper(7001, "normal", "everyone")
        wid_cond = _make_conditional(7001, 7002)
        _expire(wid_normal, hours_ago=2)
        _expire(wid_cond, hours_ago=2)

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 2)
        self.assertIsNone(db.get_whisper(wid_normal))
        self.assertIsNone(db.get_whisper(wid_cond))

    def test_still_valid_conditional_whisper_survives_cleanup(self):
        wid = _make_conditional(7001, 7002)
        future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE whispers SET auto_delete_at=? WHERE whisper_id=?",
                (future, wid),
            )
            conn.commit()

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 0)
        self.assertIsNotNone(db.get_whisper(wid))
        self.assertEqual(len(get_whisper_conditions(wid)), 1)


class TestDeleteConditionalWhisperPostgres(unittest.TestCase):
    """Same invariants against PostgreSQL — skipped when DATABASE_URL is unset."""

    PG_URL = os.getenv("DATABASE_URL")

    @classmethod
    def setUpClass(cls):
        if not cls.PG_URL:
            raise unittest.SkipTest("DATABASE_URL not set; PostgreSQL not exercised")

    def setUp(self):
        from database import postgres as pg
        self._pg = pg
        # Drop/recreate the relevant tables so tests are isolated.
        with pg.get_conn() as conn:
            conn.execute("DELETE FROM whispers")
            conn.execute("DELETE FROM users")
            conn.commit()
        db.init_db()
        init_replies_db()
        init_whisper_conditions_db()
        db.upsert_user(8001, "sender", "Sender", None)

    def test_manual_delete_removes_conditional_data(self):
        wid = _make_conditional(8001, 8002)
        self.assertIsNotNone(db.get_whisper(wid))

        db.delete_whisper(wid)

        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(get_whisper_conditions(wid), [])
        self.assertEqual(get_condition_attempts(wid), [])
        self.assertEqual(get_replies(wid), [])
        self.assertEqual(db.reader_count(wid), 0)

    def test_delete_expired_removes_conditional_whisper(self):
        wid = _make_conditional(8001, 8002)
        _expire(wid, hours_ago=2)

        deleted = db.delete_expired_whispers()

        self.assertEqual(deleted, 1)
        self.assertIsNone(db.get_whisper(wid))
        self.assertEqual(get_whisper_conditions(wid), [])


if __name__ == "__main__":
    unittest.main()
