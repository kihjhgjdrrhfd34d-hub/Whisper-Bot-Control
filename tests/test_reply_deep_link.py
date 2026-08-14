"""
tests/test_reply_deep_link.py — Regression: the /start reply_<id> deep link
(the URL reply button used by delivered replies) must stay usable after a
whisper is closed/locked.

Closing a whisper stops new *reads* only.  An authorised participant (the
sender or an existing reader) keeps the ability to reply through the deep
link, while non-participants stay blocked in every state.
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_reply_deep_link_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
import bot as bot_module

SENDER = 71001
READER = 71002
STRANGER = 71003


def _boot():
    db.init_db()
    db.upsert_user(SENDER, "dl_sender", "Sender", None)
    db.upsert_user(READER, "dl_reader", "Reader", None)
    db.upsert_user(STRANGER, "dl_stranger", "Stranger", None)


class TestReplyDeepLinkAfterClose(unittest.TestCase):
    """/start reply_<id> respects the reply authorisation, not the read gate."""

    def setUp(self):
        _boot()
        self.wid = db.create_whisper(SENDER, "deep link reply", "everyone")
        db.add_reader(self.wid, READER)
        self._sm = patch.object(
            bot_module.bot, "send_message", return_value=MagicMock()
        )
        self._sm.start()
        self.addCleanup(self._sm.stop)

    def tearDown(self):
        for uid in (SENDER, READER, STRANGER):
            bot_module.user_states.pop(uid, None)

    def _msg(self, text, user_id):
        msg = MagicMock()
        msg.text = text
        msg.chat.id = user_id
        fu = MagicMock()
        fu.id = user_id
        fu.username = f"user_{user_id}"
        fu.first_name = f"U{user_id}"
        fu.last_name = None
        msg.from_user = fu
        return msg

    def _press_reply_link(self, user_id):
        bot_module.start_cmd(self._msg(f"/start reply_{self.wid}", user_id))
        return bot_module.user_states.get(user_id)

    def _sent_texts(self):
        return [
            call.args[1]
            for call in bot_module.bot.send_message.call_args_list
            if len(call.args) >= 2 and isinstance(call.args[1], str)
        ]

    def test_deep_link_reply_works_before_close(self):
        state = self._press_reply_link(READER)
        self.assertIsNotNone(state)
        self.assertEqual(state["action"], "pending_whisper_reply")
        self.assertEqual(state["whisper_id"], self.wid)
        self.assertTrue(
            any("أنت ترد الآن على الهمسة" in t for t in self._sent_texts()),
            "reply prompt must be shown before the whisper is closed",
        )

    def test_deep_link_reply_works_after_close(self):
        db.close_whisper(self.wid)
        w = dict(db.get_whisper(self.wid))
        self.assertEqual(w.get("is_closed"), 1)
        self.assertEqual(w.get("is_locked"), 1)
        state = self._press_reply_link(READER)
        self.assertIsNotNone(state, "reader must keep replying after close")
        self.assertEqual(state["action"], "pending_whisper_reply")
        self.assertFalse(
            any("لا يمكنك الرد" in t for t in self._sent_texts()),
            "closing the whisper must not block the reply deep link",
        )

    def test_deep_link_sender_keeps_replying_after_lock(self):
        db.toggle_whisper_lock(self.wid)
        state = self._press_reply_link(SENDER)
        self.assertIsNotNone(state, "sender must keep replying after lock")
        self.assertEqual(state["whisper_id"], self.wid)

    def test_deep_link_after_reader_limit_reached(self):
        db.upsert_user(71004, "dl_r3", "R3", None)
        wid3 = db.create_whisper(SENDER, "dl first_three", "first_three",
                                 max_readers=3)
        db.record_whisper_read(wid3, READER)
        db.record_whisper_read(wid3, STRANGER)
        db.record_whisper_read(wid3, 71004)  # third reader -> auto-lock
        self.assertEqual(dict(db.get_whisper(wid3))["is_locked"], 1)
        # Simulate pressing the deep link for wid3.
        bot_module.start_cmd(self._msg(f"/start reply_{wid3}", READER))
        state = bot_module.user_states.get(READER)
        self.assertIsNotNone(state)
        self.assertEqual(state["whisper_id"], wid3)

    def test_deep_link_non_participant_still_blocked_after_close(self):
        db.close_whisper(self.wid)
        state = self._press_reply_link(STRANGER)
        self.assertIsNone(state, "non-participant stays blocked after close")
        self.assertTrue(
            any("لا يمكنك الرد" in t for t in self._sent_texts()),
            "non-participant must receive the block message",
        )

    def test_deep_link_missing_whisper_still_blocked(self):
        bot_module.start_cmd(self._msg("/start reply_no_such_wid", READER))
        self.assertTrue(
            any("الهمسة غير موجودة" in t for t in self._sent_texts()),
            "missing whisper must still be rejected",
        )