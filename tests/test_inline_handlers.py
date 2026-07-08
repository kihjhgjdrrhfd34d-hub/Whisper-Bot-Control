"""
tests/test_inline_handlers.py — Tests for handlers/inline.py

Covers:
  - FOUR_OPTIONS and DESTRUCTIVE_OPTIONS constants
  - Whisper type configuration correctness
  - Database operations used by inline handlers
  - create_whisper with different types
  - Inline query result structure (via constants)
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit
import json

_tmpdb = tempfile.mktemp(suffix="_inline_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


def _boot():
    db.init_db()
    db.upsert_user(60201, "alice", "Alice", None)


class TestInlineConstants(unittest.TestCase):
    """Test the inline option constants."""

    def test_four_options_count(self):
        from handlers.inline import FOUR_OPTIONS
        self.assertEqual(len(FOUR_OPTIONS), 4)

    def test_four_options_contain_all_types(self):
        from handlers.inline import FOUR_OPTIONS
        types = [opt[0] for opt in FOUR_OPTIONS]
        self.assertIn("first_one", types)
        self.assertIn("everyone", types)
        self.assertIn("first_three", types)
        self.assertIn("custom", types)

    def test_four_options_have_valid_max_readers(self):
        from handlers.inline import FOUR_OPTIONS
        for wtype, max_readers, title, desc, msg in FOUR_OPTIONS:
            if wtype == "first_one":
                self.assertEqual(max_readers, 1)
            elif wtype == "first_three":
                self.assertEqual(max_readers, 3)
            elif wtype == "everyone":
                self.assertEqual(max_readers, 0)

    def test_destructive_options_count(self):
        from handlers.inline import DESTRUCTIVE_OPTIONS
        self.assertEqual(len(DESTRUCTIVE_OPTIONS), 3)

    def test_destructive_options_contain_expected_types(self):
        from handlers.inline import DESTRUCTIVE_OPTIONS
        types = [opt[0] for opt in DESTRUCTIVE_OPTIONS]
        self.assertIn("first_one", types)
        self.assertIn("first_three", types)
        self.assertIn("everyone", types)
        self.assertNotIn("custom", types)

    def test_control_panel_types(self):
        from handlers.inline import CONTROL_PANEL_TYPES
        self.assertIn("custom", CONTROL_PANEL_TYPES)
        self.assertEqual(len(CONTROL_PANEL_TYPES), 1)

    def test_no_empty_titles(self):
        from handlers.inline import FOUR_OPTIONS
        for _, _, title, _, _ in FOUR_OPTIONS:
            self.assertTrue(len(title) > 0)

    def test_no_empty_descriptions(self):
        from handlers.inline import FOUR_OPTIONS
        for _, _, _, desc, _ in FOUR_OPTIONS:
            self.assertTrue(len(desc) > 0)


class TestInlineDatabase(unittest.TestCase):
    """Test database operations triggered by inline handlers."""

    def setUp(self):
        _boot()

    def test_create_everyone_whisper(self):
        wid = db.create_whisper(60201, "hello everyone", "everyone")
        w = db.get_whisper(wid)
        self.assertEqual(w["whisper_type"], "everyone")
        self.assertEqual(w["max_readers"], 0)

    def test_create_first_one_whisper(self):
        wid = db.create_whisper(60201, "first only", "first_one")
        w = db.get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 0)

    def test_create_first_three_whisper(self):
        wid = db.create_whisper(60201, "first three", "first_three")
        w = db.get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_three")
        self.assertEqual(w["max_readers"], 0)

    def test_create_custom_whisper(self):
        wid = db.create_whisper(60201, "custom", "custom", target_users=[2])
        w = db.get_whisper(wid)
        self.assertEqual(w["whisper_type"], "custom")
        targets = json.loads(w["target_users"])
        self.assertIn(2, targets)

    def test_create_destructive_whisper(self):
        wid = db.create_whisper(60201, "destructive", "first_one",
            max_readers=1, is_destructive=True,
        )
        w = db.get_whisper(wid)
        self.assertEqual(w["is_destructive"], 1)

    def test_whisper_generates_unique_ids(self):
        wid1 = db.create_whisper(60201, "a", "everyone")
        wid2 = db.create_whisper(60201, "b", "everyone")
        self.assertNotEqual(wid1, wid2)

    def test_whisper_id_length(self):
        wid = db.create_whisper(60201, "test", "everyone")
        self.assertEqual(len(wid), 12)

    def test_get_nonexistent_whisper(self):
        w = db.get_whisper("nonexistent")
        self.assertIsNone(w)


class TestInlineEdgeCases(unittest.TestCase):
    """Test edge cases for inline whisper creation."""

    def setUp(self):
        _boot()

    def test_create_whisper_with_empty_content(self):
        wid = db.create_whisper(60201, "", "everyone")
        w = db.get_whisper(wid)
        self.assertEqual(w["content"], "")

    def test_create_whisper_with_none_target(self):
        wid = db.create_whisper(60201, "no target", "everyone")
        self.assertIsNotNone(wid)

    def test_create_whisper_invalid_type(self):
        wid = db.create_whisper(60201, "invalid", "invalid_type")
        self.assertIsNotNone(wid)
        w = db.get_whisper(wid)
        self.assertEqual(w["whisper_type"], "invalid_type")

    def test_create_multiple_custom_targets(self):
        wid = db.create_whisper(60201, "multi target", "custom",
                                 target_users=[2, 3, 4])
        w = db.get_whisper(wid)
        targets = json.loads(w["target_users"])
        self.assertEqual(len(targets), 3)
        self.assertIn(2, targets)
        self.assertIn(3, targets)
        self.assertIn(4, targets)


if __name__ == "__main__":
    unittest.main(verbosity=2)
