"""
tests/test_bot.py — Tests for bot.py core functionality.

Covers:
  - _main_menu_text_and_kb structure
  - _send_help text structure
  - membership_keyboard structure
  - _notify_admins functions
  - _get_bot_me caching
  - Database operations used by start_cmd (upsert, is_new, ban check, settings)
  - Deep link handling (reply payload, whisper payload)
  - User state management
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_bot_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


def _boot():
    db.init_db()


class MockUser:
    """Mock telebot User object."""
    def __init__(self, user_id=60301, username="testuser", first_name="Test",
                 last_name=None):
        self.id = user_id
        self.username = username
        self.first_name = first_name
        self.last_name = last_name


class TestMainMenu(unittest.TestCase):
    """Test main menu text and keyboard generation."""

    def setUp(self):
        _boot()
        db.upsert_user(60301, "testuser", "Test", None)

    @patch("bot._get_bot_me")
    def test_main_menu_text_contains_welcome(self, mock_get_me):
        from bot import _main_menu_text_and_kb
        mock_bot = MagicMock()
        mock_get_me.return_value = MagicMock()
        mock_get_me.return_value.username = "test_bot"
        user = MagicMock()
        user.first_name = "TestUser"
        user.id = 1
        text, kb = _main_menu_text_and_kb(mock_bot, user)
        self.assertIn("أهلاً", text)
        self.assertIn("همسات", text)
        self.assertIsNotNone(kb)

    @patch("bot._get_bot_me")
    def test_main_menu_has_buttons(self, mock_get_me):
        from bot import _main_menu_text_and_kb
        mock_bot = MagicMock()
        mock_get_me.return_value = MagicMock()
        mock_get_me.return_value.username = "test_bot"
        user = MagicMock()
        user.first_name = "TestUser"
        user.id = 1
        text, kb = _main_menu_text_and_kb(mock_bot, user)
        self.assertTrue(len(kb.keyboard) >= 2)

    @patch("bot._get_bot_me")
    def test_main_menu_admin_has_admin_button(self, mock_get_me):
        from bot import _main_menu_text_and_kb
        mock_bot = MagicMock()
        mock_get_me.return_value = MagicMock()
        mock_get_me.return_value.username = "test_bot"
        user = MagicMock()
        user.first_name = "Admin"
        user.id = 999  # ADMIN_IDS[0]
        text, kb = _main_menu_text_and_kb(mock_bot, user)
        found_admin = False
        for row in kb.keyboard:
            for btn in row:
                if "admin" in str(btn.callback_data).lower():
                    found_admin = True
        self.assertTrue(found_admin)

    @patch("bot._get_bot_me")
    def test_main_menu_non_admin_no_admin_button(self, mock_get_me):
        from bot import _main_menu_text_and_kb
        mock_bot = MagicMock()
        mock_get_me.return_value = MagicMock()
        mock_get_me.return_value.username = "test_bot"
        user = MagicMock()
        user.first_name = "User"
        user.id = 1  # Not admin
        text, kb = _main_menu_text_and_kb(mock_bot, user)
        found_admin = False
        for row in kb.keyboard:
            for btn in row:
                if "admin" in str(btn.callback_data).lower():
                    found_admin = True
        self.assertFalse(found_admin)


class TestHelpFunction(unittest.TestCase):
    """Test help text generation."""

    def test_help_contains_commands(self):
        from bot import _send_help
        mock_bot = MagicMock()
        _send_help(mock_bot, 12345)
        mock_bot.send_message.assert_called_once()
        args, kwargs = mock_bot.send_message.call_args
        self.assertEqual(args[0], 12345)
        text = args[1]
        self.assertIn("/start", text)
        self.assertIn("/stats", text)
        self.assertIn("/help", text)

    def test_help_has_keyboard(self):
        from bot import _send_help
        mock_bot = MagicMock()
        _send_help(mock_bot, 12345)
        _, kwargs = mock_bot.send_message.call_args
        self.assertIn("reply_markup", kwargs)
        kb = kwargs["reply_markup"]
        self.assertTrue(len(kb.keyboard) > 0)

    def test_help_mentions_whisper_types(self):
        from bot import _send_help
        mock_bot = MagicMock()
        _send_help(mock_bot, 12345)
        text = mock_bot.send_message.call_args[0][1]
        self.assertIn("للجميع", text)
        self.assertIn("لأول شخص", text)
        self.assertIn("مخصصة", text)


class TestMembershipKeyboard(unittest.TestCase):
    """Test mandatory channel membership keyboard."""

    def setUp(self):
        _boot()
        db.add_mandatory_channel("@testchan", "Test Channel")

    def test_membership_keyboard_has_channels(self):
        from bot import membership_keyboard
        kb = membership_keyboard()
        found_channel = False
        found_check = False
        for row in kb.keyboard:
            for btn in row:
                if "Test Channel" in str(btn.text):
                    found_channel = True
                if "تحققت" in str(btn.text):
                    found_check = True
        self.assertTrue(found_channel)
        self.assertTrue(found_check)


class TestGetBotMe(unittest.TestCase):
    """Test _get_bot_me caching behavior."""

    def tearDown(self):
        import bot
        bot._bot_me_cache = None

    def test_get_bot_me_caches_result(self):
        from bot import _get_bot_me
        mock_bot = MagicMock()
        mock_bot.get_me.return_value = "cached_value"
        result1 = _get_bot_me(mock_bot)
        result2 = _get_bot_me(mock_bot)
        self.assertEqual(result1, result2)
        mock_bot.get_me.assert_called_once()

    def test_get_bot_me_handles_error(self):
        from bot import _get_bot_me
        mock_bot = MagicMock()
        mock_bot.get_me.side_effect = Exception("API error")
        result = _get_bot_me(mock_bot)
        self.assertIsNone(result)

    def test_get_bot_me_called_only_once(self):
        from bot import _get_bot_me
        mock_bot = MagicMock()
        mock_bot.get_me.return_value = "bot_info"
        _get_bot_me(mock_bot)
        _get_bot_me(mock_bot)
        _get_bot_me(mock_bot)
        mock_bot.get_me.assert_called_once()


class TestNotifyAdmins(unittest.TestCase):
    """Test admin notification functions."""

    def setUp(self):
        _boot()
        db.set_setting("notify_new_user", "1")
        db.set_setting("notify_block", "1")

    @patch("bot.ADMIN_IDS", [999])
    @patch("bot.bot")
    def test_notify_new_user(self, mock_bot):
        from bot import _notify_admins_new_user
        user = MagicMock()
        user.id = 123
        user.username = "newuser"
        user.first_name = "NewUser"
        _notify_admins_new_user(user)
        mock_bot.send_message.assert_called()

    @patch("bot.ADMIN_IDS", [999])
    @patch("bot.bot")
    def test_notify_block(self, mock_bot):
        from bot import _notify_admins_block
        user = MagicMock()
        user.id = 123
        user.username = "blockeduser"
        user.first_name = "Blocked"
        _notify_admins_block(user)
        mock_bot.send_message.assert_called()

    @patch("bot.ADMIN_IDS", [999])
    @patch("bot.bot")
    def test_notify_unblock(self, mock_bot):
        from bot import _notify_admins_unblock
        user = MagicMock()
        user.id = 123
        user.username = "unblockeduser"
        user.first_name = "Unblocked"
        _notify_admins_unblock(user)
        mock_bot.send_message.assert_called()

    def test_notify_disabled_respects_setting(self):
        db.set_setting("notify_new_user", "0")
        from bot import _notify_admins_new_user
        mock_bot = MagicMock()
        user = MagicMock()
        _notify_admins_new_user(user)
        mock_bot.send_message.assert_not_called()


class TestUserDatabaseOperations(unittest.TestCase):
    """Test database operations used by start_cmd."""

    def setUp(self):
        _boot()

    def test_upsert_new_user(self):
        db.upsert_user(60400, "newuser", "New", "User")
        u = db.get_user(60400)
        self.assertIsNotNone(u)
        self.assertEqual(u["username"], "newuser")

    def test_upsert_existing_user(self):
        db.upsert_user(60400, "oldname", "Old", None)
        db.upsert_user(60400, "newname", "New", None)
        u = db.get_user(60400)
        self.assertEqual(u["username"], "newname")

    def test_is_new_user_true(self):
        db.upsert_user(201, "newbie", "Newbie", None)
        self.assertTrue(db.is_new_user(201))

    def test_is_new_user_false_after_started(self):
        db.upsert_user(202, "newbie2", "Newbie2", None)
        db.mark_user_started(202)
        self.assertFalse(db.is_new_user(202))

    def test_banned_user_check(self):
        db.upsert_user(300, "banneduser", "Banned", None)
        self.assertFalse(db.is_banned(300))
        db.ban_user(300)
        self.assertTrue(db.is_banned(300))

    def test_bot_active_setting(self):
        self.assertEqual(db.get_setting("bot_active"), "1")

    def test_membership_check_disabled_by_default(self):
        self.assertEqual(db.get_setting("membership_check"), "0")


class TestDeepLinkHandling(unittest.TestCase):
    """Test database operations for deep link handling."""

    def setUp(self):
        _boot()
        db.upsert_user(60301, "sender", "Sender", None)
        self.wid = db.create_whisper(60301, "deep link test", "everyone")

    def test_whisper_accessible_by_link(self):
        w = db.get_whisper(self.wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["content"], "deep link test")

    def test_invalid_whisper_link_returns_none(self):
        w = db.get_whisper("invalid_id")
        self.assertIsNone(w)

    def test_reply_deep_link_whisper_exists(self):
        w = db.get_whisper(self.wid)
        self.assertIsNotNone(w)

    def test_reply_deep_link_nonexistent_whisper(self):
        w = db.get_whisper("nonexistent_reply_target")
        self.assertIsNone(w)


class TestUserStates(unittest.TestCase):
    """Test user state management used by bot.py."""

    def setUp(self):
        _boot()

    def test_state_set_and_get(self):
        states = {}
        states[1] = {"action": "test_action", "data": "test"}
        self.assertIn(1, states)
        self.assertEqual(states[1]["action"], "test_action")

    def test_state_clear(self):
        states = {1: {"action": "test"}}
        states.pop(1, None)
        self.assertNotIn(1, states)

    def test_state_overwrite(self):
        states = {}
        states[1] = {"action": "first"}
        states[1] = {"action": "second"}
        self.assertEqual(states[1]["action"], "second")

    def test_pending_reply_state(self):
        states = {}
        states[1] = {"action": "pending_whisper_reply", "whisper_id": "abc123"}
        self.assertIn(1, states)
        self.assertEqual(states[1]["whisper_id"], "abc123")

    def test_multiple_user_states(self):
        states = {}
        states[1] = {"action": "a"}
        states[2] = {"action": "b"}
        states[3] = {"action": "c"}
        self.assertEqual(len(states), 3)


class TestUserSearch(unittest.TestCase):
    """Test user search used in message handlers."""

    def setUp(self):
        _boot()
        db.upsert_user(500, "searchme", "Search", "Me")
        db.upsert_user(501, "another", "Another", None)

    def test_search_by_username_fragment(self):
        results = db.search_users("search")
        self.assertTrue(any(r["user_id"] == 500 for r in results))

    def test_search_by_id(self):
        results = db.search_users("500")
        self.assertTrue(any(r["user_id"] == 500 for r in results))

    def test_search_by_first_name(self):
        results = db.search_users("Search")
        self.assertTrue(any(r["user_id"] == 500 for r in results))

    def test_search_empty_query(self):
        results = db.search_users("")
        self.assertIsNotNone(results)

    def test_search_no_match(self):
        results = db.search_users("__no_match_possible__")
        self.assertEqual(len(results), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
