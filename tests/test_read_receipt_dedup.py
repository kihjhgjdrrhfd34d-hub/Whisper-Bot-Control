"""
tests/test_read_receipt_dedup.py
Tests for Priority 1: read-receipt deduplication fix.

Verifies:
  - add_reader_if_new() returns True only on first insert
  - Repeated calls return False (no duplicate notifications possible)
  - reader_count() stays correct
  - UNIQUE constraint on whisper_readers is enforced
"""
import os
import sys
import unittest
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
atexit.register(
    lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb)
)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


class TestAddReaderIfNew(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.upsert_user(9001, "sender",  "Sender",  None)
        db.upsert_user(9002, "reader1", "Reader1", None)
        db.upsert_user(9003, "reader2", "Reader2", None)
        self.wid = db.create_whisper(9001, "secret text", "everyone")

    def test_first_read_returns_true(self):
        """First time a user reads — add_reader_if_new must return True."""
        result = db.add_reader_if_new(self.wid, 9002)
        self.assertTrue(result, "First read should return True")

    def test_second_read_same_user_returns_false(self):
        """Repeated read by same user — must return False (no duplicate receipt)."""
        db.add_reader_if_new(self.wid, 9002)
        result = db.add_reader_if_new(self.wid, 9002)
        self.assertFalse(result, "Second read by same user should return False")

    def test_ten_presses_still_one_record(self):
        """Simulate user pressing button 10 times — DB stays at 1 record."""
        for _ in range(10):
            db.add_reader_if_new(self.wid, 9002)
        self.assertEqual(db.reader_count(self.wid), 1)

    def test_different_user_returns_true(self):
        """A different user reading for the first time returns True."""
        db.add_reader_if_new(self.wid, 9002)
        result = db.add_reader_if_new(self.wid, 9003)
        self.assertTrue(result, "New user first read should return True")

    def test_reader_count_accurate(self):
        """reader_count() reflects actual unique readers."""
        db.add_reader_if_new(self.wid, 9002)
        db.add_reader_if_new(self.wid, 9002)  # duplicate
        db.add_reader_if_new(self.wid, 9003)
        self.assertEqual(db.reader_count(self.wid), 2)

    def test_unique_constraint_enforced(self):
        """Direct INSERT OR IGNORE must not raise even on duplicates."""
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO whisper_readers (whisper_id, user_id)"
                " VALUES (?, ?)", (self.wid, 9002)
            )
            conn.execute(
                "INSERT OR IGNORE INTO whisper_readers (whisper_id, user_id)"
                " VALUES (?, ?)", (self.wid, 9002)  # duplicate — must be silent
            )
            conn.commit()
        self.assertEqual(db.reader_count(self.wid), 1)

    def test_add_reader_backward_compat(self):
        """Legacy add_reader() still works (now delegates to add_reader_if_new)."""
        db.add_reader(self.wid, 9002)
        self.assertEqual(db.reader_count(self.wid), 1)
        # calling it again must NOT raise and must NOT add a second record
        db.add_reader(self.wid, 9002)
        self.assertEqual(db.reader_count(self.wid), 1)

    def test_sender_not_counted_separately(self):
        """The sender field is not special in readers table — test normal flow."""
        wid2 = db.create_whisper(9001, "another", "first_one")
        first = db.add_reader_if_new(wid2, 9002)
        self.assertTrue(first)
        # reader_count should be 1 now
        self.assertEqual(db.reader_count(wid2), 1)

    def test_first_one_first_ever_detection(self):
        """
        Simulate how the whisper handler detects the 'first ever reader'
        for first_one whispers: check reader_count==1 AFTER add_reader_if_new.
        """
        wid = db.create_whisper(9001, "first_one whisper", "first_one")
        is_new = db.add_reader_if_new(wid, 9002)
        is_first_ever = (db.reader_count(wid) == 1) if is_new else False
        self.assertTrue(is_new)
        self.assertTrue(is_first_ever)

        # Second user — new but not first ever
        is_new2 = db.add_reader_if_new(wid, 9003)
        is_first_ever2 = (db.reader_count(wid) == 1) if is_new2 else False
        self.assertTrue(is_new2)
        self.assertFalse(is_first_ever2)

        # Same user again — not new, definitely not first ever
        is_new3 = db.add_reader_if_new(wid, 9002)
        is_first_ever3 = (db.reader_count(wid) == 1) if is_new3 else False
        self.assertFalse(is_new3)
        self.assertFalse(is_first_ever3)


class TestDatabaseIndexes(unittest.TestCase):
    """Verify that the performance indexes were created."""
    def setUp(self):
        db.init_db()

    def test_indexes_exist(self):
        expected_indexes = {
            "idx_whispers_sender",
            "idx_wr_whisper",
            "idx_wr_user",
            "idx_curious_whisper",
            "idx_users_created",
        }
        with db.get_conn() as conn:
            actual = {
                r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
        for idx in expected_indexes:
            self.assertIn(idx, actual, f"Index '{idx}' missing")


class TestConfigNoPlaceholder(unittest.TestCase):
    """Verify config.py doesn't use 'YOUR_BOT_TOKEN_HERE' as a default."""
    def test_no_placeholder_default(self):
        import importlib
        import config as cfg
        # The module-level BOT_TOKEN should not be the old hardcoded placeholder
        self.assertNotEqual(
            cfg.BOT_TOKEN, "YOUR_BOT_TOKEN_HERE",
            "config.py must not have a hardcoded placeholder token"
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
