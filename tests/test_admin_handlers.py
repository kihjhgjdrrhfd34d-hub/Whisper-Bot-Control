"""
tests/test_admin_handlers.py — Tests for handlers/admin.py

Covers:
  - admin_main_keyboard structure
  - is_admin check
  - User management database operations (search, ban, unban)
  - Settings management (get/set)
  - Mandatory channels CRUD
  - Broadcast database operations
  - User pagination
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_admin_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999,888"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from config import ADMIN_IDS


def _boot():
    db.init_db()
    # Seed test users
    db.upsert_user(60101, "alice", "Alice", None)
    db.upsert_user(60102, "bob", "Bob", None)
    db.upsert_user(60103, "charlie", "Charlie", None)
    db.upsert_user(60200, "admin_user", "Admin", None)
    # Create some whispers for stats
    db.create_whisper(60101, "hello", "everyone")
    db.create_whisper(60102, "world", "everyone")


class TestIsAdmin(unittest.TestCase):
    """Test admin detection."""

    def setUp(self):
        _boot()

    def test_admin_id_is_admin(self):
        from handlers.admin import is_admin
        self.assertTrue(is_admin(999))
        self.assertTrue(is_admin(888))

    def test_non_admin_is_not_admin(self):
        from handlers.admin import is_admin
        self.assertFalse(is_admin(1))
        self.assertFalse(is_admin(9999))

    def test_zero_id_not_admin(self):
        from handlers.admin import is_admin
        self.assertFalse(is_admin(0))


class TestAdminMainKeyboard(unittest.TestCase):
    """Test the admin keyboard builder."""

    def setUp(self):
        _boot()

    def test_keyboard_has_admin_buttons(self):
        from handlers.admin import admin_main_keyboard
        kb = admin_main_keyboard()
        self.assertIsNotNone(kb)
        # Check that keyboard contains admin-related buttons
        # The keyboard has multiple rows, we check by looking at callback data patterns
        found_admin = False
        found_stats = False
        for row in kb.keyboard:
            for btn in row:
                if "admin:" in str(btn.callback_data):
                    if "main" in str(btn.callback_data):
                        found_admin = True
                    if "stats" in str(btn.callback_data):
                        found_stats = True
        self.assertTrue(found_admin or found_stats, "Keyboard should have admin buttons")

    def test_keyboard_not_empty(self):
        from handlers.admin import admin_main_keyboard
        kb = admin_main_keyboard()
        self.assertTrue(len(kb.keyboard) > 0)


class TestAdminUserManagement(unittest.TestCase):
    """Test user management operations used by admin handlers."""

    def setUp(self):
        _boot()

    def test_ban_user(self):
        db.ban_user(60101)
        self.assertTrue(db.is_banned(60101))

    def test_unban_user(self):
        db.ban_user(60101)
        db.unban_user(60101)
        self.assertFalse(db.is_banned(60101))

    def test_search_by_username(self):
        results = db.search_users("alice")
        self.assertTrue(any(r["user_id"] == 60101 for r in results))

    def test_search_by_id_as_string(self):
        results = db.search_users("60101")
        self.assertTrue(any(r["user_id"] == 60101 for r in results))

    def test_search_nonexistent_returns_empty(self):
        results = db.search_users("zzz_nonexistent_zzz")
        self.assertEqual(len(results), 0)

    def test_get_all_users_pagination(self):
        rows, total = db.get_all_users(page=0, per_page=2)
        self.assertLessEqual(len(rows), 2)
        self.assertGreaterEqual(total, 4)

    def test_get_all_users_second_page(self):
        rows, total = db.get_all_users(page=1, per_page=2)
        # Should get remaining users
        if total > 2:
            self.assertTrue(len(rows) > 0)


class TestAdminSettings(unittest.TestCase):
    """Test settings management used by admin handlers."""

    def setUp(self):
        _boot()

    def test_get_setting_default(self):
        val = db.get_setting("bot_active")
        self.assertEqual(val, "1")

    def test_set_setting(self):
        db.set_setting("bot_active", "0")
        self.assertEqual(db.get_setting("bot_active"), "0")
        db.set_setting("bot_active", "1")

    def test_set_custom_setting(self):
        db.set_setting("custom_admin_key", "custom_value")
        self.assertEqual(db.get_setting("custom_admin_key"), "custom_value")

    def test_get_all_settings_batch(self):
        result = db.get_all_settings(["bot_active", "membership_check"])
        self.assertIn("bot_active", result)
        self.assertIn("membership_check", result)

    def test_nonexistent_setting_default(self):
        val = db.get_setting("definitely_not_a_real_setting_key_xyz")
        self.assertIsNone(val)


class TestMandatoryChannels(unittest.TestCase):
    """Test mandatory channel operations used by admin handlers."""

    def setUp(self):
        _boot()

    def test_add_channel(self):
        db.add_mandatory_channel("@testchannel", "Test Channel")
        channels = db.get_mandatory_channels()
        ids = [ch["channel_id"] for ch in channels]
        self.assertIn("@testchannel", ids)

    def test_add_channel_without_name(self):
        db.add_mandatory_channel("@noname")
        channels = db.get_mandatory_channels()
        ids = [ch["channel_id"] for ch in channels]
        self.assertIn("@noname", ids)

    def test_remove_channel(self):
        db.add_mandatory_channel("@removeme", "Remove Me")
        db.remove_mandatory_channel("@removeme")
        channels = db.get_mandatory_channels()
        ids = [ch["channel_id"] for ch in channels]
        self.assertNotIn("@removeme", ids)

    def test_remove_nonexistent_channel_no_crash(self):
        db.remove_mandatory_channel("@nonexistent_channel_xyz")

    def test_duplicate_channel_ignored(self):
        db.add_mandatory_channel("@dup", "Original")
        db.add_mandatory_channel("@dup", "Duplicate")
        channels = [ch for ch in db.get_mandatory_channels()
                    if ch["channel_id"] == "@dup"]
        self.assertEqual(len(channels), 1)

    def test_get_channels_when_empty(self):
        # Remove any existing channels first
        for ch in db.get_mandatory_channels():
            db.remove_mandatory_channel(ch["channel_id"])
        channels = db.get_mandatory_channels()
        self.assertEqual(len(channels), 0)


class TestAdminBroadcast(unittest.TestCase):
    """Test broadcast database operations."""

    def setUp(self):
        _boot()

    def test_broadcast_db_structure(self):
        """Verify broadcast table exists and can be queried."""
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("broadcasts", tables)


class TestAdminStats(unittest.TestCase):
    """Test statistics used by admin panel."""

    def setUp(self):
        _boot()

    def test_get_stats_contains_all_keys(self):
        s = db.get_stats()
        expected = {"total_users", "banned_users", "active_users",
                     "total_whispers", "total_reads", "new_today", "whispers_today"}
        for key in expected:
            self.assertIn(key, s)

    def test_stats_counts_reflect_data(self):
        s = db.get_stats()
        self.assertGreaterEqual(s["total_users"], 4)
        self.assertGreaterEqual(s["total_whispers"], 2)

    def test_banned_count(self):
        db.ban_user(60101)
        s = db.get_stats()
        self.assertGreaterEqual(s["banned_users"], 1)
        db.unban_user(60101)

    def test_get_user_stats_contains_keys(self):
        s = db.get_user_stats(60101)
        expected = {"sent", "received_reads", "read_others", "curious_on_mine",
                     "locked", "type_everyone", "type_first_one",
                     "type_first_three", "type_custom"}
        for key in expected:
            self.assertIn(key, s)

    def test_user_stats_reflects_sent(self):
        s = db.get_user_stats(60101)
        self.assertGreaterEqual(s["sent"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
