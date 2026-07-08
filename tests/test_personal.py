import os
import sys
import unittest
from unittest.mock import MagicMock

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"] = "0:test_token_placeholder"
os.environ["ADMIN_IDS"] = "999"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db


class TestResolveTarget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        db.upsert_user(1001, "alice", "Alice", "Smith")
        db.upsert_user(1002, "bob", "Bob", "Jones")
        db.upsert_user(1003, None, "Charlie", None)

    def _make_msg(self, text=None, reply_to_user_id=None, reply_to_is_bot=False):
        msg = MagicMock()
        msg.text = text
        if reply_to_user_id is not None:
            reply_from = MagicMock()
            reply_from.id = reply_to_user_id
            reply_from.is_bot = reply_to_is_bot
            reply = MagicMock()
            reply.from_user = reply_from
            msg.reply_to_message = reply
        else:
            msg.reply_to_message = None
        return msg

    def test_reply_target_resolves_correctly(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(reply_to_user_id=2001)
        target_id, hint = _resolve_target(bot, msg)
        self.assertEqual(target_id, 2001)
        self.assertIsNone(hint)

    def test_reply_target_skips_bot(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="@alice", reply_to_user_id=999999, reply_to_is_bot=True)
        target_id, hint = _resolve_target(bot, msg)
        self.assertEqual(target_id, 1001)
        self.assertIsNone(hint)

    def test_username_lookup_finds_user(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="@alice")
        target_id, hint = _resolve_target(bot, msg)
        self.assertEqual(target_id, 1001)
        self.assertIsNone(hint)

    def test_username_lookup_case_insensitive(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="@ALICE")
        target_id, hint = _resolve_target(bot, msg)
        self.assertEqual(target_id, 1001)
        self.assertIsNone(hint)

    def test_username_lookup_unknown_returns_error(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="@nonexistent")
        target_id, hint = _resolve_target(bot, msg)
        self.assertIsNone(target_id)
        self.assertIn("لم أجد", hint or "")

    def test_numeric_user_id(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="5505")
        target_id, hint = _resolve_target(bot, msg)
        self.assertEqual(target_id, 5505)
        self.assertIsNone(hint)

    def test_numeric_user_id_negative(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="-5")
        target_id, hint = _resolve_target(bot, msg)
        self.assertIsNone(target_id)
        self.assertIn("موجبًا", hint or "")

    def test_numeric_user_id_zero(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="0")
        target_id, hint = _resolve_target(bot, msg)
        self.assertIsNone(target_id)
        self.assertIn("موجبًا", hint or "")

    def test_invalid_format_returns_error(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="not_a_username_or_id")
        target_id, hint = _resolve_target(bot, msg)
        self.assertIsNone(target_id)
        self.assertIn("ليس معرفًا رقميًا", hint or "")

    def test_empty_text_no_reply_returns_error(self):
        from handlers.personal import _resolve_target
        bot = MagicMock()
        msg = self._make_msg(text="")
        target_id, hint = _resolve_target(bot, msg)
        self.assertIsNone(target_id)
        self.assertIn("أرسل معرف", hint or "")


class TestPersonalDB(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        db.init_db()
        from database.personal import init_personal_db
        init_personal_db()

    def setUp(self):
        from database.personal import get_conn
        with get_conn() as c:
            c.execute("DELETE FROM personal_whispers")
            c.commit()

    def _count_whispers(self):
        from database.personal import get_conn
        with get_conn() as c:
            row = c.execute("SELECT COUNT(*) FROM personal_whispers").fetchone()
            return row[0]

    def test_create_and_retrieve(self):
        from database.personal import create_personal_whisper, get_personal_whisper
        wid = create_personal_whisper(1, 2, "hello")
        pw = get_personal_whisper(wid)
        self.assertIsNotNone(pw)
        self.assertEqual(pw["content"], "hello")
        self.assertEqual(pw["sender_id"], 1)
        self.assertEqual(pw["recipient_id"], 2)

    def test_inbox_and_sent(self):
        from database.personal import create_personal_whisper, get_user_inbox, get_user_sent
        create_personal_whisper(10, 20, "msg1")
        create_personal_whisper(10, 30, "msg2")
        inbox_20, total_20 = get_user_inbox(20)
        self.assertEqual(total_20, 1)
        inbox_30, total_30 = get_user_inbox(30)
        self.assertEqual(total_30, 1)
        sent_10, sent_total = get_user_sent(10)
        self.assertEqual(sent_total, 2)

    def test_mark_as_read(self):
        from database.personal import create_personal_whisper, mark_as_read, count_unread
        wid = create_personal_whisper(5, 6, "secret")
        self.assertEqual(count_unread(6), 1)
        mark_as_read(wid, 6)
        self.assertEqual(count_unread(6), 0)

    def test_crud_round_trip(self):
        from database.personal import create_personal_whisper
        create_personal_whisper(100, 200, "test")
        self.assertEqual(self._count_whispers(), 1)
        create_personal_whisper(100, 300, "test2")
        self.assertEqual(self._count_whispers(), 2)


if __name__ == "__main__":
    unittest.main()
