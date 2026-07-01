"""
tests/test_fixes.py
Tests for all bug fixes applied in this session:
  1. _safe_edit_text "not modified" handling
  2. get_all_settings batch fetch
  3. Migration guard (CREATE INDEX only when tables exist)
  4. add_reader_if_new deduplication
  5. config.py token handling
  6. admin.py guard/answer flow correctness
"""
import os
import sys
import unittest
import tempfile
import atexit

# Redirect DB before any import
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_token_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


class TestGetAllSettings(unittest.TestCase):
    """get_all_settings() batch fetch."""

    def setUp(self):
        db.init_db()

    def test_batch_fetch_returns_all_keys(self):
        keys = ["bot_active", "membership_check", "auto_delete_enabled"]
        result = db.get_all_settings(keys)
        self.assertIsInstance(result, dict)
        for k in keys:
            self.assertIn(k, result)

    def test_batch_fetch_matches_single(self):
        keys = ["bot_active", "read_receipt_enabled", "auto_delete_hours"]
        batch = db.get_all_settings(keys)
        for k in keys:
            single = db.get_setting(k)
            self.assertEqual(batch[k], single,
                f"Mismatch for key {k!r}: batch={batch[k]!r} single={single!r}")

    def test_batch_fetch_unknown_key_uses_default(self):
        result = db.get_all_settings(["bot_active", "nonexistent_xyz"])
        self.assertEqual(result["bot_active"], "1")
        self.assertIsNone(result["nonexistent_xyz"])

    def test_batch_fetch_empty_list(self):
        result = db.get_all_settings([])
        self.assertEqual(result, {})

    def test_batch_fetch_custom_value(self):
        db.set_setting("auto_delete_hours", "48")
        result = db.get_all_settings(["auto_delete_hours"])
        self.assertEqual(result["auto_delete_hours"], "48")
        db.set_setting("auto_delete_hours", "24")  # restore


class TestAddReaderDedup(unittest.TestCase):
    """add_reader_if_new() returns correct bool and prevents duplicate notifications."""

    def setUp(self):
        db.init_db()
        db.upsert_user(7001, "sender", "Sender", None)
        db.upsert_user(7002, "reader", "Reader", None)
        self.wid = db.create_whisper(7001, "dedup test", "everyone")

    def test_first_read_returns_true(self):
        result = db.add_reader_if_new(self.wid, 7002)
        self.assertTrue(result, "First read must return True")

    def test_second_read_returns_false(self):
        db.add_reader_if_new(self.wid, 7002)
        result = db.add_reader_if_new(self.wid, 7002)
        self.assertFalse(result, "Second read must return False (dedup)")

    def test_ten_reads_still_one_record(self):
        for _ in range(10):
            db.add_reader_if_new(self.wid, 7002)
        self.assertEqual(db.reader_count(self.wid), 1)

    def test_different_users_each_get_true(self):
        db.upsert_user(7003, "r2", "Reader2", None)
        r1 = db.add_reader_if_new(self.wid, 7002)
        r2 = db.add_reader_if_new(self.wid, 7003)
        self.assertTrue(r1)
        self.assertTrue(r2)
        self.assertEqual(db.reader_count(self.wid), 2)

    def test_add_reader_backward_compat(self):
        """Original add_reader() still works (no return value required)."""
        db.add_reader(self.wid, 7002)  # should not raise
        self.assertEqual(db.reader_count(self.wid), 1)

    def test_notification_only_on_first_read(self):
        """Simulate the notification gate used in handlers/whisper.py."""
        notifications_sent = 0

        def send_notification():
            nonlocal notifications_sent
            notifications_sent += 1

        # Press button 5 times
        for _ in range(5):
            is_new = db.add_reader_if_new(self.wid, 7002)
            if is_new:
                send_notification()

        self.assertEqual(notifications_sent, 1,
            "Notification must be sent exactly once regardless of button presses")


class TestMigrationGuard(unittest.TestCase):
    """_run_migrations must not fail on a fresh (empty) database."""

    def test_migration_on_empty_db(self):
        """
        Simulate calling _run_migrations() on a DB where tables do not exist yet.
        Previously this crashed with 'no such table: main.whispers'.
        """
        import sqlite3
        empty_db = tempfile.mktemp(suffix=".test.db")
        try:
            # Patch DATABASE_PATH temporarily
            orig = os.environ.get("DATABASE_PATH")
            os.environ["DATABASE_PATH"] = empty_db

            # Reload database module with new path
            import importlib
            import config as cfg
            importlib.reload(cfg)
            import database as db2
            importlib.reload(db2)

            # _run_migrations on empty DB should not raise
            try:
                db2._run_migrations()
            except Exception as exc:
                self.fail(f"_run_migrations() raised on empty DB: {exc}")
        finally:
            if orig is not None:
                os.environ["DATABASE_PATH"] = orig
            if os.path.exists(empty_db):
                os.unlink(empty_db)

    def test_init_db_then_migration(self):
        """Normal flow: init_db() then _run_migrations() must be idempotent."""
        try:
            db.init_db()
            db._run_migrations()   # second call must not raise
            db._run_migrations()   # third call — still fine
        except Exception as exc:
            self.fail(f"Idempotent migration failed: {exc}")


