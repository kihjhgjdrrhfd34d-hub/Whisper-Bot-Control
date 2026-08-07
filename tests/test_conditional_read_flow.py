"""
tests/test_conditional_read_flow.py — Regression tests for the conditional-whisper
read flow, driven through the REAL registered handlers (bot.py + handlers/whisper.py).

Guards the three confirmed regressions:
  1. The condition registry must be auto-populated at import time.
  2. A password message must be routed to the condition engine (not swallowed by
     the generic handle_messages fall-through).
  3. sqlite3.Row returned by get_whisper must not break content delivery.

Flow under test:
  create conditional whisper -> /start view_<wid> -> read:<wid> callback
  -> password request (state=cond_answer) -> wrong password keeps state
  -> correct password -> content delivered + reader recorded.
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_cond_read_flow.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.whisper_conditions import init_whisper_conditions_db, get_condition_attempts

import bot as botmod
bot = botmod.bot
user_states = botmod.user_states

SENDER_ID = 10001
READER_ID = 10002


def _dispatch_message(msg):
    """First-matching message handler wins — mirrors telebot's break-on-match."""
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
    msg.message_id = 1000 + user_id
    msg.reply_to_message = None
    return msg


def _make_callback(user_id, data):
    call = MagicMock()
    call.id = f"cb_{data}"
    call.data = data
    call.from_user = _make_user(user_id, f"user{user_id}", f"User{user_id}")
    call.message = MagicMock()
    call.message.chat.id = user_id
    call.message.message_id = 2000 + user_id
    call.inline_message_id = None
    return call


class TestConditionalReadFlow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        db.init_db()
        init_whisper_conditions_db()
        # Stub every network-bound call on the real bot instance.
        bot.send_message = MagicMock(return_value=MagicMock(message_id=1, chat=None))
        bot.answer_callback_query = MagicMock()
        bot.reply_to = MagicMock(return_value=MagicMock(message_id=2, chat=None))
        bot.edit_message_reply_markup = MagicMock()
        bot.delete_message = MagicMock()
        bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
        bot.get_chat_member = MagicMock(return_value=MagicMock(status="member"))
        if not any(getattr(h['function'], '__name__', '') == 'handle_read'
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

    def _create_conditional_whisper(self, content="SECRET CONTENT", password="secret"):
        from handlers.conditional_whisper import _hash_password
        return db.create_whisper(
            sender_id=SENDER_ID,
            content=content,
            whisper_type="everyone",
            conditions_data={"password": _hash_password(password)},
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

    def test_registry_is_initialized(self):
        """The condition registry must be auto-populated at import time."""
        from conditions import registry
        names = set(registry.all().keys())
        self.assertIn("password", names)
        self.assertIn("question", names)
        self.assertIn("time_window", names)
        self.assertIn("channel_member", names)

    def test_full_flow_correct_password(self):
        """Create -> view -> read -> password prompt -> correct password ->
        content delivered + reader recorded."""
        wid = self._create_conditional_whisper()
        w = dict(db.get_whisper(wid))
        self.assertEqual(w["whisper_type"], "everyone")
        self.assertTrue(w["conditions_data"])

        self._start_view(wid)
        # Card with the read button is sent.
        self.assertTrue(any("همسة" in t for t in self._texts_sent()),
                        "/start view_ must send the whisper card")

        self._read_callback(wid)
        # Password prompt requested; reader enters cond_answer state.
        self.assertEqual(user_states.get(READER_ID),
                         {"action": "cond_answer", "whisper_id": wid,
                          "condition_type": "password"})
        self.assertTrue(
            any("password" in t or "كلمة سر" in t for t in self._texts_sent()),
            "a password/interaction prompt must be shown",
        )

        _dispatch_message(_make_message(READER_ID, "secret"))
        # State cleared; the reader then presses the reveal button so the
        # content is delivered as a callback alert (same as normal whispers).
        self.assertIsNone(user_states.get(READER_ID), "state must be popped")
        self._read_callback(wid)
        from database import get_readers
        readers = [r["user_id"] for r in get_readers(wid)]
        self.assertIn(READER_ID, readers, "reader must be recorded")
        self.assertTrue(any("SECRET CONTENT" in t for t in self._alerts_shown()),
                        "whisper content must be delivered as a callback alert")

    def test_wrong_password_keeps_state_and_records_attempt(self):
        """Wrong password must not unlock the whisper, must keep the state,
        and must record a failed attempt."""
        wid = self._create_conditional_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        self.assertEqual(user_states.get(READER_ID, {}).get("action"), "cond_answer")

        _dispatch_message(_make_message(READER_ID, "wrongpass"))

        self.assertEqual(user_states.get(READER_ID, {}).get("action"), "cond_answer",
                         "state must persist after a wrong password")
        from database import get_readers, reader_count
        self.assertEqual(reader_count(wid), 0, "no read on wrong password")
        self.assertEqual(get_readers(wid), [])
        attempts = get_condition_attempts(wid, READER_ID)
        self.assertTrue(any(not a["passed"] for a in attempts),
                        "failed attempt must be recorded")

        # After the correct password the whisper unlocks via the reveal button.
        _dispatch_message(_make_message(READER_ID, "secret"))
        self.assertIsNone(user_states.get(READER_ID))
        self._read_callback(wid)
        self.assertEqual(reader_count(wid), 1)
        self.assertTrue(any("SECRET CONTENT" in t for t in self._alerts_shown()))

    def test_sqlite_row_content_delivery(self):
        """A plain whisper read via callback passes a sqlite3.Row into
        _complete_read_flow; content must still be delivered."""
        wid = db.create_whisper(SENDER_ID, "PLAIN CONTENT", "everyone")
        raw = db.get_whisper(wid)
        self.assertIsNotNone(raw)
        self.assertFalse(isinstance(raw, dict),
                         "get_whisper must return a row (guards the Row regression)")

        call = self._make_and_dispatch_read(wid)

        # Content delivered via the callback alert (call context).
        alerts = [c.args[1] for c in bot.answer_callback_query.call_args_list
                  if len(c.args) >= 2 and isinstance(c.args[1], str)]
        self.assertTrue(any("PLAIN CONTENT" in t for t in alerts),
                        "content must be delivered through _answer_with_content")
        from database import get_readers
        self.assertIn(READER_ID, [r["user_id"] for r in get_readers(wid)])

    def _make_and_dispatch_read(self, wid):
        call = _make_callback(READER_ID, f"read:{wid}")
        _dispatch_callback(call)
        return call


if __name__ == "__main__":
    unittest.main(verbosity=2)
