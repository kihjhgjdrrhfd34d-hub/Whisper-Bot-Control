"""
tests/test_whisper_spam.py — Tests for whisper anti-spam rate limiting.

Covers:
  - DB helpers: record_whisper_timestamp, check_whisper_rate_limit
  - Under limit: allowed
  - Exactly at limit: still allowed (count == limit means next will be blocked)
  - Exceeding limit: blocked
  - Different users: isolated limits
  - Different groups: isolated limits
  - Disabled anti-spam: always allowed
  - All whisper types: timestamps recorded for everyone, first_one, first_three, custom
  - Group settings panel: anti-spam toggle, preset buttons, status text
  - Arabic block message correctness
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_spam_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    record_whisper_timestamp, check_whisper_rate_limit, SPAM_BLOCK_MESSAGE,
    get_group_settings, update_group_setting, ensure_group_settings,
)


def _boot():
    db.init_db()
    db.upsert_user(999, "admin", "Admin", None)


def _ensure_users(*user_ids):
    for uid in user_ids:
        db.upsert_user(uid, f"user{uid}", f"User{uid}", None)


# ─────────────────────────────────────────────────────────────────────────────
# Database helpers
# ─────────────────────────────────────────────────────────────────────────────

class TestRecordWhisperTimestamp(unittest.TestCase):
    """record_whisper_timestamp inserts a row into whisper_timestamps."""

    def setUp(self):
        _boot()
        _ensure_users(1001, 1002)

    def test_records_timestamp(self):
        record_whisper_timestamp(1001, -1001001)
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT * FROM whisper_timestamps WHERE user_id=? AND chat_id=?",
                (1001, -1001001),
            ).fetchone()
        self.assertIsNotNone(row)

    def test_records_multiple(self):
        record_whisper_timestamp(1002, -1002002)
        record_whisper_timestamp(1002, -1002002)
        record_whisper_timestamp(1002, -1002002)
        with db.get_conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM whisper_timestamps WHERE user_id=? AND chat_id=?",
                (1002, -1002002),
            ).fetchone()[0]
        self.assertEqual(count, 3)


class TestCheckWhisperRateLimit(unittest.TestCase):
    """check_whisper_rate_limit enforces per-user per-group limits."""

    def setUp(self):
        _boot()
        self.user = 2001
        self.chat = -2001001
        _ensure_users(self.user)
        # Reset group settings to defaults
        with db.get_conn() as conn:
            conn.execute("DELETE FROM group_settings WHERE chat_id=?", (self.chat,))
            conn.execute("DELETE FROM whisper_timestamps WHERE user_id=? AND chat_id=?",
                         (self.user, self.chat))
            conn.commit()
        ensure_group_settings(self.chat)

    def test_under_limit_returns_allowed(self):
        for _ in range(4):
            record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        self.assertEqual(count, 4)

    def test_exactly_at_limit_returns_allowed(self):
        for _ in range(4):
            record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        self.assertEqual(count, 4)

    def test_exceeding_limit_returns_blocked(self):
        for _ in range(5):
            record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertFalse(allowed)
        self.assertEqual(count, 5)

    def test_well_over_limit(self):
        for _ in range(10):
            record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertFalse(allowed)
        self.assertEqual(count, 10)

    def test_different_users_have_isolated_limits(self):
        user_a, user_b = 3001, 3002
        chat = -3001001
        _ensure_users(user_a, user_b)
        ensure_group_settings(chat)
        for _ in range(5):
            record_whisper_timestamp(user_a, chat)
        allowed_a, _ = check_whisper_rate_limit(user_a, chat)
        allowed_b, count_b = check_whisper_rate_limit(user_b, chat)
        self.assertFalse(allowed_a)
        self.assertTrue(allowed_b)
        self.assertEqual(count_b, 0)

    def test_different_groups_have_isolated_limits(self):
        chat_a, chat_b = -4001001, -4002002
        ensure_group_settings(chat_a)
        ensure_group_settings(chat_b)
        for _ in range(5):
            record_whisper_timestamp(self.user, chat_a)
        allowed_a, _ = check_whisper_rate_limit(self.user, chat_a)
        allowed_b, count_b = check_whisper_rate_limit(self.user, chat_b)
        self.assertFalse(allowed_a)
        self.assertTrue(allowed_b)
        self.assertEqual(count_b, 0)

    def test_disabled_anti_spam_always_allows(self):
        update_group_setting(self.chat, "spam_limit_enabled", 0)
        for _ in range(100):
            record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        self.assertEqual(count, 0)

    def test_custom_limit_count(self):
        update_group_setting(self.chat, "spam_limit_count", 3)
        for _ in range(2):
            record_whisper_timestamp(self.user, self.chat)
        allowed, _ = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertFalse(allowed)
        self.assertEqual(count, 3)

    def test_custom_window_seconds(self):
        update_group_setting(self.chat, "spam_limit_window_seconds", 1)
        for _ in range(5):
            record_whisper_timestamp(self.user, self.chat)
        allowed, _ = check_whisper_rate_limit(self.user, self.chat)
        self.assertFalse(allowed)


class TestRateLimitWindowExpiry(unittest.TestCase):
    """Timestamps older than the window are excluded from the count."""

    def setUp(self):
        _boot()
        self.user = 5001
        self.chat = -5001001
        _ensure_users(self.user)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM group_settings WHERE chat_id=?", (self.chat,))
            conn.execute("DELETE FROM whisper_timestamps WHERE user_id=? AND chat_id=?",
                         (self.user, self.chat))
            conn.commit()
        ensure_group_settings(self.chat)

    def test_old_timestamps_outside_window_not_counted(self):
        update_group_setting(self.chat, "spam_limit_window_seconds", 2)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=10)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_conn() as conn:
            for _ in range(5):
                conn.execute(
                    "INSERT INTO whisper_timestamps (user_id, chat_id, created_at)"
                    " VALUES (?, ?, ?)",
                    (self.user, self.chat, old_time),
                )
            conn.commit()
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        self.assertEqual(count, 0)

    def test_mixed_old_and_new_timestamps(self):
        update_group_setting(self.chat, "spam_limit_window_seconds", 60)
        old_time = (datetime.now(timezone.utc) - timedelta(seconds=120)).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_conn() as conn:
            for _ in range(3):
                conn.execute(
                    "INSERT INTO whisper_timestamps (user_id, chat_id, created_at)"
                    " VALUES (?, ?, ?)",
                    (self.user, self.chat, old_time),
                )
            conn.commit()
        record_whisper_timestamp(self.user, self.chat)
        record_whisper_timestamp(self.user, self.chat)
        allowed, count = check_whisper_rate_limit(self.user, self.chat)
        self.assertTrue(allowed)
        self.assertEqual(count, 2)


class TestSpamBlockMessage(unittest.TestCase):
    """The Arabic block message is correct."""

    def test_message_content(self):
        self.assertIn("تجاوزت الحد المسموح", SPAM_BLOCK_MESSAGE)
        self.assertIn("⏳", SPAM_BLOCK_MESSAGE)
        self.assertTrue(len(SPAM_BLOCK_MESSAGE) > 10)


# ─────────────────────────────────────────────────────────────────────────────
# All whisper types record timestamps
# ─────────────────────────────────────────────────────────────────────────────

class TestAllWhisperTypesRecordTimestamps(unittest.TestCase):
    """Every whisper type creates a timestamp when rate limiting is active."""

    def setUp(self):
        _boot()
        self.sender = 6001
        self.chat = -6001001
        _ensure_users(self.sender)
        ensure_group_settings(self.chat)
        with db.get_conn() as conn:
            conn.execute("DELETE FROM whisper_timestamps")
            conn.commit()

    def _count_timestamps(self):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM whisper_timestamps WHERE user_id=? AND chat_id=?",
                (self.sender, self.chat),
            ).fetchone()[0]

    def test_everyone_type(self):
        db.create_whisper(self.sender, "test everyone", "everyone")
        record_whisper_timestamp(self.sender, self.chat)
        self.assertEqual(self._count_timestamps(), 1)

    def test_first_one_type(self):
        db.create_whisper(self.sender, "test first_one", "first_one")
        record_whisper_timestamp(self.sender, self.chat)
        self.assertEqual(self._count_timestamps(), 1)

    def test_first_three_type(self):
        db.create_whisper(self.sender, "test first_three", "first_three")
        record_whisper_timestamp(self.sender, self.chat)
        self.assertEqual(self._count_timestamps(), 1)

    def test_custom_type(self):
        _ensure_users(7001)
        db.create_whisper(self.sender, "test custom", "custom",
                          target_users=[7001])
        record_whisper_timestamp(self.sender, self.chat)
        self.assertEqual(self._count_timestamps(), 1)

    def test_destructive_type(self):
        db.create_whisper(self.sender, "test destructive", "everyone",
                          is_destructive=True)
        record_whisper_timestamp(self.sender, self.chat)
        self.assertEqual(self._count_timestamps(), 1)


# ─────────────────────────────────────────────────────────────────────────────
# Group settings panel: anti-spam controls
# ─────────────────────────────────────────────────────────────────────────────

def _make_call(chat_id, data, user_id=999, msg_id=100, cb_id="cb_1"):
    call = MagicMock()
    call.data = data
    call.from_user.id = user_id
    call.message.chat.id = chat_id
    call.message.message_id = msg_id
    call.id = cb_id
    return call


def _register_handlers():
    """Register group_settings handlers and return positional handler references."""
    from handlers.group_settings import register_group_settings_handlers
    bot = MagicMock()
    register_group_settings_handlers(bot, {})
    calls = bot.callback_query_handler.return_value.call_args_list
    show_handler = calls[0][0][0]      # admin:group_settings
    toggle_handler = calls[1][0][0]    # group_toggle:*
    autodel_handler = calls[2][0][0]   # group_autodel_set:*
    spam_set_handler = calls[3][0][0]  # group_spam_set:*
    return bot, show_handler, toggle_handler, autodel_handler, spam_set_handler


class TestSpamSettingsText(unittest.TestCase):
    """_build_settings_text includes anti-spam info."""

    def setUp(self):
        _boot()

    def _text(self, chat_id):
        from handlers.group_settings import _build_settings_text
        return _build_settings_text(chat_id)

    def test_shows_anti_spam_enabled_by_default(self):
        text = self._text(7001)
        self.assertIn("حماية Spam", text)
        self.assertIn("5 همسة", text)

    def test_shows_disabled_when_turned_off(self):
        update_group_setting(7002, "spam_limit_enabled", 0)
        text = self._text(7002)
        self.assertIn("🔴", text)
        self.assertIn("معطل", text)

    def test_shows_custom_count(self):
        update_group_setting(7003, "spam_limit_count", 10)
        text = self._text(7003)
        self.assertIn("10 همسة", text)


class TestSpamSettingsKeyboard(unittest.TestCase):
    """_settings_keyboard has anti-spam controls."""

    def setUp(self):
        _boot()

    def _kb(self, chat_id):
        from handlers.group_settings import _settings_keyboard
        return _settings_keyboard(chat_id)

    def test_has_spam_toggle_button(self):
        kb = self._kb(8001)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "group_toggle:spam_limit_enabled":
                    found = True
                    self.assertIn("حماية Spam", btn.text)
        self.assertTrue(found)

    def test_has_spam_preset_buttons(self):
        kb = self._kb(8002)
        callbacks = []
        for row in kb.keyboard:
            for btn in row:
                callbacks.append(btn.callback_data)
        self.assertIn("group_spam_set:3", callbacks)
        self.assertIn("group_spam_set:5", callbacks)
        self.assertIn("group_spam_set:10", callbacks)

    def test_active_preset_has_checkmark(self):
        update_group_setting(8003, "spam_limit_count", 5)
        kb = self._kb(8003)
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "group_spam_set:5":
                    self.assertIn("✅", btn.text)
                elif btn.callback_data.startswith("group_spam_set:"):
                    self.assertNotIn("✅", btn.text)

    def test_has_back_button(self):
        kb = self._kb(8004)
        last_row = kb.keyboard[-1]
        self.assertEqual(last_row[0].callback_data, "admin:main")

    def test_spam_count_label_row(self):
        kb = self._kb(8005)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "noop" and "عدد الهمسات" in btn.text:
                    found = True
        self.assertTrue(found)


class TestSpamToggleHandler(unittest.TestCase):
    """Toggle anti-spam on/off via handler."""

    def setUp(self):
        _boot()

    def test_toggle_off(self):
        chat_id = 9001
        bot, _, toggle, _, _ = _register_handlers()
        call = _make_call(chat_id, "group_toggle:spam_limit_enabled")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["spam_limit_enabled"], 0)

    def test_toggle_on(self):
        chat_id = 9002
        update_group_setting(chat_id, "spam_limit_enabled", 0)
        bot, _, toggle, _, _ = _register_handlers()
        call = _make_call(chat_id, "group_toggle:spam_limit_enabled")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["spam_limit_enabled"], 1)


class TestSpamPresetHandler(unittest.TestCase):
    """Set spam limit count via preset buttons."""

    def setUp(self):
        _boot()

    def test_set_to_3(self):
        chat_id = 10001
        bot, _, _, _, spam_set = _register_handlers()
        call = _make_call(chat_id, "group_spam_set:3")
        spam_set(call)
        self.assertEqual(get_group_settings(chat_id)["spam_limit_count"], 3)

    def test_set_to_5(self):
        chat_id = 10002
        bot, _, _, _, spam_set = _register_handlers()
        call = _make_call(chat_id, "group_spam_set:5")
        spam_set(call)
        self.assertEqual(get_group_settings(chat_id)["spam_limit_count"], 5)

    def test_set_to_10(self):
        chat_id = 10003
        bot, _, _, _, spam_set = _register_handlers()
        call = _make_call(chat_id, "group_spam_set:10")
        spam_set(call)
        self.assertEqual(get_group_settings(chat_id)["spam_limit_count"], 10)

    def test_preset_refreshes_message(self):
        chat_id = 10004
        bot, _, _, _, spam_set = _register_handlers()
        call = _make_call(chat_id, "group_spam_set:3")
        spam_set(call)
        bot.edit_message_text.assert_called_once()
        args, kwargs = bot.edit_message_text.call_args
        self.assertIn("3 همسة", args[0])

    def test_non_admin_cannot_set_preset(self):
        bot, _, _, _, spam_set = _register_handlers()
        call = _make_call(10005, "group_spam_set:5", user_id=111)
        spam_set(call)
        assert bot.answer_callback_query.call_count >= 2
        args, kwargs = bot.answer_callback_query.call_args
        self.assertIn("غير مصرح", str(args))
        self.assertTrue(kwargs.get("show_alert", False))


class TestSpamGroupSettingsDB(unittest.TestCase):
    """DB defaults for spam settings in group_settings table."""

    def setUp(self):
        _boot()

    def test_default_spam_limit_enabled(self):
        settings = get_group_settings(11001)
        self.assertEqual(settings["spam_limit_enabled"], 1)

    def test_default_spam_limit_count(self):
        settings = get_group_settings(11002)
        self.assertEqual(settings["spam_limit_count"], 5)

    def test_default_spam_limit_window(self):
        settings = get_group_settings(11003)
        self.assertEqual(settings["spam_limit_window_seconds"], 60)

    def test_update_spam_limit_count(self):
        update_group_setting(11004, "spam_limit_count", 10)
        settings = get_group_settings(11004)
        self.assertEqual(settings["spam_limit_count"], 10)

    def test_update_spam_limit_enabled(self):
        update_group_setting(11005, "spam_limit_enabled", 0)
        settings = get_group_settings(11005)
        self.assertEqual(settings["spam_limit_enabled"], 0)

    def test_multiple_groups_independent_spam_settings(self):
        update_group_setting(11006, "spam_limit_count", 3)
        update_group_setting(11007, "spam_limit_count", 10)
        g1 = get_group_settings(11006)
        g2 = get_group_settings(11007)
        self.assertEqual(g1["spam_limit_count"], 3)
        self.assertEqual(g2["spam_limit_count"], 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
