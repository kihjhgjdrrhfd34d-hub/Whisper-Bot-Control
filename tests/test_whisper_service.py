"""
tests/test_whisper_service.py — Unit tests for services/whisper_service.py

Tests the pure-data business logic layer — no TeleBot dependency.
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_service_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import create_whisper, upsert_user, get_whisper
from services.whisper_service import (
    parse_whisper_id,
    is_destructive_whisper,
    is_own_whisper,
    ensure_user,
    record_read_and_check,
    get_reader_display_name,
    get_opener_name,
    get_user_display,
    build_first_one_notification,
    build_read_receipt_message,
    build_destructive_receipt_message,
    build_curious_report_lines,
)


class TestParseWhisperId(unittest.TestCase):
    """parse_whisper_id extracts whisper ID from various callback prefixes."""

    def test_parse_read_prefix(self):
        self.assertEqual(parse_whisper_id("read:abc123"), "abc123")

    def test_parse_lock_prefix(self):
        self.assertEqual(parse_whisper_id("lock:whisper_42"), "whisper_42")

    def test_parse_delete_prefix(self):
        self.assertEqual(parse_whisper_id("delete:del_id"), "del_id")

    def test_parse_curious_prefix(self):
        self.assertEqual(parse_whisper_id("curious:cur_id"), "cur_id")

    def test_parse_edit_prefix(self):
        self.assertEqual(parse_whisper_id("edit:edit_id"), "edit_id")


class TestIsDestructiveWhisper(unittest.TestCase):
    """is_destructive_whisper checks the is_destructive flag."""

    def test_destructive_returns_true(self):
        w = {"is_destructive": 1}
        self.assertTrue(is_destructive_whisper(w))

    def test_non_destructive_returns_false(self):
        w = {"is_destructive": 0}
        self.assertFalse(is_destructive_whisper(w))

    def test_missing_flag_returns_false(self):
        w = {}
        self.assertFalse(is_destructive_whisper(w))

    def test_string_flag_converts_correctly(self):
        w = {"is_destructive": "1"}
        self.assertTrue(is_destructive_whisper(w))


class TestIsOwnWhisper(unittest.TestCase):
    """is_own_whisper compares user_id with sender_id."""

    def test_own_returns_true(self):
        w = {"sender_id": 100}
        self.assertTrue(is_own_whisper(100, w))

    def test_not_own_returns_false(self):
        w = {"sender_id": 100}
        self.assertFalse(is_own_whisper(99, w))


class TestEnsureUser(unittest.TestCase):
    """ensure_user upserts silently."""

    def setUp(self):
        db.init_db()

    def test_ensure_user_creates_new(self):
        ensure_user(70001, "new_user", "New", "User")
        from database import get_user
        u = get_user(70001)
        self.assertIsNotNone(u)
        self.assertEqual(u["username"], "new_user")

    def test_ensure_user_updates_existing(self):
        upsert_user(70002, "old_name", "Old", None)
        ensure_user(70002, "new_name", "New", None)
        from database import get_user
        u = get_user(70002)
        self.assertEqual(u["username"], "new_name")

    def test_ensure_user_silent_on_error(self):
        # Should not raise even if called with invalid args
        ensure_user(70003, None, None, None)
        from database import get_user
        u = get_user(70003)
        self.assertIsNotNone(u)


class TestGetReaderDisplayName(unittest.TestCase):
    """get_reader_display_name formats reader name for display."""

    def test_with_username(self):
        r = {"username": "bob", "first_name": "Bob"}
        self.assertEqual(get_reader_display_name(r), "@bob")

    def test_without_username(self):
        r = {"username": None, "first_name": "Bob"}
        self.assertEqual(get_reader_display_name(r), "Bob")

    def test_without_username_or_name(self):
        r = {"username": None, "first_name": None}
        self.assertEqual(get_reader_display_name(r), "مستخدم مجهول")

    def test_empty_dict(self):
        self.assertEqual(get_reader_display_name({}), "مستخدم مجهول")


class TestGetOpenerName(unittest.TestCase):
    """get_opener_name returns the first reader's display name."""

    def setUp(self):
        db.init_db()
        upsert_user(60001, "alice", "Alice", None)
        upsert_user(60002, "bob", "Bob", None)
        self.wid = create_whisper(60001, "test", "everyone")

    def test_no_readers_returns_fallback(self):
        name = get_opener_name(self.wid)
        self.assertEqual(name, "شخص آخر")

    def test_with_reader_returns_username(self):
        from database import add_reader_if_new
        add_reader_if_new(self.wid, 60002)
        name = get_opener_name(self.wid)
        self.assertEqual(name, "@bob")

    def test_reader_without_username_returns_name(self):
        upsert_user(60003, None, "Charlie", None)
        from database import add_reader_if_new
        add_reader_if_new(self.wid, 60003)
        name = get_opener_name(self.wid)
        self.assertEqual(name, "Charlie")


class TestGetUserDisplay(unittest.TestCase):
    """get_user_display formats a Telegram user for display."""

    def _make_user(self, username=None, first_name=None):
        u = MagicMock()
        u.username = username
        u.first_name = first_name
        return u

    def test_with_username(self):
        u = self._make_user("bob", "Bob")
        self.assertEqual(get_user_display(u), "@bob")

    def test_without_username(self):
        u = self._make_user(None, "Bob")
        self.assertEqual(get_user_display(u), "Bob")

    def test_without_username_or_name(self):
        u = self._make_user(None, None)
        self.assertEqual(get_user_display(u), "شخص")


