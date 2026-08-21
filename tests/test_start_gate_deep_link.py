"""
tests/test_start_gate_deep_link.py — Start-gate deep-link read flow.

Covers:
  1. A brand-new user (no DB record) pressing '🔒 اضغط للرؤية' is redirected
     to the bot's private chat via a t.me deep link and is NOT recorded as
     a reader and receives no whisper content.
  2. '/start whisper_<id>' completes the open through
     handle_whisper_start_deep_link: read recorded, content delivered.
  3. Registered users (even started=0) keep the normal in-place read flow —
     no redirect, content via callback alert.
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_start_gate_dl_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, upsert_user, mark_user_started,
    reader_count, get_readers, set_setting,
)

NEW_USER_ID = 79999


def _boot():
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers")
        conn.execute("DELETE FROM curious_ones")
        conn.execute("DELETE FROM whispers")
        conn.execute("DELETE FROM users WHERE user_id = ?", (NEW_USER_ID,))
        conn.commit()
    upsert_user(70001, "sender_sgd", "Sender", None)
    upsert_user(70002, "known_sgd", "Known", None)
    mark_user_started(70002)
    # 70003: registered but never completed /start (started=0)
    upsert_user(70003, "stale_sgd", "Stale", None)
    set_setting("bot_active", "1")


def _dispatch_read_callback(bot_module, user_id, wid):
    """Simulate pressing the '🔒 اضغط للرؤية' button (read:<wid>)."""
    if not any(getattr(h["function"], "__name__", "") == "handle_read"
               for h in bot_module.bot.callback_query_handlers):
        bot_module.register_all_handlers()

    call = MagicMock()
    call.id = f"cb_{wid}_{user_id}"
    call.data = f"read:{wid}"
    call.from_user = MagicMock()
    call.from_user.id = user_id
    call.from_user.username = f"user{user_id}"
    call.from_user.first_name = "Tester"
    call.from_user.last_name = None
    call.message = MagicMock()
    call.message.chat.id = -1001234
    call.message.message_id = 2000 + user_id
    call.inline_message_id = None

    for handler in bot_module.bot.callback_query_handlers:
        if bot_module.bot._test_message_handler(handler, call):
            handler["function"](call)
            return call
    raise AssertionError(f"No callback handler matched read:{wid}")


def _capture_bot_io(bot_module):
    """Replace send_message/answer_callback_query/get_me with capturers."""
    sent, alerts = [], []

    def capture_send(chat_id, text=None, **kwargs):
        sent.append({"chat_id": chat_id, "text": text or "", "kwargs": kwargs})
        return MagicMock()

    def capture_answer(callback_id, text="", **kwargs):
        alerts.append({"callback_id": callback_id, "text": text or "",
                       "kwargs": kwargs})
        return True

    originals = (
        bot_module.bot.send_message,
        bot_module.bot.answer_callback_query,
        bot_module.bot.get_me,
    )
    bot_module.bot.send_message = capture_send
    bot_module.bot.answer_callback_query = capture_answer
    bot_module.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))
    return sent, alerts, originals


class TestNewUserRedirectedToPrivateDeepLink(unittest.TestCase):
    """Requirement: new users pressing the read button get a deep link to
    the bot's private chat and are never recorded as readers."""

    def setUp(self):
        _boot()

    def test_new_user_gets_deep_link_and_not_recorded(self):
        import bot as bot_module
        wid = create_whisper(70001, "sgd secret content xyz", "everyone")

        sent, alerts, originals = _capture_bot_io(bot_module)
        try:
            _dispatch_read_callback(bot_module, NEW_USER_ID, wid)
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = originals

        # Not recorded as reader / curious
        self.assertEqual(reader_count(wid), 0,
                         "start gate must not record the new user as reader")
        self.assertEqual(get_readers(wid), [])

        # Callback acked WITHOUT any whisper content in the popup
        self.assertEqual(len(alerts), 1, f"expected one ack alert, got {alerts}")
        self.assertFalse(alerts[0]["kwargs"].get("show_alert"),
                         "redirect ack must not be a content popup")
        self.assertNotIn("sgd secret content xyz", alerts[0]["text"])

        # A deep-link button to private chat is posted
        dl_messages = [m for m in sent
                       if m["kwargs"].get("reply_markup") is not None]
        self.assertEqual(len(dl_messages), 1,
                         f"expected one deep-link message, got {sent}")
        kb = dl_messages[0]["kwargs"]["reply_markup"]
        urls = [btn.url for row in kb.keyboard for btn in row if btn.url]
        expected = f"https://t.me/testbot?start=whisper_{wid}"
        self.assertIn(expected, urls,
                      f"deep link {expected} missing, got {urls}")

        # No whisper content anywhere in the group/private messages
        all_text = " ".join(m["text"] for m in sent)
        self.assertNotIn("sgd secret content xyz", all_text)

    def test_registered_started_user_reads_normally(self):
        import bot as bot_module
        wid = create_whisper(70001, "known user content", "everyone")

        sent, alerts, originals = _capture_bot_io(bot_module)
        try:
            _dispatch_read_callback(bot_module, 70002, wid)
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = originals

        # Read recorded normally, no redirect message posted
        self.assertEqual(reader_count(wid), 1)
        deep_link_msgs = [m for m in sent
                          if m["kwargs"].get("reply_markup") is not None
                          and "t.me" in str(m["kwargs"])]
        self.assertEqual(deep_link_msgs, [],
                         "registered user must not be redirected")

    def test_registered_but_never_started_user_passes_gate(self):
        """ce2d694 refinement: existence of a DB record before ensure_user
        means the user engaged the bot before → gate must NOT block."""
        import bot as bot_module
        wid = create_whisper(70001, "stale user content", "everyone")

        sent, alerts, originals = _capture_bot_io(bot_module)
        try:
            _dispatch_read_callback(bot_module, 70003, wid)
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = originals

        self.assertEqual(reader_count(wid), 1,
                         "registered (started=0) user must pass the gate")
        deep_link_msgs = [m for m in sent
                          if m["kwargs"].get("reply_markup") is not None
                          and "t.me" in str(m["kwargs"])]
        self.assertEqual(deep_link_msgs, [])


