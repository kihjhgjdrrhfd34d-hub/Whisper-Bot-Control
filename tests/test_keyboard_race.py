"""
tests/test_keyboard_race.py — Race condition tests for _update_group_keyboard.

Verifies that concurrent calls to _update_group_keyboard always produce
a keyboard containing ALL reader names from the database.
"""
import os
import sys
import threading
import tempfile
import atexit
import unittest
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_race_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import create_whisper, upsert_user, get_whisper
from database import add_reader_if_new, reader_count, get_readers
from database import update_whisper_group_message


def _boot():
    db.init_db()
    upsert_user(60001, "alice",   "Alice",   None)
    upsert_user(60002, "bob",     "Bob",     None)
    upsert_user(60003, "charlie", "Charlie", None)
    upsert_user(60004, "dave",    "Dave",    None)


class TestKeyboardLockMechanism(unittest.TestCase):
    """Test the per-whisper locking mechanism in isolation."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "lock test", "first_three")
        update_whisper_group_message(self.wid, inline_message_id="test_inline")

    def test_lock_is_reused_for_same_whisper_id(self):
        from handlers.whisper import _get_keyboard_lock
        lock1 = _get_keyboard_lock(self.wid)
        lock2 = _get_keyboard_lock(self.wid)
        self.assertIs(lock1, lock2, "Same whisper_id must return the same Lock")

    def test_lock_differs_for_different_whisper_ids(self):
        from handlers.whisper import _get_keyboard_lock
        wid2 = create_whisper(60001, "lock test 2", "first_one")
        lock1 = _get_keyboard_lock(self.wid)
        lock2 = _get_keyboard_lock(wid2)
        self.assertIsNot(lock1, lock2, "Different whisper_ids must return different Locks")

    def test_lock_serializes_concurrent_access(self):
        """Two threads calling _update_group_keyboard must not interleave."""
        from handlers.whisper import _update_group_keyboard

        bot = MagicMock()
        w = get_whisper(self.wid)

        for uid in [60002, 60003]:
            add_reader_if_new(self.wid, uid)

        results = []

        def update():
            try:
                _update_group_keyboard(bot, self.wid, w)
                results.append("ok")
            except Exception as e:
                results.append(f"fail: {e}")

        threads = [threading.Thread(target=update) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(results), 5)
        self.assertTrue(all(r == "ok" for r in results),
                        "All concurrent calls must succeed")


class TestUpdateGroupKeyboardReaders(unittest.TestCase):
    """_update_group_keyboard must fetch readers from DB inside the function."""

    def setUp(self):
        _boot()
        self.sender = 60001
        self.wid = create_whisper(self.sender, "ft race", "first_three")
        update_whisper_group_message(self.wid, inline_message_id="test_inline")

    def _verify_reader_names(self, kb, expected_count):
        name_buttons = [
            btn for row in kb.keyboard
            for btn in row
            if btn.text.startswith("👤")
        ]
        self.assertEqual(len(name_buttons), expected_count,
                         f"Expected {expected_count} reader name buttons in keyboard")
        return name_buttons

    def test_zero_readers_shows_no_names(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        w = get_whisper(self.wid)
        _update_group_keyboard(bot, self.wid, w)
        if bot.edit_message_reply_markup.called:
            kb = bot.edit_message_reply_markup.call_args[1]["reply_markup"]
            self._verify_reader_names(kb, 0)

    def test_one_reader_shows_one_name(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        w = get_whisper(self.wid)
        add_reader_if_new(self.wid, 60002)
        _update_group_keyboard(bot, self.wid, w)
        self.assertTrue(bot.edit_message_reply_markup.called)
        kb = bot.edit_message_reply_markup.call_args[1]["reply_markup"]
        self._verify_reader_names(kb, 1)

    def test_two_readers_show_two_names(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        w = get_whisper(self.wid)
        for uid in [60002, 60003]:
            add_reader_if_new(self.wid, uid)
        _update_group_keyboard(bot, self.wid, w)
        self.assertTrue(bot.edit_message_reply_markup.called)
        kb = bot.edit_message_reply_markup.call_args[1]["reply_markup"]
        self._verify_reader_names(kb, 2)

    def test_three_readers_show_three_names(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        w = get_whisper(self.wid)
        for uid in [60002, 60003, 60004]:
            add_reader_if_new(self.wid, uid)
        _update_group_keyboard(bot, self.wid, w)
        self.assertTrue(bot.edit_message_reply_markup.called)
        kb = bot.edit_message_reply_markup.call_args[1]["reply_markup"]
        self._verify_reader_names(kb, 3)


class TestConcurrentFirstThreeKeyboard(unittest.TestCase):
    """
    Simulate the race: multiple readers read a first_three whisper concurrently.
    Each thread: add reader → call _update_group_keyboard.
    Final keyboard must show ALL 3 reader names.
    """

    def setUp(self):
        _boot()
        self.sender = 60001
        self.wid = create_whisper(self.sender, "ft race", "first_three")
        update_whisper_group_message(self.wid, inline_message_id="test_inline")
        self.w = get_whisper(self.wid)

    def test_concurrent_reads_all_appear_in_keyboard(self):
        """
        Three concurrent reads on a first_three whisper.
        The final keyboard must contain all 3 reader names.
        """
        from handlers.whisper import _update_group_keyboard

        bot = MagicMock()
        reader_ids = [60002, 60003, 60004]
        errors = []

        def read_and_update(uid):
            try:
                add_reader_if_new(self.wid, uid)
                _update_group_keyboard(bot, self.wid, self.w)
            except Exception as e:
                errors.append(f"uid={uid}: {e}")

        barrier = threading.Barrier(3)

        def synced_read(uid):
            barrier.wait()
            read_and_update(uid)

        threads = [threading.Thread(target=synced_read, args=(uid,))
                   for uid in reader_ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertTrue(bot.edit_message_reply_markup.called,
                        "edit_message_reply_markup must be called")

        # Inspect the LAST call's keyboard
        call = bot.edit_message_reply_markup.call_args
        kb = call[1]["reply_markup"]

        name_buttons = [
            btn for row in kb.keyboard
            for btn in row
            if btn.text.startswith("👤")
        ]
        self.assertEqual(len(name_buttons), 3,
                         "Keyboard must show all 3 reader names after concurrent reads")

        # Also verify the final DB state
        self.assertEqual(reader_count(self.wid), 3)

    def test_concurrent_with_existing_readers_no_data_loss(self):
        """
        2 readers already in DB. A 3rd concurrent read must not cause
        the keyboard to show only 2 names.
        """
        from handlers.whisper import _update_group_keyboard

        # Pre-add 2 readers
        add_reader_if_new(self.wid, 60002)
        add_reader_if_new(self.wid, 60003)

        bot = MagicMock()
        errors = []
        start = threading.Event()

        def add_and_update():
            start.wait()
            add_reader_if_new(self.wid, 60004)
            _update_group_keyboard(bot, self.wid, self.w)

        def update_from_thread():
            start.wait()
            try:
                _update_group_keyboard(bot, self.wid, self.w)
            except Exception as e:
                errors.append(str(e))

        t1 = threading.Thread(target=add_and_update)
        t2 = threading.Thread(target=update_from_thread)
        t1.start()
        t2.start()
        start.set()
        t1.join()
        t2.join()

        self.assertEqual(len(errors), 0, f"Errors: {errors}")
        self.assertTrue(bot.edit_message_reply_markup.called)

        call = bot.edit_message_reply_markup.call_args
        kb = call[1]["reply_markup"]

        name_buttons = [
            btn for row in kb.keyboard
            for btn in row
            if btn.text.startswith("👤")
        ]
        self.assertEqual(len(name_buttons), 3)


class TestEditMessageReplyMarkupOrdering(unittest.TestCase):
    """
    Verify that edit_message_reply_markup in _update_group_keyboard
    is called with coordinates from the call object (not stale DB values).
    """

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "order test", "first_three")
        self.w = get_whisper(self.wid)

    def test_uses_call_inline_message_id_when_available(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        add_reader_if_new(self.wid, 60002)

        call = MagicMock()
        call.inline_message_id = "cb_inline_123"
        call.message = None

        _update_group_keyboard(bot, self.wid, self.w, call=call)

        self.assertTrue(bot.edit_message_reply_markup.called)
        kwargs = bot.edit_message_reply_markup.call_args[1]
        self.assertEqual(kwargs.get("inline_message_id"), "cb_inline_123")

    def test_uses_call_message_when_no_inline_id(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        add_reader_if_new(self.wid, 60002)

        call = MagicMock()
        call.inline_message_id = None
        call.message.chat.id = -100
        call.message.message_id = 42

        _update_group_keyboard(bot, self.wid, self.w, call=call)

        self.assertTrue(bot.edit_message_reply_markup.called)
        kwargs = bot.edit_message_reply_markup.call_args[1]
        self.assertEqual(kwargs.get("chat_id"), -100)
        self.assertEqual(kwargs.get("message_id"), 42)

    def test_falls_back_to_stored_coords_when_no_call(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        add_reader_if_new(self.wid, 60002)
        update_whisper_group_message(self.wid, inline_message_id="db_inline_789")

        w = get_whisper(self.wid)
        _update_group_keyboard(bot, self.wid, w)

        self.assertTrue(bot.edit_message_reply_markup.called)
        kwargs = bot.edit_message_reply_markup.call_args[1]
        self.assertEqual(kwargs.get("inline_message_id"), "db_inline_789")


if __name__ == "__main__":
    unittest.main(verbosity=2)
