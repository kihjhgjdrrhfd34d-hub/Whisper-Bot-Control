"""
tests/test_question_condition_flow.py — Regression tests for the
"question & answer" condition (QuestionCondition), driven through the REAL
registered handlers (bot.py + handlers/whisper.py + handlers/conditional_whisper.py).

Covers:
  1. Correct answer unlocks the whisper and records the reader.
  2. Wrong answer keeps the cond_answer state and records a failed attempt.
  3. Retry: after a wrong answer, the correct answer still unlocks.
  4. Opening the whisper after a correct answer delivers the content.
  5. The reader is recorded exactly once.
  6. The creation wizard stores the answer hashed inside conditions_data.

Flow under test:
  create conditional whisper -> /start view_<wid> -> read:<wid> callback
  -> question prompt (state=cond_answer) -> wrong answer keeps state
  -> correct answer -> content delivered + reader recorded.
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_question_cond.db")
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

SENDER_ID = 20001
READER_ID = 20002


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


def _question_condition(question="What color is the sky?", answer="blue"):
    from handlers.conditional_whisper import _hash_password
    cfg = _hash_password(answer)
    cfg["question"] = question
    return cfg


class TestQuestionConditionReadFlow(unittest.TestCase):

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

    def _create_question_whisper(self, content="QUESTION SECRET", question="What color is the sky?", answer="blue"):
        return db.create_whisper(
            sender_id=SENDER_ID,
            content=content,
            whisper_type="everyone",
            conditions_data={"question": _question_condition(question, answer)},
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

    def test_registry_has_question(self):
        from conditions import registry
        self.assertIn("question", set(registry.all().keys()))

    def test_correct_answer_unlocks_and_records_reader(self):
        wid = self._create_question_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        self.assertEqual(user_states.get(READER_ID),
                         {"action": "cond_answer", "whisper_id": wid,
                          "condition_type": "question"})
        self.assertTrue(any("What color is the sky?" in t for t in self._texts_sent()),
                        "the question prompt must be shown to the reader")

        _dispatch_message(_make_message(READER_ID, "blue"))
        self.assertIsNone(user_states.get(READER_ID), "state must be popped")
        from database import get_readers
        self.assertIn(READER_ID, [r["user_id"] for r in get_readers(wid)],
                      "reader must be recorded")
        self.assertTrue(any("QUESTION SECRET" in t for t in self._texts_sent()),
                        "whisper content must be delivered to the reader")

    def test_wrong_answer_keeps_state_and_records_attempt(self):
        wid = self._create_question_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        self.assertEqual(user_states.get(READER_ID, {}).get("action"), "cond_answer")

        _dispatch_message(_make_message(READER_ID, "wrong answer"))
        self.assertEqual(user_states.get(READER_ID, {}).get("action"), "cond_answer",
                         "state must persist after a wrong answer")
        from database import get_readers, reader_count
        self.assertEqual(reader_count(wid), 0, "no read on wrong answer")
        self.assertEqual(get_readers(wid), [])
        attempts = get_condition_attempts(wid, READER_ID)
        self.assertTrue(any(not a["passed"] for a in attempts),
                        "failed attempt must be recorded")

        # Correct answer afterwards unlocks the whisper.
        _dispatch_message(_make_message(READER_ID, "blue"))
        self.assertIsNone(user_states.get(READER_ID))
        self.assertEqual(reader_count(wid), 1)
        self.assertTrue(any("QUESTION SECRET" in t for t in self._texts_sent()))

    def test_retry_after_multiple_wrong_answers(self):
        wid = self._create_question_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        for _ in range(2):
            _dispatch_message(_make_message(READER_ID, "nope"))
            self.assertEqual(user_states.get(READER_ID, {}).get("action"), "cond_answer")

        _dispatch_message(_make_message(READER_ID, "blue"))
        self.assertIsNone(user_states.get(READER_ID), "retry must unlock after correct answer")
        from database import reader_count
        self.assertEqual(reader_count(wid), 1)

    def test_reader_recorded_once(self):
        wid = self._create_question_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        _dispatch_message(_make_message(READER_ID, "blue"))
        self.assertIsNone(user_states.get(READER_ID))
        from database import get_readers, reader_count
        self.assertEqual(reader_count(wid), 1)

        # Re-opening afterwards must not double-record the reader.
        self._start_view(wid)
        self._read_callback(wid)
        _dispatch_message(_make_message(READER_ID, "blue"))
        self.assertEqual(reader_count(wid), 1, "reader must be recorded only once")
        readers = [r["user_id"] for r in get_readers(wid)]
        self.assertEqual(readers.count(READER_ID), 1)

    def test_max_attempts_blocks(self):
        wid = self._create_question_whisper()
        self._start_view(wid)
        self._read_callback(wid)
        for _ in range(3):
            _dispatch_message(_make_message(READER_ID, "nope"))
        self.assertIsNone(user_states.get(READER_ID),
                          "state must be cleared after exhausting attempts")
        from database import reader_count
        self.assertEqual(reader_count(wid), 0)


class TestQuestionConditionWizard(unittest.TestCase):

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

    def test_wizard_stores_question_and_hashed_answer(self):
        # Start conditional whisper -> type-selection menu is shown.
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        self.assertEqual(user_states.get(SENDER_ID), {"action": "cw_awaiting_condition_type"})
        self.assertTrue(any("اختر نوع الشرط" in t for t in self._texts_sent()))
        self.assertTrue(any("سؤال وإجابة" in label for label in self._button_labels_sent()),
                        "question option must be shown in the type-selection menu")

        # Choose the question condition -> ask for the question.
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:question"))
        self.assertEqual(user_states.get(SENDER_ID), {"action": "cw_awaiting_question"})
        self.assertTrue(any("أرسل السؤال" in t for t in self._texts_sent()))

        # Send the question -> ask for the correct answer.
        _dispatch_message(_make_message(SENDER_ID, "ما لون السماء؟"))
        self.assertEqual(user_states.get(SENDER_ID),
                         {"action": "cw_awaiting_answer", "cw_question": "ما لون السماء؟"})
        self.assertTrue(any("أرسل الإجابة الصحيحة" in t for t in self._texts_sent()))

        # Send the answer -> stored hashed, never plaintext.
        _dispatch_message(_make_message(SENDER_ID, "أزرق"))
        state = user_states.get(SENDER_ID)
        self.assertEqual(state["action"], "cw_awaiting_content")
        qcfg = state["conditions_data"]["question"]
        self.assertEqual(qcfg["question"], "ما لون السماء؟")
        self.assertTrue(qcfg.get("hash"), "answer must be hashed")
        self.assertNotIn("أزرق", json.dumps(qcfg, ensure_ascii=False),
                         "plaintext answer must never be stored")

        # Send the content -> draft saved immediately, no type-selection step.
        _dispatch_message(_make_message(SENDER_ID, "همسة سرية بسؤال"))
        self.assertIsNone(user_states.get(SENDER_ID), "state must be cleared after save")
        from database.envelope import get_draft
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["content"], "همسة سرية بسؤال")
        conds = json.loads(draft["conditions_data"])
        self.assertIn("question", conds)
        self.assertEqual(conds["question"]["question"], "ما لون السماء؟")
        self.assertTrue(conds["question"].get("hash"))
        self.assertNotIn("أزرق", json.dumps(conds, ensure_ascii=False))

        # The share button must open the inline type list with the draft id.
        self.assertTrue(any(
            getattr(btn, "switch_inline_query", None) == f"cw:{draft['id']}"
            for c in bot.send_message.call_args_list
            for kb in ([c.kwargs.get("reply_markup")] if c.kwargs else [])
            if kb and hasattr(kb, "keyboard")
            for row in kb.keyboard
            for btn in row
        ), "share button must switch_inline_query to the cw: draft list")

    def test_wizard_password_path_still_works(self):
        """Choosing password from the new menu keeps the original flow intact."""
        _dispatch_callback(_make_callback(SENDER_ID, "cwhisper_start"))
        _dispatch_callback(_make_callback(SENDER_ID, "cw_cond:password"))
        self.assertEqual(user_states.get(SENDER_ID), {"action": "cw_awaiting_password"})

        _dispatch_message(_make_message(SENDER_ID, "secret"))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_confirmation")
        _dispatch_message(_make_message(SENDER_ID, "secret"))
        self.assertEqual(user_states.get(SENDER_ID).get("action"), "cw_awaiting_content")
        self.assertIn("password", user_states.get(SENDER_ID)["conditions_data"])

        _dispatch_message(_make_message(SENDER_ID, "محتوى بكلمة مرور"))
        self.assertIsNone(user_states.get(SENDER_ID), "state must be cleared after save")
        from database.envelope import get_draft
        draft = get_draft(SENDER_ID)
        self.assertIsNotNone(draft)
        self.assertEqual(draft["content"], "محتوى بكلمة مرور")
        conds = json.loads(draft["conditions_data"])
        self.assertIn("password", conds)
        self.assertTrue(conds["password"].get("hash"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
