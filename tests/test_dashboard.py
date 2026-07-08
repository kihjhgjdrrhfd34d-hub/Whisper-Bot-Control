"""
tests/test_dashboard.py
Unit tests for the Whisper Dashboard feature (v1.1).

Tests cover:
  - Dashboard text building
  - Database functions (close, pin, toggle_pin, get_pinned)
  - is_closed checks in can_read_whisper and can_reply_to_whisper
"""

import json
import os
import sys
import unittest

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"] = "0:test_token_placeholder"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.replies import init_replies_db, can_reply_to_whisper


class TestDashboardDBFunctions(unittest.TestCase):
    """Test database functions added for the dashboard."""

    def setUp(self):
        db.init_db()
        init_replies_db()
        db.upsert_user(7001, "sender", "Sender", None)
        db.upsert_user(7002, "reader1", "Reader1", None)

    def test_close_whisper(self):
        wid = db.create_whisper(7001, "close test", "everyone")
        db.close_whisper(wid)
        w = db.get_whisper(wid)
        self.assertIsNotNone(w)
        w = dict(w)
        self.assertEqual(w.get("is_closed"), 1)
        self.assertEqual(w.get("is_locked"), 1)

    def test_close_whisper_nonexistent(self):
        # Should not raise
        db.close_whisper("nonexistent_id")

    def test_is_whisper_closed(self):
        wid = db.create_whisper(7001, "closed?", "everyone")
        self.assertFalse(db.is_whisper_closed(wid))
        db.close_whisper(wid)
        self.assertTrue(db.is_whisper_closed(wid))

    def test_is_whisper_closed_nonexistent(self):
        self.assertFalse(db.is_whisper_closed("nonexistent_id"))

    def test_toggle_pin_whisper(self):
        wid = db.create_whisper(7001, "pin test", "everyone")
        # Initially not pinned
        w = dict(db.get_whisper(wid))
        self.assertEqual(w.get("is_pinned", 0), 0)
        # Pin it
        new_state = db.toggle_pin_whisper(wid)
        self.assertEqual(new_state, 1)
        w = dict(db.get_whisper(wid))
        self.assertEqual(w.get("is_pinned"), 1)
        # Unpin it
        new_state = db.toggle_pin_whisper(wid)
        self.assertEqual(new_state, 0)
        w = dict(db.get_whisper(wid))
        self.assertEqual(w.get("is_pinned"), 0)

    def test_toggle_pin_nonexistent(self):
        result = db.toggle_pin_whisper("nonexistent_id")
        self.assertIsNone(result)

    def test_get_pinned_whispers(self):
        wid1 = db.create_whisper(7001, "pinned1", "everyone")
        wid2 = db.create_whisper(7001, "pinned2", "everyone")
        wid3 = db.create_whisper(7001, "not pinned", "everyone")
        db.toggle_pin_whisper(wid1)
        db.toggle_pin_whisper(wid2)
        rows, total = db.get_pinned_whispers(7001)
        self.assertEqual(total, 2)
        ids = [r["whisper_id"] for r in rows]
        self.assertIn(wid1, ids)
        self.assertIn(wid2, ids)
        self.assertNotIn(wid3, ids)

    def test_get_sender_whispers(self):
        # Use a unique sender_id to avoid counting whispers from other tests
        db.upsert_user(7100, "unique_sender", "Unique", None)
        wid1 = db.create_whisper(7100, "a", "everyone")
        wid2 = db.create_whisper(7100, "b", "everyone")
        rows, total = db.get_sender_whispers(7100)
        self.assertEqual(total, 2)
        ids = [r["whisper_id"] for r in rows]
        self.assertIn(wid1, ids)
        self.assertIn(wid2, ids)