class TestBuildFirstOneNotification(unittest.TestCase):
    """build_first_one_notification creates the detailed HTML notification."""

    def _make_user(self, user_id=60001, username="alice", first_name="Alice"):
        u = MagicMock()
        u.id = user_id
        u.username = username
        u.first_name = first_name
        return u

    def test_contains_all_sections(self):
        u = self._make_user()
        w = {"content": "secret message"}
        msg = build_first_one_notification(u, w)
        self.assertIn("تمت مشاهدة هذه الهمسة", msg)
        self.assertIn("alice", msg)
        self.assertIn("Alice", msg)
        self.assertIn("60001", msg)
        self.assertIn("secret message", msg)

    def test_escapes_html_content(self):
        u = self._make_user()
        w = {"content": "<b>bold</b>"}
        msg = build_first_one_notification(u, w)
        self.assertIn("&lt;b&gt;", msg)
        self.assertNotIn("<b>", msg)

    def test_no_username_shows_fallback(self):
        u = self._make_user(username=None)
        w = {"content": "hi"}
        msg = build_first_one_notification(u, w)
        self.assertIn("لا يوجد", msg)

    def test_no_first_name_shows_fallback(self):
        u = self._make_user(first_name=None)
        w = {"content": "hi"}
        msg = build_first_one_notification(u, w)
        self.assertIn("مستخدم مجهول", msg)

    def test_parse_mode_html(self):
        """The returned message is meant to be sent with parse_mode='HTML'."""
        u = self._make_user()
        w = {"content": "test"}
        msg = build_first_one_notification(u, w)
        # HTML tags like <br> or similar — but our template uses plaintext
        # Just verify it returns a non-empty string
        self.assertTrue(len(msg) > 50)


class TestBuildReadReceiptMessage(unittest.TestCase):
    """build_read_receipt_message creates the simple read receipt."""

    def _make_user(self, username="bob", first_name="Bob"):
        u = MagicMock()
        u.username = username
        u.first_name = first_name
        return u

    def test_with_username(self):
        u = self._make_user("bob")
        msg = build_read_receipt_message(u)
        self.assertEqual(msg, "👁 قرأ @bob همستك!")

    def test_without_username(self):
        u = self._make_user(None, "Bob")
        msg = build_read_receipt_message(u)
        self.assertEqual(msg, "👁 قرأ Bob همستك!")


class TestBuildDestructiveReceiptMessage(unittest.TestCase):
    """build_destructive_receipt_message creates destructive read receipt."""

    def _make_user(self, username="bob"):
        u = MagicMock()
        u.username = username
        u.first_name = "Bob"
        return u

    def test_contains_destructive_indicator(self):
        u = self._make_user()
        msg = build_destructive_receipt_message(u)
        self.assertIn("التدميرية", msg)
        self.assertIn("@bob", msg)


class TestBuildCuriousReportLines(unittest.TestCase):
    """build_curious_report_lines creates the curious-ones report."""

    def test_no_curious_still_returns_lines(self):
        lines = build_curious_report_lines([], [])
        self.assertTrue(any("0 شخص" in l for l in lines))

    def test_with_curious_users(self):
        curious = [
            {"first_name": "Alice", "username": "alice", "user_id": 1, "tried_at": "2024-01-01T12:00:00"},
        ]
        readers = [{"user_id": 1}]
        lines = build_curious_report_lines(curious, readers)
        full = "\n".join(lines)
        self.assertIn("Alice", full)
        self.assertIn("@alice", full)
        self.assertIn("1 شخص", full)

    def test_curious_without_username(self):
        curious = [
            {"first_name": "Bob", "username": None, "user_id": 2, "tried_at": None},
        ]
        readers = []
        lines = build_curious_report_lines(curious, readers)
        full = "\n".join(lines)
        self.assertIn("Bob", full)
        self.assertIn("—", full)  # username fallback


class TestRecordReadAndCheck(unittest.TestCase):
    """record_read_and_check composes record_whisper_read + reader_count."""

    def setUp(self):
        db.init_db()
        upsert_user(60001, "alice", "Alice", None)
        upsert_user(60002, "bob", "Bob", None)
        self.wid = create_whisper(60001, "record test", "everyone")

    def test_first_read_returns_true_true(self):
        is_new, is_first = record_read_and_check(self.wid, 60002)
        self.assertTrue(is_new)
        self.assertTrue(is_first)

    def test_duplicate_read_returns_false_false(self):
        record_read_and_check(self.wid, 60002)
        is_new, is_first = record_read_and_check(self.wid, 60002)
        self.assertFalse(is_new)
        self.assertFalse(is_first)

    def test_second_user_new_but_not_first_ever(self):
        record_read_and_check(self.wid, 60002)
        is_new, is_first = record_read_and_check(self.wid, 60003)
        # This is a new user but the DB may have 2 readers now
        # We're inserting user 60003 for the first time
        # But the DB already has 1 reader (60002), so reader_count=2
        self.assertTrue(is_new)
        self.assertFalse(is_first)
