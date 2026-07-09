"""
tests/test_group_settings.py — Tests for handlers/group_settings.py

Covers:
  - _build_settings_text output
  - _settings_keyboard structure
  - Group settings database CRUD
  - Toggle logic for all four settings
  - Admin guard blocks non-admins
  - Values persist after re-reading from DB
  - Keyboard reflects current DB state
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_group_settings_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import get_group_settings, update_group_setting, ensure_group_settings


def _boot():
    db.init_db()
    db.upsert_user(999, "admin", "Admin", None)


# ─── helpers ──────────────────────────────────────────────────────────────────

def _make_call(chat_id, data, user_id=999, msg_id=100, cb_id="cb_1"):
    call = MagicMock()
    call.data = data
    call.from_user.id = user_id
    call.message.chat.id = chat_id
    call.message.message_id = msg_id
    call.id = cb_id
    return call


def _register_and_get_handlers():
    """Register group_settings handlers and return (show_handler, toggle_handler, autodel_handler)."""
    from handlers.group_settings import register_group_settings_handlers
    bot = MagicMock()
    register_group_settings_handlers(bot, {})
    calls = bot.callback_query_handler.return_value.call_args_list
    show_handler = calls[0][0][0] if len(calls) > 0 else None
    toggle_handler = calls[1][0][0] if len(calls) > 1 else None
    autodel_handler = calls[2][0][0] if len(calls) > 2 else None
    return bot, show_handler, toggle_handler, autodel_handler


# ─────────────────────────────────────────────────────────────────────────────
# _build_settings_text
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupSettingsText(unittest.TestCase):
    """_build_settings_text output."""

    def setUp(self):
        _boot()

    def _text(self, chat_id):
        from handlers.group_settings import _build_settings_text
        return _build_settings_text(chat_id)

    def test_title_present(self):
        text = self._text(1001)
        self.assertIn("⚙️", text)
        self.assertIn("إعدادات الهمسات", text)

    def test_shows_all_settings(self):
        text = self._text(1002)
        self.assertIn("الهمسات العامة", text)
        self.assertIn("الهمسات المجهولة", text)
        self.assertIn("إشعارات القراءة", text)
        self.assertIn("الحذف التلقائي", text)

    def test_default_all_green(self):
        text = self._text(1003)
        self.assertIn("🟢 الهمسات العامة", text)
        self.assertIn("🟢 الهمسات المجهولة", text)
        self.assertIn("🟢 إشعارات القراءة", text)
        self.assertIn("معطل", text)

    def test_red_when_disabled(self):
        update_group_setting(1004, "public_whispers_enabled", 0)
        update_group_setting(1004, "anonymous_enabled", 0)
        update_group_setting(1004, "read_notifications", 0)
        text = self._text(1004)
        self.assertIn("🔴 الهمسات العامة", text)
        self.assertIn("🔴 الهمسات المجهولة", text)
        self.assertIn("🔴 إشعارات القراءة", text)

    def test_auto_delete_shows_minutes(self):
        update_group_setting(1005, "auto_delete_minutes", 5)
        text = self._text(1005)
        self.assertIn("5 دقيقة", text)
        self.assertNotIn("معطل", text)


# ─────────────────────────────────────────────────────────────────────────────
# _settings_keyboard
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupSettingsKeyboard(unittest.TestCase):
    """_settings_keyboard structure."""

    def setUp(self):
        _boot()

    def _kb(self, chat_id):
        from handlers.group_settings import _settings_keyboard
        return _settings_keyboard(chat_id)

    def test_has_seven_rows(self):
        kb = self._kb(2001)
        self.assertEqual(len(kb.keyboard), 7)

    def test_has_back_button(self):
        kb = self._kb(2002)
        last_row = kb.keyboard[-1]
        btn = last_row[0]
        self.assertEqual(btn.callback_data, "admin:main")
        self.assertIn("رجوع", btn.text)

    def test_toggle_callbacks_present(self):
        kb = self._kb(2003)
        callbacks = []
        for row in kb.keyboard:
            for btn in row:
                callbacks.append(btn.callback_data)
        self.assertIn("group_toggle:public_whispers_enabled", callbacks)
        self.assertIn("group_toggle:anonymous_enabled", callbacks)
        self.assertIn("group_toggle:read_notifications", callbacks)
        self.assertIn("group_autodel_set:0", callbacks)
        self.assertIn("group_autodel_set:1", callbacks)
        self.assertIn("group_autodel_set:5", callbacks)
        self.assertIn("group_autodel_set:10", callbacks)
        self.assertIn("group_autodel_set:30", callbacks)
        self.assertIn("group_autodel_set:60", callbacks)

    def test_icons_reflect_db_state(self):
        update_group_setting(2004, "public_whispers_enabled", 0)
        update_group_setting(2004, "anonymous_enabled", 0)
        kb = self._kb(2004)
        for row in kb.keyboard:
            for btn in row:
                if "public_whispers_enabled" in btn.callback_data:
                    self.assertIn("🔴", btn.text)
                if "anonymous_enabled" in btn.callback_data:
                    self.assertIn("🔴", btn.text)

    def test_auto_delete_shows_minutes_when_enabled(self):
        update_group_setting(2005, "auto_delete_minutes", 10)
        kb = self._kb(2005)
        found_label = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "noop" and "دقيقة" in btn.text:
                    found_label = True
                    self.assertIn("10", btn.text)
        self.assertTrue(found_label, "auto-delete label with minutes not found")

    def test_auto_delete_shows_disabled_when_zero(self):
        update_group_setting(2006, "auto_delete_minutes", 0)
        kb = self._kb(2006)
        found_label = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "noop" and "معطل" in btn.text:
                    found_label = True
        self.assertTrue(found_label, "auto-delete disabled label not found")


# ─────────────────────────────────────────────────────────────────────────────
# Auto-delete preset buttons
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoDeletePresetButtons(unittest.TestCase):
    """Auto-delete preset button rendering and active value display."""

    def setUp(self):
        _boot()

    def _kb(self, chat_id):
        from handlers.group_settings import _settings_keyboard
        return _settings_keyboard(chat_id)

    def test_all_six_preset_buttons_present(self):
        kb = self._kb(5001)
        preset_callbacks = []
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data.startswith("group_autodel_set:"):
                    preset_callbacks.append(btn.callback_data)
        expected = [
            "group_autodel_set:0",
            "group_autodel_set:1",
            "group_autodel_set:5",
            "group_autodel_set:10",
            "group_autodel_set:30",
            "group_autodel_set:60",
        ]
        for cb in expected:
            self.assertIn(cb, preset_callbacks)

    def test_preset_buttons_in_two_rows_of_three(self):
        kb = self._kb(5002)
        preset_rows = []
        for row in kb.keyboard:
            cbs = [btn.callback_data for btn in row]
            if any(cb.startswith("group_autodel_set:") for cb in cbs):
                preset_rows.append(cbs)
        self.assertEqual(len(preset_rows), 2)
        self.assertEqual(len(preset_rows[0]), 3)
        self.assertEqual(len(preset_rows[1]), 3)

    def test_active_value_has_checkmark(self):
        update_group_setting(5003, "auto_delete_minutes", 5)
        kb = self._kb(5003)
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "group_autodel_set:5":
                    self.assertIn("✅", btn.text)
                elif btn.callback_data.startswith("group_autodel_set:"):
                    self.assertNotIn("✅", btn.text)

    def test_disabled_value_shows_checkmark_on_zero(self):
        update_group_setting(5004, "auto_delete_minutes", 0)
        kb = self._kb(5004)
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "group_autodel_set:0":
                    self.assertIn("✅", btn.text)
                elif btn.callback_data.startswith("group_autodel_set:"):
                    self.assertNotIn("✅", btn.text)

    def test_label_shows_current_minutes_when_enabled(self):
        update_group_setting(5005, "auto_delete_minutes", 30)
        kb = self._kb(5005)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "noop" and "30" in btn.text:
                    found = True
        self.assertTrue(found)

    def test_label_shows_disabled_when_zero(self):
        update_group_setting(5006, "auto_delete_minutes", 0)
        kb = self._kb(5006)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "noop" and "معطل" in btn.text:
                    found = True
        self.assertTrue(found)


# ─────────────────────────────────────────────────────────────────────────────
# Auto-delete preset handler
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoDeletePresetHandler(unittest.TestCase):
    """group_autodel_set: handler updates DB and refreshes message."""

    def setUp(self):
        _boot()

    def test_handler_updates_setting_to_zero(self):
        chat_id = 6001
        update_group_setting(chat_id, "auto_delete_minutes", 5)
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:0")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 0)

    def test_handler_updates_setting_to_one(self):
        chat_id = 6002
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:1")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 1)

    def test_handler_updates_setting_to_five(self):
        chat_id = 6003
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:5")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 5)

    def test_handler_updates_setting_to_ten(self):
        chat_id = 6004
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:10")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 10)

    def test_handler_updates_setting_to_thirty(self):
        chat_id = 6005
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:30")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 30)

    def test_handler_updates_setting_to_sixty(self):
        chat_id = 6006
        _, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:60")
        handler(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 60)

    def test_handler_refreshes_message(self):
        chat_id = 6007
        bot, _, _, handler = _register_and_get_handlers()
        call = _make_call(chat_id, "group_autodel_set:10")
        handler(call)
        args, kwargs = bot.edit_message_text.call_args
        self.assertEqual(args[1], chat_id)
        self.assertIn("10 دقيقة", args[0])
        self.assertIsNotNone(kwargs.get("reply_markup"))

    def test_handler_persistence_after_re_read(self):
        chat_id = 6008
        _, _, _, handler = _register_and_get_handlers()
        handler(_make_call(chat_id, "group_autodel_set:30"))
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 30)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 30)

    def test_non_admin_cannot_set_preset(self):
        bot, _, _, handler = _register_and_get_handlers()
        call = _make_call(6009, "group_autodel_set:5", user_id=111)
        handler(call)
        assert bot.answer_callback_query.call_count >= 2
        args, kwargs = bot.answer_callback_query.call_args
        self.assertIn("غير مصرح", str(args))
        self.assertTrue(kwargs.get("show_alert", False))
        bot.edit_message_text.assert_not_called()


# ─────────────────────────────────────────────────────────────────────────────
# Database CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupSettingsDB(unittest.TestCase):
    """Database operations for group settings."""

    def setUp(self):
        _boot()

    def test_ensure_creates_defaults(self):
        settings = get_group_settings(9999)
        self.assertEqual(settings["public_whispers_enabled"], 1)
        self.assertEqual(settings["anonymous_enabled"], 1)
        self.assertEqual(settings["read_notifications"], 1)
        self.assertEqual(settings["auto_delete_minutes"], 0)

    def test_update_and_read(self):
        update_group_setting(42, "public_whispers_enabled", 0)
        settings = get_group_settings(42)
        self.assertEqual(settings["public_whispers_enabled"], 0)

    def test_update_multiple_fields(self):
        update_group_setting(77, "public_whispers_enabled", 0)
        update_group_setting(77, "anonymous_enabled", 0)
        update_group_setting(77, "read_notifications", 0)
        update_group_setting(77, "auto_delete_minutes", 15)
        settings = get_group_settings(77)
        self.assertEqual(settings["public_whispers_enabled"], 0)
        self.assertEqual(settings["anonymous_enabled"], 0)
        self.assertEqual(settings["read_notifications"], 0)
        self.assertEqual(settings["auto_delete_minutes"], 15)

    def test_update_invalid_key_raises(self):
        with self.assertRaises(ValueError):
            update_group_setting(1, "invalid_key", 1)

    def test_idempotent_ensure(self):
        ensure_group_settings(555)
        ensure_group_settings(555)
        settings = get_group_settings(555)
        self.assertEqual(settings["public_whispers_enabled"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# Toggle logic (handler integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupSettingsToggle(unittest.TestCase):
    """Toggle logic for all four settings via the registered handler."""

    def setUp(self):
        _boot()

    def test_toggle_public_whispers(self):
        chat_id = 3001
        _, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:public_whispers_enabled")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["public_whispers_enabled"], 0)
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["public_whispers_enabled"], 1)

    def test_toggle_anonymous(self):
        chat_id = 3002
        _, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:anonymous_enabled")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["anonymous_enabled"], 0)
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["anonymous_enabled"], 1)

    def test_toggle_read_notifications(self):
        chat_id = 3003
        _, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:read_notifications")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["read_notifications"], 0)
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["read_notifications"], 1)

    def test_toggle_auto_delete(self):
        chat_id = 3004
        _, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:auto_delete_minutes")
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 5)
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["auto_delete_minutes"], 0)

    def test_toggle_updates_message(self):
        chat_id = 3005
        bot, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:public_whispers_enabled")
        toggle(call)
        args, kwargs = bot.edit_message_text.call_args
        self.assertEqual(args[1], chat_id)
        self.assertEqual(args[2], 100)
        self.assertIn("🔴", args[0])
        self.assertIsNotNone(kwargs.get("reply_markup"))

    def test_persistence_after_re_read(self):
        chat_id = 3006
        _, _, toggle, _ = _register_and_get_handlers()
        toggle(_make_call(chat_id, "group_toggle:public_whispers_enabled"))
        self.assertEqual(get_group_settings(chat_id)["public_whispers_enabled"], 0)
        self.assertEqual(get_group_settings(chat_id)["public_whispers_enabled"], 0)

        toggle(_make_call(chat_id, "group_toggle:anonymous_enabled"))
        toggle(_make_call(chat_id, "group_toggle:read_notifications"))
        toggle(_make_call(chat_id, "group_toggle:auto_delete_minutes"))

        final = get_group_settings(chat_id)
        self.assertEqual(final["public_whispers_enabled"], 0)
        self.assertEqual(final["anonymous_enabled"], 0)
        self.assertEqual(final["read_notifications"], 0)
        self.assertEqual(final["auto_delete_minutes"], 5)


# ─────────────────────────────────────────────────────────────────────────────
# Admin guard
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupSettingsAdminGuard(unittest.TestCase):
    """Non-admin users are blocked from opening / toggling settings."""

    def setUp(self):
        _boot()

    def test_non_admin_cannot_open_panel(self):
        bot, show, _, _ = _register_and_get_handlers()
        call = _make_call(4001, "admin:group_settings", user_id=111)
        show(call)
        assert bot.answer_callback_query.call_count >= 2
        args, kwargs = bot.answer_callback_query.call_args
        self.assertIn("غير مصرح", str(args))
        self.assertTrue(kwargs.get("show_alert", False))
        bot.edit_message_text.assert_not_called()

    def test_non_admin_cannot_toggle(self):
        bot, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(4002, "group_toggle:public_whispers_enabled", user_id=111)
        toggle(call)
        assert bot.answer_callback_query.call_count >= 2
        args, kwargs = bot.answer_callback_query.call_args
        self.assertIn("غير مصرح", str(args))
        self.assertTrue(kwargs.get("show_alert", False))
        bot.edit_message_text.assert_not_called()

    def test_admin_can_open_panel(self):
        chat_id = 4003
        bot, show, _, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "admin:group_settings", user_id=999)
        show(call)
        bot.edit_message_text.assert_called_once()
        args, _ = bot.edit_message_text.call_args
        self.assertIn("إعدادات الهمسات", args[0])

    def test_admin_can_toggle_and_updates_db(self):
        chat_id = 4004
        bot, _, toggle, _ = _register_and_get_handlers()
        call = _make_call(chat_id, "group_toggle:public_whispers_enabled", user_id=999)
        toggle(call)
        self.assertEqual(get_group_settings(chat_id)["public_whispers_enabled"], 0)

    def test_guardian_imported_correctly(self):
        """_guard_admin from admin.py works with our handlers."""
        from handlers.admin import is_admin
        self.assertTrue(is_admin(999))
        self.assertFalse(is_admin(111))


# ─────────────────────────────────────────────────────────────────────────────
# Admin main keyboard
# ─────────────────────────────────────────────────────────────────────────────

class TestAdminMainKeyboardHasGroupButton(unittest.TestCase):
    """The admin main keyboard includes the group settings button."""

    def setUp(self):
        _boot()

    def test_keyboard_has_group_settings_button(self):
        from handlers.admin import admin_main_keyboard
        kb = admin_main_keyboard()
        found = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data == "admin:group_settings":
                    found = True
                    self.assertIn("الهمسات", btn.text)
        self.assertTrue(
            found,
            "admin_main_keyboard should have 'admin:group_settings' button",
        )

    def test_existing_buttons_unchanged(self):
        from handlers.admin import admin_main_keyboard
        kb = admin_main_keyboard()
        callbacks = set()
        for row in kb.keyboard:
            for btn in row:
                callbacks.add(btn.callback_data)
        self.assertIn("admin:stats", callbacks)
        self.assertIn("admin:broadcast", callbacks)
        self.assertIn("admin:users:0", callbacks)
        self.assertIn("admin:settings", callbacks)
        self.assertIn("admin:channels", callbacks)
        self.assertIn("admin:reports", callbacks)
        self.assertIn("admin:backups", callbacks)
        self.assertIn("admin:enterprise_stats", callbacks)
        self.assertIn("admin:group_settings", callbacks)


if __name__ == "__main__":
    unittest.main(verbosity=2)