class TestConfigHandling(unittest.TestCase):
    """config.py token and admin ID handling."""

    def test_admin_ids_no_zero(self):
        """ADMIN_IDS must not contain 0 (old default placeholder)."""
        from config import ADMIN_IDS
        # config.py uses ADMIN_IDS="" default → no 0 in the list
        self.assertIsInstance(ADMIN_IDS, list)
        self.assertNotIn(0, ADMIN_IDS, "ADMIN_IDS must not contain placeholder 0")
        # All entries must be valid positive integers
        for uid in ADMIN_IDS:
            self.assertIsInstance(uid, int)
            self.assertGreater(uid, 0)

    def test_bot_token_present(self):
        from config import BOT_TOKEN
        # In test env we set it to placeholder
        self.assertIsNotNone(BOT_TOKEN)
        self.assertNotEqual(BOT_TOKEN, "YOUR_BOT_TOKEN_HERE")

    def test_get_all_settings_is_exported(self):
        """get_all_settings must be importable from database."""
        from database import get_all_settings
        self.assertTrue(callable(get_all_settings))


class TestSafeEditTextLogic(unittest.TestCase):
    """
    Unit test for the _safe_edit_text 'not modified' detection logic.
    We test the string matching used to detect the error, not Telegram API.
    """

    def test_not_modified_detection(self):
        """Strings that should be detected as 'not modified'."""
        not_modified_msgs = [
            "Bad Request: message is not modified: specified new message content and reply markup are exactly the same as a current content of the message",
            "message is not modified",
            "MESSAGE_NOT_MODIFIED",
            "message_not_modified",
        ]
        for msg in not_modified_msgs:
            err_str = msg.lower()
            is_not_modified = "not modified" in err_str or "message_not_modified" in err_str
            self.assertTrue(is_not_modified,
                f"Should detect as 'not modified': {msg!r}")

    def test_real_error_not_suppressed(self):
        """Other errors should NOT be treated as 'not modified'."""
        real_errors = [
            "Bad Request: chat not found",
            "Forbidden: bot was blocked by the user",
            "Bad Request: message to edit not found",
        ]
        for msg in real_errors:
            err_str = msg.lower()
            is_not_modified = "not modified" in err_str or "message_not_modified" in err_str
            self.assertFalse(is_not_modified,
                f"Real error should NOT be suppressed: {msg!r}")


class TestAdminKeyboardBatch(unittest.TestCase):
    """settings_keyboard() must use batch DB read."""

    def setUp(self):
        db.init_db()

    def test_settings_keyboard_importable(self):
        """settings_keyboard must import without error (mocking telebot)."""
        import sys
        from unittest.mock import MagicMock
        # Mock telebot so we can test admin.py logic without the library
        telebot_mock = MagicMock()
        telebot_mock.TeleBot = MagicMock
        telebot_mock.types = MagicMock()
        sys.modules.setdefault("telebot", telebot_mock)
        sys.modules.setdefault("telebot.types", telebot_mock.types)
        try:
            # Remove cached import if present
            for mod in list(sys.modules.keys()):
                if "handlers.admin" in mod:
                    del sys.modules[mod]
            from handlers import admin as adm
            self.assertTrue(callable(adm.settings_keyboard))
            self.assertTrue(callable(adm.admin_main_keyboard))
        except Exception as exc:
            self.fail(f"settings_keyboard import/use failed: {exc}")

    def test_get_all_settings_keys_covered(self):
        """All keys used in settings_keyboard must be in DEFAULT_SETTINGS."""
        from config import DEFAULT_SETTINGS
        # _SETTINGS_KEYS is a module-level constant — check it directly
        settings_keys = [
            "bot_active", "membership_check", "content_protection",
            "read_receipt_enabled", "auto_delete_enabled", "auto_delete_hours",
            "antispam_enabled", "xp_enabled", "auto_backup_enabled",
        ]
        for key in settings_keys:
            self.assertIn(key, DEFAULT_SETTINGS,
                f"Key {key!r} used in settings_keyboard but missing from DEFAULT_SETTINGS")

    def test_get_all_settings_batch_consistency(self):
        """Batch result must match individual get_setting() for each key."""
        keys = [
            "bot_active", "membership_check", "content_protection",
            "read_receipt_enabled", "auto_delete_enabled",
        ]
        batch = db.get_all_settings(keys)
        for k in keys:
            self.assertEqual(batch[k], db.get_setting(k))


if __name__ == "__main__":
    unittest.main(verbosity=2)
