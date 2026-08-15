"""
SQLite/PostgreSQL parity regression: a same-user repeat read of a limited
whisper (first_three / first_five) with capacity remaining must NOT raise.

History
-------
record_whisper_read() limits readers with a conditional INSERT:

    INSERT INTO whisper_readers (whisper_id, user_id)
    SELECT ?, ? WHERE (SELECT COUNT(*) ...) < <limit>

In PostgreSQL the INSERT carries ON CONFLICT (whisper_id, user_id) DO NOTHING,
so re-reading a whisper the user already opened (while spots remain) returns
False gracefully.  The SQLite sibling had no ON CONFLICT, so the same repeat
read raised "IntegrityError: UNIQUE constraint failed:
whisper_readers.whisper_id, whisper_readers.user_id", crashing the read flow.
"""
import os
import sys
import unittest
import tempfile
import atexit as _ate

_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


def _boot():
    db.init_db()
    for uid, name in ((70001, "sender"), (70002, "r1"), (70003, "r2"), (70004, "r3"), (70005, "r4")):
        db.upsert_user(uid, name, name.title(), None)


class TestRepeatReadLimitedWhisper(unittest.TestCase):
    def setUp(self):
        _boot()

    def check_repeat_read_is_graceful(self, whisper_type, max_readers):
        wid = db.create_whisper(
            70001, "secret", whisper_type,
            target_users=[], max_readers=max_readers,
        )
        # Two distinct readers take the first two seats.
        self.assertTrue(db.record_whisper_read(wid, 70002))
        self.assertTrue(db.record_whisper_read(wid, 70003))

        # r1 re-reads while capacity remains: must NOT raise, must return False.
        self.assertFalse(db.record_whisper_read(wid, 70002))

        # A brand-new reader still fits.
        self.assertTrue(db.record_whisper_read(wid, 70004))
        return wid, max_readers

    def test_first_three_repeat_read_no_integrity_error(self):
        wid, _ = self.check_repeat_read_is_graceful("first_three", 3)
        self.assertEqual(db.reader_count(wid), 3)

    def test_first_five_repeat_read_no_integrity_error(self):
        wid, _ = self.check_repeat_read_is_graceful("first_five", 5)
        self.assertEqual(db.reader_count(wid), 3)

    def test_reader_cap_still_enforced_after_repeat_read(self):
        wid, limit = self.check_repeat_read_is_graceful("first_three", 3)
        # Whisper is now locked/full; a 5th distinct reader cannot join.
        self.assertFalse(db.record_whisper_read(wid, 70005))
        self.assertEqual(db.reader_count(wid), limit)


if __name__ == "__main__":
    unittest.main()
