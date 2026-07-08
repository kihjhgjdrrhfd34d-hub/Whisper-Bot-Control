"""
tests/test_database.py
Unit tests for core database functions.
Uses an in-memory SQLite database — never touches the production DB.
"""
import json
import os
import sys
import unittest

# ── Redirect DATABASE_PATH to in-memory BEFORE importing database ─────────────
import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"  # valid enough for tests

# Insert project root onto path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from config import GROUP_DEFAULT_SETTINGS


class TestDatabaseInit(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_init_creates_tables(self):
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        required = {"users", "whispers", "whisper_readers", "curious_ones",
                    "settings", "mandatory_channels", "broadcasts"}
        for t in required:
            self.assertIn(t, tables, f"Table '{t}' missing after init_db()")

    def test_default_settings_seeded(self):
        required_keys = [
            "bot_active", "membership_check", "content_protection",
            "read_receipt_enabled", "auto_delete_enabled", "auto_delete_hours",
            "notify_new_user", "notify_block",
        ]
        for key in required_keys:
            val = db.get_setting(key)
            self.assertIsNotNone(val, f"Setting '{key}' not seeded")


class TestUsers(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_upsert_and_get(self):
        db.upsert_user(1001, "alice", "Alice", "Smith")
        u = db.get_user(1001)
        self.assertIsNotNone(u)
        self.assertEqual(u["username"], "alice")
        self.assertEqual(u["first_name"], "Alice")

    def test_upsert_update(self):
        db.upsert_user(1002, "bob", "Bob", None)
        db.upsert_user(1002, "bob_new", "Bobby", None)
        u = db.get_user(1002)
        self.assertEqual(u["username"], "bob_new")
        self.assertEqual(u["first_name"], "Bobby")

    def test_is_new_user(self):
        db.upsert_user(1003, "carol", "Carol", None)
        self.assertTrue(db.is_new_user(1003))
        db.mark_user_started(1003)
        self.assertFalse(db.is_new_user(1003))

    def test_ban_unban(self):
        db.upsert_user(1004, "dave", "Dave", None)
        self.assertFalse(db.is_banned(1004))
        db.ban_user(1004)
        self.assertTrue(db.is_banned(1004))
        db.unban_user(1004)
        self.assertFalse(db.is_banned(1004))

    def test_search_users(self):
        db.upsert_user(1005, "searchme", "Search", None)
        results = db.search_users("searchme")
        self.assertTrue(any(r["user_id"] == 1005 for r in results))

    def test_get_all_users_pagination(self):
        for i in range(2010, 2030):
            db.upsert_user(i, f"user{i}", f"User{i}", None)
        rows, total = db.get_all_users(page=0, per_page=5)
        self.assertEqual(len(rows), 5)
        self.assertGreaterEqual(total, 20)


class TestWhispers(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.upsert_user(3001, "sender", "Sender", None)
        db.upsert_user(3002, "reader", "Reader", None)

    def test_create_and_get(self):
        wid = db.create_whisper(3001, "hello world", "everyone")
        self.assertIsNotNone(wid)
        w = db.get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["content"], "hello world")
        self.assertEqual(w["whisper_type"], "everyone")

    def test_update_content(self):
        wid = db.create_whisper(3001, "original", "everyone")
        db.update_whisper_content(wid, "updated")
        w = db.get_whisper(wid)
        self.assertEqual(w["content"], "updated")

    def test_lock_toggle(self):
        wid = db.create_whisper(3001, "lockme", "everyone")
        w = db.get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)
        new_state = db.toggle_whisper_lock(wid)
        self.assertEqual(new_state, 1)
        new_state = db.toggle_whisper_lock(wid)
        self.assertEqual(new_state, 0)

    def test_delete_whisper(self):
        wid = db.create_whisper(3001, "deleteme", "everyone")
        db.delete_whisper(wid)
        self.assertIsNone(db.get_whisper(wid))

    def test_readers(self):
        wid = db.create_whisper(3001, "readtest", "everyone")
        self.assertEqual(db.reader_count(wid), 0)
        db.add_reader(wid, 3002)
        self.assertEqual(db.reader_count(wid), 1)
        readers = db.get_readers(wid)
        self.assertEqual(readers[0]["user_id"], 3002)

    def test_clear_readers(self):
        wid = db.create_whisper(3001, "cleartest", "first_one")
        db.add_reader(wid, 3002)
        db.clear_whisper_readers(wid)
        self.assertEqual(db.reader_count(wid), 0)

    def test_curious_ones(self):
        wid = db.create_whisper(3001, "curioustest", "custom",
                                 target_users=[3001])
        db.add_curious(wid, 3002)
        curious = db.get_curious_ones(wid)
        self.assertEqual(len(curious), 1)
        self.assertEqual(curious[0]["user_id"], 3002)
        self.assertIn("tried_at", curious[0].keys())

    def test_auto_delete_at_set(self):
        wid = db.create_whisper(3001, "expiring", "everyone",
                                 auto_delete_hours=1)
        w = db.get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

    def test_auto_delete_not_set_when_zero(self):
        wid = db.create_whisper(3001, "nodeletion", "everyone",
                                 auto_delete_hours=0)
        w = db.get_whisper(wid)
        self.assertIsNone(w["auto_delete_at"])


class TestCanReadWhisper(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.upsert_user(4001, "s", "Sender", None)
        db.upsert_user(4002, "r1", "Reader1", None)
        db.upsert_user(4003, "r2", "Reader2", None)
        db.upsert_user(4004, "r3", "Reader3", None)
        db.upsert_user(4005, "r4", "Reader4", None)

    def test_everyone_allowed(self):
        wid = db.create_whisper(4001, "pub", "everyone")
        can, reason = db.can_read_whisper(wid, 4002)
        self.assertTrue(can)
        self.assertEqual(reason, "allowed")

    def test_sender_always_allowed(self):
        wid = db.create_whisper(4001, "mine", "first_one")
        can, reason = db.can_read_whisper(wid, 4001)
        self.assertTrue(can)
        self.assertEqual(reason, "sender")

    def test_first_one_taken(self):
        wid = db.create_whisper(4001, "first", "first_one")
        db.add_reader(wid, 4002)
        can, reason = db.can_read_whisper(wid, 4003)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_first_three_slots(self):
        wid = db.create_whisper(4001, "three", "first_three")
        db.add_reader(wid, 4002)
        db.add_reader(wid, 4003)
        db.add_reader(wid, 4004)
        can, reason = db.can_read_whisper(wid, 4005)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_custom_allowed(self):
        wid = db.create_whisper(4001, "cust", "custom",
                                  target_users=[4002])
        can, reason = db.can_read_whisper(wid, 4002)
        self.assertTrue(can)

    def test_custom_not_target(self):
        wid = db.create_whisper(4001, "cust2", "custom",
                                  target_users=[4002])
        can, reason = db.can_read_whisper(wid, 4003)
        self.assertFalse(can)
        self.assertEqual(reason, "not_target")

    def test_locked(self):
        wid = db.create_whisper(4001, "locked", "everyone")
        db.toggle_whisper_lock(wid)
        can, reason = db.can_read_whisper(wid, 4002)
        self.assertFalse(can)
        self.assertEqual(reason, "locked")

    def test_not_found(self):
        can, reason = db.can_read_whisper("nonexistent_id", 4002)
        self.assertFalse(can)
        self.assertEqual(reason, "not_found")


class TestSettings(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_get_default(self):
        val = db.get_setting("bot_active")
        self.assertEqual(val, "1")

    def test_set_and_get(self):
        db.set_setting("test_key", "test_value")
        self.assertEqual(db.get_setting("test_key"), "test_value")

    def test_overwrite(self):
        db.set_setting("bot_active", "0")
        self.assertEqual(db.get_setting("bot_active"), "0")
        db.set_setting("bot_active", "1")  # restore
        self.assertEqual(db.get_setting("bot_active"), "1")

    def test_missing_key_returns_none(self):
        val = db.get_setting("definitely_not_a_real_key_xyz")
        self.assertIsNone(val)


class TestGroupSettings(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_ensure_creates_settings(self):
        db.ensure_group_settings(-100123456789)
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM group_settings WHERE chat_id=?", (-100123456789,)
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["public_whispers_enabled"], 1)
        self.assertEqual(row["anonymous_enabled"], 1)
        self.assertEqual(row["read_notifications"], 1)
        self.assertEqual(row["auto_delete_minutes"], 0)

    def test_get_group_settings_auto_creates(self):
        settings = db.get_group_settings(-100987654321)
        self.assertEqual(settings["chat_id"], -100987654321)
        self.assertEqual(settings["public_whispers_enabled"], 1)
        self.assertEqual(settings["anonymous_enabled"], 1)
        self.assertEqual(settings["read_notifications"], 1)
        self.assertEqual(settings["auto_delete_minutes"], 0)

    def test_get_group_settings_returns_all_keys(self):
        settings = db.get_group_settings(-100111222333)
        for key in GROUP_DEFAULT_SETTINGS:
            self.assertIn(key, settings)
        self.assertIn("chat_id", settings)

    def test_update_single_setting(self):
        chat_id = -100555666777
        db.ensure_group_settings(chat_id)
        db.update_group_setting(chat_id, "public_whispers_enabled", "0")
        settings = db.get_group_settings(chat_id)
        self.assertEqual(settings["public_whispers_enabled"], 0)
        self.assertEqual(settings["anonymous_enabled"], 1)
        self.assertEqual(settings["read_notifications"], 1)
        self.assertEqual(settings["auto_delete_minutes"], 0)

    def test_update_multiple_independently(self):
        chat_id = -100888999000
        db.update_group_setting(chat_id, "public_whispers_enabled", "0")
        db.update_group_setting(chat_id, "anonymous_enabled", "0")
        db.update_group_setting(chat_id, "auto_delete_minutes", "30")
        settings = db.get_group_settings(chat_id)
        self.assertEqual(settings["public_whispers_enabled"], 0)
        self.assertEqual(settings["anonymous_enabled"], 0)
        self.assertEqual(settings["read_notifications"], 1)
        self.assertEqual(settings["auto_delete_minutes"], 30)

    def test_update_non_existent_key_raises(self):
        with self.assertRaises(ValueError) as ctx:
            db.update_group_setting(-100000000001, "nonexistent_key", "1")
        self.assertIn("Invalid group setting key", str(ctx.exception))

    def test_multiple_groups_isolated(self):
        db.update_group_setting(-100111, "public_whispers_enabled", "0")
        db.update_group_setting(-100222, "public_whispers_enabled", "1")
        g1 = db.get_group_settings(-100111)
        g2 = db.get_group_settings(-100222)
        self.assertEqual(g1["public_whispers_enabled"], 0)
        self.assertEqual(g2["public_whispers_enabled"], 1)

    def test_ensure_is_idempotent(self):
        chat_id = -100333444555
        db.ensure_group_settings(chat_id)
        db.ensure_group_settings(chat_id)
        db.ensure_group_settings(chat_id)
        with db.get_conn() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) FROM group_settings WHERE chat_id=?", (chat_id,)
            ).fetchone()
        self.assertEqual(rows[0], 1)

    def test_update_without_explicit_ensure(self):
        settings = db.get_group_settings(-100666777888)
        self.assertEqual(settings["public_whispers_enabled"], 1)
        db.update_group_setting(-100666777888, "public_whispers_enabled", "0")
        settings = db.get_group_settings(-100666777888)
        self.assertEqual(settings["public_whispers_enabled"], 0)


class TestMandatoryChannels(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_add_and_get(self):
        db.add_mandatory_channel("@testchannel", "Test Channel")
        channels = db.get_mandatory_channels()
        ids = [ch["channel_id"] for ch in channels]
        self.assertIn("@testchannel", ids)

    def test_remove(self):
        db.add_mandatory_channel("@removeme", "Remove Me")
        db.remove_mandatory_channel("@removeme")
        channels = db.get_mandatory_channels()
        ids = [ch["channel_id"] for ch in channels]
        self.assertNotIn("@removeme", ids)

    def test_duplicate_ignored(self):
        db.add_mandatory_channel("@dup", "Dup")
        db.add_mandatory_channel("@dup", "Dup Again")
        channels = [ch for ch in db.get_mandatory_channels()
                    if ch["channel_id"] == "@dup"]
        self.assertEqual(len(channels), 1)


class TestStats(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_get_stats_returns_dict(self):
        s = db.get_stats()
        for key in ["total_users", "banned_users", "active_users",
                    "total_whispers", "total_reads", "new_today", "whispers_today"]:
            self.assertIn(key, s)

    def test_get_user_stats(self):
        db.upsert_user(5001, "statuser", "StatUser", None)
        s = db.get_user_stats(5001)
        for key in ["sent", "received_reads", "read_others", "curious_on_mine",
                    "locked", "type_everyone", "type_first_one",
                    "type_first_three", "type_custom"]:
            self.assertIn(key, s)


class TestDeleteExpired(unittest.TestCase):
    def setUp(self):
        db.init_db()
        db.upsert_user(6001, "exp", "Expired", None)

    def test_delete_expired_returns_count(self):
        from datetime import datetime, timedelta
        # Create a whisper that expired 1 hour ago
        with db.get_conn() as conn:
            past = (datetime.utcnow() - timedelta(hours=1)).isoformat()
            conn.execute(
                "INSERT INTO whispers (whisper_id, sender_id, content,"
                " whisper_type, auto_delete_at) VALUES (?,?,?,?,?)",
                ("expiredwid", 6001, "expired content", "everyone", past),
            )
            conn.commit()
        count = db.delete_expired_whispers()
        self.assertGreaterEqual(count, 1)
        self.assertIsNone(db.get_whisper("expiredwid"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
