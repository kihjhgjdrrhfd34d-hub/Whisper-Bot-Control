"""
tests/test_unlock_at_condition_flow.py — Regression tests for the
"unlock after time" condition (UnlockAtCondition), driven through the REAL
registered handlers (bot.py + handlers/whisper.py + handlers/conditional_whisper.py)
and the condition module itself.

Covers:
  1. The condition is auto-discovered in the registry.
  2. Before the unlock time the whisper stays locked and shows the remaining time.
  3. After the unlock time the whisper opens and the reader is recorded.
  4. The creation wizard stores the unlock timestamp inside conditions_data.
  5. Custom dates are validated and past times are rejected.
"""
import datetime
import json
import os
import sys
import time
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_unlock_at_cond.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.whisper_conditions import init_whisper_conditions_db

import bot as botmod
bot = botmod.bot
user_states = botmod.user_states

SENDER_ID = 20001
READER_ID = 20002


def _dispatch_message(msg):
    from telebot import ContinueHandling
    for handler in bot.message_handlers:
        if bot._test_message_handler(handler, msg):
            result = handler['function'](msg)
            if not isinstance(result, ContinueHandling):
                return handler['function']
    return None


def _dispatch_callback(call):
    for handler in bot.callback_query_handlers:
        if bot._test_message_handler(handler, call):
            handler['function'](call)
            return handler['function']
    return None


def _make_user(user_id, username, first_name):
    u = MagicMock()
    u.id = user_id
    u.username = username
    u.first_name = first_name
    u.last_name = None
    return u


def _make_message(user_id, text):
    msg = MagicMock()
    msg.content_type = "text"
    msg.text = text
    msg.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    msg.chat = MagicMock()
    msg.chat.id = user_id
    msg.chat.type = "private"
    msg.message_id = 3000 + user_id
    msg.reply_to_message = None
    return msg


def _make_callback(user_id, data):
    call = MagicMock()
    call.id = f"cb_{data}"
    call.data = data
    call.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    call.message = MagicMock()
    call.message.chat.id = user_id
    call.message.message_id = 4000 + user_id
    call.inline_message_id = None
    return call


def _future_timestamp(seconds_ahead=600):
    return int(time.time()) + seconds_ahead


def _past_timestamp():
    return int(time.time()) - 60


class TestUnlockAtConditionModule(unittest.TestCase):
    """Unit tests for the condition module itself (independent of handlers)."""

    def test_registry_has_unlock_at(self):
        from conditions import registry
        self.assertIn("unlock_at", set(registry.all().keys()))

    def test_format_remaining(self):
        from conditions.unlock_at import format_remaining
        self.assertEqual(format_remaining(0), "00:00:00")
        self.assertEqual(format_remaining(1), "00:00:01")
        self.assertEqual(format_remaining(60), "00:01:00")
        self.assertEqual(format_remaining(3600 + 15 * 60 + 48), "01:15:48")
        self.assertEqual(format_remaining(90000), "1 يوم، 01:00:00")

    def test_resolve_timestamp_from_dict(self):
        from conditions.unlock_at import resolve_timestamp
        self.assertEqual(resolve_timestamp({"unlock_at": {"timestamp": 1786000000}}), 1786000000)
        self.assertEqual(resolve_timestamp({"timestamp": 1786000000}), 1786000000)
        self.assertEqual(resolve_timestamp({"unlock_at": {"iso": "2026-08-20 15:30"}}),
                         int(datetime.datetime(2026, 8, 20, 15, 30).timestamp()))
        self.assertIsNone(resolve_timestamp({"unlock_at": {}}))
        self.assertIsNone(resolve_timestamp({}))

    def test_parse_custom_datetime(self):
        from conditions.unlock_at import parse_custom_datetime
        expected = int(datetime.datetime(2026, 8, 20, 15, 30).timestamp())
        self.assertEqual(parse_custom_datetime("2026-08-20 15:30"), expected)
        self.assertEqual(parse_custom_datetime("20/08/2026 15:30"), expected)
        self.assertEqual(parse_custom_datetime("20-08-2026 15:30"), expected)
        self.assertEqual(parse_custom_datetime("2026-08-20 15:30:00"), expected)
        self.assertIsNone(parse_custom_datetime("not a date"))
        self.assertIsNone(parse_custom_datetime(""))

    def test_check_future_timestamp_fails_with_remaining(self):
        from conditions.unlock_at import UnlockAtCondition
        cond = UnlockAtCondition()
        config = {"unlock_at": {"timestamp": _future_timestamp(seconds_ahead=90)}}
        result = cond.check({"whisper_id": "w1"}, READER_ID, config)
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "not_yet")
        self.assertFalse(result.requires_interaction)
        self.assertIn("لم يحن وقت فتح هذه الهمسة بعد", result.message)
        self.assertIn("الوقت المتبقي", result.message)
        self.assertRegex(result.message, r"\d{2}:\d{2}:\d{2}")

    def test_check_past_timestamp_passes(self):
        from conditions.unlock_at import UnlockAtCondition
        cond = UnlockAtCondition()
        config = {"unlock_at": {"timestamp": _past_timestamp()}}
        result = cond.check({"whisper_id": "w1"}, READER_ID, config)
        self.assertTrue(result.passed)
        self.assertEqual(result.reason, "unlock_time_reached")

    def test_check_invalid_config_fails(self):
        from conditions.unlock_at import UnlockAtCondition
        cond = UnlockAtCondition()
        result = cond.check({"whisper_id": "w1"}, READER_ID, {})
        self.assertFalse(result.passed)
        self.assertEqual(result.reason, "invalid_config")


class TestUnlockAtConditionReadFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_whisper_conditions_db()
        bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        bot.answer_callback_query = MagicMock()
        bot.reply_to = MagicMock(return_value=MagicMock(message_id=2, chat=None))
        bot.edit_message_reply_markup = MagicMock()
        bot.delete_message = MagicMock()
        bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        bot.get_chat_member = MagicMock(return_value=MagicMock(status="member"))
        if not any(getattr(h['function'], '__name__', '') == 'cwhisper_start'
                   for h in bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        user_states.clear()
        bot.send_message.reset_mock()
        bot.answer_callback_query.reset_mock()
        bot.reply_to.reset_mock()
        bot.edit_message_reply_markup.reset_mock()
        db.upsert_user(SENDER_ID, "sender", "Sender", None)
        db.upsert_user(READER_ID, "reader", "Reader", None)

    def _create_unlock_whisper(self, content="UNLOCK SECRET", timestamp=None):
        if timestamp is None:
            timestamp = _future_timestamp()
        return db.create_whisper(
            sender_id=SENDER_ID,
            content=content,
            whisper_type="everyone",
            conditions_data={"unlock_at": {"timestamp": timestamp}},
        )

    def _start_view(self, wid):
        msg = _make_message(READER_ID, f"/start view_{wid}")
        _dispatch_message(msg)
        return msg

    def _read_callback(self, wid):
        call = _make_callback(READER_ID, f"read:{wid}")
        _dispatch_callback(call)
        return call

    def _texts_sent(self):
        return [c.args[1] for c in bot.send_message.call_args_list
                if len(c.args) >= 2 and isinstance(c.args[1], str)]

    def _alerts_shown(self):
        return [c.args[1] for c in bot.answer_callback_query.call_args_list
                if len(c.args) >= 2 and isinstance(c.args[1], str)]

    def test_not_yet_time_blocks_read_via_start(self):
        wid = self._create_unlock_whisper()
        self._start_view(wid)
        from database import reader_count
        self.assertEqual(reader_count(wid), 0, "no read before the unlock time")
        texts = self._texts_sent()
        self.assertTrue(any("لم يحن وقت فتح هذه الهمسة بعد" in t for t in texts),
                        "the not-yet message must be shown")
        self.assertTrue(any("الوقت المتبقي" in t for t in texts),
                        "the remaining time must be shown")
        self.assertTrue(any("UNLOCK SECRET" in t for t in texts) is False,
                        "content must NOT be delivered before the unlock time")

    def test_not_yet_time_blocks_read_via_callback(self):
        wid = self._create_unlock_whisper()
        self._read_callback(wid)
        from database import reader_count
        self.assertEqual(reader_count(wid), 0)
        alerts = self._alerts_shown()
        self.assertTrue(any("لم يحن وقت فتح هذه الهمسة بعد" in t for t in alerts),
                        "the not-yet message must be shown as a callback alert")

    def test_time_reached_delivers_content(self):
        wid = self._create_unlock_whisper(timestamp=_past_timestamp())
        self._start_view(wid)
        from database import get_readers
        readers = [r["user_id"] for r in get_readers(wid)]
        self.assertIn(READER_ID, readers, "reader must be recorded after the unlock time")
        self.assertTrue(any("UNLOCK SECRET" in t for t in self._texts_sent()),
                        "content must be delivered after the unlock time")

    def test_time_reached_via_callback_delivers_content(self):
        wid = self._create_unlock_whisper(timestamp=_past_timestamp())
        self._read_callback(wid)
        from database import get_readers
        self.assertIn(READER_ID, [r["user_id"] for r in get_readers(wid)])
        self.assertTrue(any("UNLOCK SECRET" in t for t in self._alerts_shown()),
                        "content must be delivered via callback alert")


class TestUnlockAtConditionWizard(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_whisper_conditions_db()
        from database.envelope import init_envelope_db
        init_envelope_db()
        bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        bot.answer_callback_query = MagicMock()
        bot.reply_to = MagicMock(return_value=MagicMock(message_id=2, chat=None))
        bot.edit_message_reply_markup = MagicMock()
        bot.delete_message = MagicMock()
        bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        bot.get_chat_member = MagicMock(return_value=MagicMock(status="member"))
        if not any(getattr(h['function'], '__name__', '') == 'cwhisper_start'
                   for h in bot.callback_query_handlers):
            botmod.register_all_handlers()

    def setUp(self):
        user_states.clear()
        bot.send_message.reset_mock()
        bot.answer_callback_query.reset_mock()
        bot.reply_to.reset_mock()
        db.upsert_user(SENDER_ID, "sender", "Sender", None)

    def _texts_sent(self):
        return [c.args[1] for c in bot.send_message.call_args_list
                if len(c.args) >= 2 and isinstance(c.args[1], str)]

    def _button_labels_sent(self):
        labels = []
        for c in bot.send_message.call_args_list:
            kb = (c.kwargs or {}).get("reply_markup")
            if kb and hasattr(kb, "keyboard"):
                for row in kb.keyboard:
                    for btn in row:
                        if getattr(btn, "text", None):
                            labels.append(btn.text)
        return labels

    def test_wizard_option_listed(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        self.assertEqual(user_states.get(SENDER_ID), {"action": "cw_awaiting_condition_type"})
        self.assertTrue(any("فتح بعد وقت" in label for label in self._button_labels_sent()),
                        "the unlock_at option must be shown in the type-selection menu")

    def test_wizard_preset_duration_stores_timestamp(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:unlock_at"))
        state = user_states.get(SENDER_ID)
        self.assertEqual(state.get("action"), "cw_awaiting_unlock_duration")
        labels = self._button_labels_sent()
        for label in ("بعد 5 دقائق", "بعد 30 دقيقة", "بعد ساعة", "بعد 6 ساعات",
                      "بعد 12 ساعة", "بعد 24 ساعة", "موعد مخصص"):
            self.assertTrue(any(label in l for l in labels), f"duration '{label}' must be shown")

        _dispatch_callback(_make_callback(SENDER_ID, "cw_unlock:5min"))
        state = user_states.get(SENDER_ID)
        self.assertEqual(state["action"], "cw_awaiting_content")
        unlock = state["conditions_data"]["unlock_at"]
        self.assertIn("timestamp", unlock)
        now = int(time.time())
        self.assertTrue(now + 5 * 60 - 5 <= unlock["timestamp"] <= now + 5 * 60 + 5,
                        "stored timestamp must be ~5 minutes in the future")

    def test_wizard_preset_stored_in_draft(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:unlock_at"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_unlock:24h"))
        _dispatch_message(_make_message(SENDER_ID, "همسة تُفتح بعد يوم"))
        self.assertIsNone(user_states.get(SENDER_ID), "state must be cleared after save")
        from database.envelope import get_draft
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        conds = json.loads(draft["conditions_data"])
        self.assertIn("unlock_at", conds)
        self.assertIn("timestamp", conds["unlock_at"])
        now = int(time.time())
        self.assertTrue(now + 24 * 3600 - 5 <= conds["unlock_at"]["timestamp"] <= now + 24 * 3600 + 5)

    def test_wizard_custom_datetime_valid(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:unlock_at"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_unlock:custom"))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_unlock_custom")

        future = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        _dispatch_message(_make_message(SENDER_ID, future))
        state = user_states.get(SENDER_ID)
        self.assertEqual(state["action"], "cw_awaiting_content")
        unlock = state["conditions_data"]["unlock_at"]
        expected = int(datetime.datetime.strptime(future, "%Y-%m-%d %H:%M").timestamp())
        self.assertEqual(unlock["timestamp"], expected)
        self.assertTrue(any("تم تحديد وقت الفتح" in t for t in self._texts_sent()))

    def test_wizard_custom_invalid_rejected(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:unlock_at"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_unlock:custom"))

        _dispatch_message(_make_message(SENDER_ID, "ليس تاريخاً صحيحاً"))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_unlock_custom",
                         "invalid date must keep the custom-input state")
        self.assertTrue(any("صيغة التاريخ غير صحيحة" in t for t in self._texts_sent()))

    def test_wizard_custom_past_rejected(self):
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:unlock_at"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_unlock:custom"))

        past = (datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M")
        _dispatch_message(_make_message(SENDER_ID, past))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_unlock_custom",
                         "a past date must keep the custom-input state")
        self.assertTrue(any("وقت في الماضي" in t for t in self._texts_sent()))

        future = (datetime.datetime.now() + datetime.timedelta(days=2)).strftime("%Y-%m-%d %H:%M")
        _dispatch_message(_make_message(SENDER_ID, future))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_content",
                         "a valid future date must advance the wizard")


if __name__ == "__main__":
    unittest.main(verbosity=2)