class TestDashboardIsClosedIntegration(unittest.TestCase):
    """Test that is_closed affects can_read_whisper and can_reply_to_whisper."""

    def setUp(self):
        db.init_db()
        init_replies_db()
        db.upsert_user(8001, "sender", "Sender", None)
        db.upsert_user(8002, "reader", "Reader", None)
        db.upsert_user(8003, "other", "Other", None)

    def test_can_read_whisper_when_closed(self):
        wid = db.create_whisper(8001, "closed read test", "everyone")
        db.close_whisper(wid)
        can, reason = db.can_read_whisper(wid, 8002)
        self.assertFalse(can)
        self.assertEqual(reason, "locked")

    def test_can_read_whisper_when_not_closed(self):
        wid = db.create_whisper(8001, "open read test", "everyone")
        can, reason = db.can_read_whisper(wid, 8002)
        self.assertTrue(can)

    def test_can_reply_to_whisper_when_closed(self):
        wid = db.create_whisper(8001, "closed reply test", "everyone")
        db.add_reader(wid, 8002)
        db.close_whisper(wid)
        ok, reason = can_reply_to_whisper(wid, 8002)
        self.assertFalse(ok)
        self.assertEqual(reason, "whisper_locked")

    def test_can_reply_to_whisper_when_not_closed(self):
        wid = db.create_whisper(8001, "open reply test", "everyone")
        db.add_reader(wid, 8002)
        ok, reason = can_reply_to_whisper(wid, 8002)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


class TestDashboardTextBuilding(unittest.TestCase):
    """Test the dashboard text building logic from handlers/dashboard.py."""

    def setUp(self):
        db.init_db()
        init_replies_db()
        db.upsert_user(9001, "sender", "Sender", None)
        db.upsert_user(9002, "reader1", "Reader1", None)

    def test_dashboard_text_contains_whisper_info(self):
        """Verify the dashboard text includes key sections."""
        from handlers.dashboard import _build_dashboard_text, _build_stats_text, _build_readers_text, _build_replies_text
        wid = db.create_whisper(9001, "test content", "everyone")
        w = db.get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertIn("📨 معلومات الهمسة", text)
        self.assertIn("للجميع 🌍", text)
        # Dashboard shows metadata only, not the whisper content itself

    def test_stats_text(self):
        from handlers.dashboard import _build_stats_text
        wid = db.create_whisper(9001, "stats test", "everyone")
        w = db.get_whisper(wid)
        text = _build_stats_text(w)
        self.assertIn("📊 الإحصائيات", text)
        self.assertIn("عدد القراءات", text)

    def test_readers_text_empty(self):
        from handlers.dashboard import _build_readers_text
        wid = db.create_whisper(9001, "readers test", "everyone")
        text = _build_readers_text(wid)
        self.assertIn("لا يوجد قراء", text)

    def test_readers_text_with_reader(self):
        from handlers.dashboard import _build_readers_text
        wid = db.create_whisper(9001, "readers test", "everyone")
        db.add_reader(wid, 9002)
        text = _build_readers_text(wid)
        self.assertIn("Reader1", text)
        self.assertIn("reader1", text)

    def test_replies_text_empty(self):
        from handlers.dashboard import _build_replies_text
        wid = db.create_whisper(9001, "replies test", "everyone")
        text = _build_replies_text(wid)
        self.assertIn("لا توجد ردود", text)

    def test_replies_text_with_reply(self):
        from handlers.dashboard import _build_replies_text
        from database.replies import create_reply
        wid = db.create_whisper(9001, "replies test", "everyone")
        create_reply(wid, 9002, "this is a reply")
        text = _build_replies_text(wid)
        self.assertIn("this is a reply", text)
        self.assertIn("Reader1", text)

    def test_dashboard_keyboard_structure(self):
        """Verify the dashboard keyboard has all expected buttons."""
        from handlers.dashboard import dashboard_keyboard
        wid = db.create_whisper(9001, "kb test", "everyone")
        kb = dashboard_keyboard(wid)
        buttons = []
        for row in kb.keyboard:
            for btn in row:
                buttons.append(btn.text)
        expected = ["📊 الإحصائيات", "👁️ عرض القراء", "💬 عرض الردود",
                     "📤 إعادة إرسال", "📌 تثبيت", "🗑 حذف", "🔒 إغلاق"]
        for exp in expected:
            self.assertIn(exp, buttons, f"Button '{exp}' not found in dashboard keyboard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
