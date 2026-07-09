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


class TestPublicWhispersSetting(unittest.TestCase):
    """Test that public_whispers_enabled setting is enforced in inline handler."""

    def setUp(self):
        _boot()
        self.chat_id = -1001234567890
        self.sender_id = 60201
        db.update_group_setting(self.chat_id, "spam_limit_enabled", 0)

    def _capture_inline_handler(self, bot):
        handlers = []
        def fake_inline_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f
            return deco
        bot.inline_handler = fake_inline_handler
        return handlers

    def _make_query(self, text="test whisper", chat_id=None):
        query = MagicMock()
        query.id = "query_1"
        query.from_user.id = self.sender_id
        query.from_user.username = "alice"
        query.from_user.first_name = "Alice"
        query.query = text
        query.chat_type = "group" if chat_id else "sender"
        if chat_id:
            query._chat = MagicMock()
            query._chat.id = chat_id
            query._chat.type = "group"
        else:
            query._chat = None
        return query

    def _call_handler(self, bot, query):
        handlers = self._capture_inline_handler(bot)
        from handlers.inline import register_inline_handlers
        register_inline_handlers(bot)
        handler_func = handlers[0][1]
        handler_func(query)
        return bot, handler_func

    def _get_result_ids(self, bot):
        if not bot.answer_inline_query.called:
            return []
        _, kwargs = bot.answer_inline_query.call_args
        args = bot.answer_inline_query.call_args[0]
        results = args[1]
        return [r.id for r in results]

    # ── Blocked public whispers ───────────────────────────────────────────

    def test_everyone_filtered_when_disabled(self):
        """When public_whispers_enabled=0, everyone options should not appear."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertFalse(any("everyone" in rid for rid in ids),
                         "everyone must be filtered when public_whispers_enabled=0")

    def test_error_result_present_when_disabled(self):
        """Error result should be shown when public whispers are disabled."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertIn("error:public_disabled", ids,
                      "error result must be present when public whispers disabled")

    def test_no_everyone_db_record_when_blocked(self):
        """No everyone whisper record should be created in DB when disabled."""
        conn = db.get_conn()
        before = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE whisper_type='everyone'"
        ).fetchone()[0]
        conn.close()
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        conn = db.get_conn()
        after = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE whisper_type='everyone'"
        ).fetchone()[0]
        conn.close()
        self.assertEqual(after, before,
                         "no everyone whisper records should be created when setting is disabled")

    # ── Allowed public whispers ──────────────────────────────────────────

    def test_everyone_included_when_enabled(self):
        """When public_whispers_enabled=1, everyone options should appear."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 1)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertTrue(any("everyone" in rid for rid in ids),
                        "everyone must be included when public_whispers_enabled=1")

    def test_everyone_db_record_created_when_allowed(self):
        """Everyone whisper record should be created when setting is enabled."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 1)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        conn = db.get_conn()
        count = conn.execute(
            "SELECT COUNT(*) FROM whispers WHERE whisper_type='everyone'"
        ).fetchone()[0]
        conn.close()
        self.assertGreater(count, 0,
                           "everyone whisper records should be created when setting is enabled")

    # ── Private whispers unaffected ──────────────────────────────────────

    def test_first_one_unaffected_when_disabled(self):
        """first_one should still appear even when public whispers disabled."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertTrue(any("first_one" in rid for rid in ids),
                        "first_one must be unaffected by public_whispers_enabled")

    def test_first_three_unaffected_when_disabled(self):
        """first_three should still appear even when public whispers disabled."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertTrue(any("first_three" in rid for rid in ids),
                        "first_three must be unaffected by public_whispers_enabled")

    def test_custom_unaffected_when_disabled(self):
        """custom should still appear even when public whispers disabled."""
        db.update_group_setting(self.chat_id, "public_whispers_enabled", 0)
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=self.chat_id)
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertTrue(any("custom" in rid for rid in ids),
                        "custom must be unaffected by public_whispers_enabled")

    # ── No chat context (private) ────────────────────────────────────────

    def test_everyone_shown_when_no_chat_context(self):
        """When no chat info (private), everyone options should be shown."""
        db.update_group_setting(None, "public_whispers_enabled", 0)  # no-op, no chat
        bot = MagicMock()
        bot.get_me.return_value = MagicMock()
        bot.get_me.return_value.username = "test_bot"
        query = self._make_query(chat_id=None)  # no chat context
        self._call_handler(bot, query)
        ids = self._get_result_ids(bot)
        self.assertTrue(any("everyone" in rid for rid in ids),
                        "everyone must be shown when no chat context")


if __name__ == "__main__":
    unittest.main(verbosity=2)