class TestStartDeepLinkOpensWhisper(unittest.TestCase):
    """'/start whisper_<id>' opens the whisper directly in private."""

    def setUp(self):
        _boot()

    def _make_start_msg(self, user_id, payload):
        msg = MagicMock()
        msg.text = f"/start {payload}"
        msg.from_user = MagicMock()
        msg.from_user.id = user_id
        msg.from_user.username = f"user{user_id}"
        msg.from_user.first_name = "Tester"
        msg.from_user.last_name = None
        msg.from_user.is_bot = False
        msg.chat = MagicMock()
        msg.chat.id = user_id
        msg.chat.type = "private"
        return msg

    def test_start_whisper_records_read_and_delivers_content(self):
        import bot as bot_module
        wid = create_whisper(70001, "dl open secret content", "everyone")

        sent, alerts, originals = _capture_bot_io(bot_module)
        try:
            bot_module.start_cmd(self._make_start_msg(NEW_USER_ID, f"whisper_{wid}"))
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = originals

        # The read IS recorded through the shared read flow
        self.assertEqual(reader_count(wid), 1,
                         "/start whisper_<id> must record the read")
        self.assertEqual(get_readers(wid)[0]["user_id"], NEW_USER_ID)

        # Content delivered to the user's private chat
        user_texts = " ".join(m["text"] for m in sent
                              if m["chat_id"] == NEW_USER_ID)
        self.assertIn("dl open secret content", user_texts)

    def test_start_whisper_respects_first_one_limit(self):
        import bot as bot_module
        wid = create_whisper(70001, "first one dl content", "first_one",
                             max_readers=1)

        # First reader takes the slot
        sent1, _, orig1 = _capture_bot_io(bot_module)
        try:
            bot_module.start_cmd(self._make_start_msg(71001, f"whisper_{wid}"))
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = orig1
        self.assertEqual(reader_count(wid), 1)

        # Second reader is denied — limit preserved through the deep link
        sent2, _, orig2 = _capture_bot_io(bot_module)
        try:
            bot_module.start_cmd(self._make_start_msg(72002, f"whisper_{wid}"))
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = orig2

        self.assertEqual(reader_count(wid), 1,
                         "first_one limit must hold via deep-link path")
        denied_texts = " ".join(m["text"] for m in sent2
                                if m["chat_id"] == 72002)
        self.assertIn("أول شخص", denied_texts,
                      "second user must get the taken_one denial")
        self.assertNotIn("first one dl content", denied_texts)

    def test_start_whisper_missing_id_friendly_error(self):
        import bot as bot_module
        sent, _, originals = _capture_bot_io(bot_module)
        try:
            bot_module.start_cmd(self._make_start_msg(NEW_USER_ID,
                                                      "whisper_doesnotexist"))
        finally:
            (bot_module.bot.send_message,
             bot_module.bot.answer_callback_query,
             bot_module.bot.get_me) = originals

        texts = " ".join(m["text"] for m in sent if m["chat_id"] == NEW_USER_ID)
        self.assertIn("غير متاحة", texts)


if __name__ == "__main__":
    unittest.main()
