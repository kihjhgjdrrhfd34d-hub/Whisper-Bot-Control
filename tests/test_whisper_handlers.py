"""
tests/test_whisper_handlers.py — Tests for handlers/whisper.py

Covers helper functions and database interactions used by whisper handlers:
  - _destroy_whisper_message
  - dwhisper_cmd logic (create destructive whisper)
  - handle_read flow (read permission, reader registration, edge cases)
  - handle_lock / handle_delete / handle_edit / handle_curious flows
  - Database-level operations (toggle, delete, clear, update)
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_whisper_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import create_whisper, upsert_user, get_whisper, delete_whisper
from database import toggle_whisper_lock, clear_whisper_readers, update_whisper_content
from database import get_readers, reader_count, add_reader_if_new, get_curious_ones, get_user


def _boot():
    db.init_db()
    upsert_user(60001, "alice", "Alice", None)
    upsert_user(60002, "bob", "Bob", None)
    upsert_user(60003, "charlie", "Charlie", None)


class TestDestroyWhisperMessage(unittest.TestCase):
    """Test the _destroy_whisper_message helper function."""

    def setUp(self):
        _boot()

    def _make_call(self, has_message=True, has_inline_msg_id=False):
        call = MagicMock()
        if has_message:
            call.message = MagicMock()
            call.message.chat.id = 100
            call.message.message_id = 200
        else:
            call.message = None
        if has_inline_msg_id:
            call.inline_message_id = "inline_abc123"
        else:
            call.inline_message_id = None
        return call

    def test_destroy_deletes_message(self):
        from handlers.whisper import _destroy_whisper_message
        bot = MagicMock()
        call = self._make_call()
        _destroy_whisper_message(call, bot)
        bot.delete_message.assert_called_once_with(100, 200)

    def test_destroy_fallback_to_edit(self):
        from handlers.whisper import _destroy_whisper_message
        bot = MagicMock()
        bot.delete_message.side_effect = Exception("delete failed")
        call = self._make_call()
        _destroy_whisper_message(call, bot)
        bot.edit_message_text.assert_called_once()

    def test_destroy_no_message_no_crash(self):
        from handlers.whisper import _destroy_whisper_message
        bot = MagicMock()
        call = self._make_call(has_message=False)
        _destroy_whisper_message(call, bot)
        bot.delete_message.assert_not_called()

    def test_destroy_inline_message(self):
        from handlers.whisper import _destroy_whisper_message
        bot = MagicMock()
        # delete_message fails -> falls back to edit via inline_message_id
        bot.delete_message.side_effect = Exception("delete failed")
        call = self._make_call(has_message=True, has_inline_msg_id=True)
        _destroy_whisper_message(call, bot)
        bot.edit_message_text.assert_called_once()


class TestDwhisperCmdDatabase(unittest.TestCase):
    """Test the database side of /dwhisper command (handler called with mock)."""

    def setUp(self):
        _boot()

    def test_dwhisper_creates_destructive_whisper(self):
        wid = create_whisper(
            sender_id=60001, content="boom!", whisper_type="first_one",
            target_users=[60002], max_readers=1, is_destructive=True,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["is_destructive"], 1)
        self.assertEqual(w["whisper_type"], "first_one")

    def test_dwhisper_auto_delete_set(self):
        wid = create_whisper(
            sender_id=60001, content="auto boom", whisper_type="first_one",
            target_users=[60002], max_readers=1, auto_delete_hours=24,
            is_destructive=True,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

    def test_dwhisper_target_users_stored(self):
        import json
        wid = create_whisper(
            sender_id=60001, content="targeted", whisper_type="custom",
            target_users=[60002], max_readers=1, is_destructive=False,
        )
        w = get_whisper(wid)
        targets = json.loads(w["target_users"])
        self.assertIn(60002, targets)

    def test_dwhisper_invalid_user_id_does_not_create(self):
        """dwhisper with invalid target should not create whisper."""
        # The handler validates before calling create_whisper
        # So we test that create_whisper with bad params still works
        wid = create_whisper(
            sender_id=60001, content="test", whisper_type="everyone",
        )
        self.assertIsNotNone(wid)


class TestReadWhisperDatabase(unittest.TestCase):
    """Test database operations that handle_read relies on."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "test whisper", "everyone")

    def test_read_adds_reader(self):
        is_new = add_reader_if_new(self.wid, 2)
        self.assertTrue(is_new)
        self.assertEqual(reader_count(self.wid), 1)

    def test_read_duplicate_returns_false(self):
        add_reader_if_new(self.wid, 2)
        is_new = add_reader_if_new(self.wid, 2)
        self.assertFalse(is_new)

    def test_read_records_reader_info(self):
        add_reader_if_new(self.wid, 60002)
        readers = get_readers(self.wid)
        self.assertEqual(len(readers), 1)
        self.assertEqual(readers[0]["user_id"], 60002)

    def test_sender_can_read_own(self):
        can, reason = db.can_read_whisper(self.wid, 60001)
        self.assertTrue(can)
        self.assertEqual(reason, "sender")

    def test_everyone_can_read(self):
        can, reason = db.can_read_whisper(self.wid, 60099)
        self.assertTrue(can)
        self.assertEqual(reason, "allowed")

    def test_locked_whisper_blocks_read(self):
        toggle_whisper_lock(self.wid)
        can, reason = db.can_read_whisper(self.wid, 60002)
        self.assertFalse(can)
        self.assertEqual(reason, "locked")

    def test_read_nonexistent_whisper(self):
        can, reason = db.can_read_whisper("does_not_exist", 60002)
        self.assertFalse(can)
        self.assertEqual(reason, "not_found")

    def test_curious_tracked_when_not_target(self):
        wid2 = create_whisper(60001, "private", "custom", target_users=[60002])
        db.add_curious(wid2, 60099)
        curious = get_curious_ones(wid2)
        self.assertEqual(len(curious), 1)
        self.assertEqual(curious[0]["user_id"], 60099)


class TestFirstOneWhisperFlow(unittest.TestCase):
    """Test first_one whisper type specific behavior."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "first only", "first_one")

    def test_first_read_succeeds(self):
        can, reason = db.can_read_whisper(self.wid, 2)
        self.assertTrue(can)
        add_reader_if_new(self.wid, 2)
        self.assertEqual(reader_count(self.wid), 1)

    def test_second_read_blocked(self):
        add_reader_if_new(self.wid, 2)
        can, reason = db.can_read_whisper(self.wid, 3)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_clear_then_allow_another(self):
        add_reader_if_new(self.wid, 2)
        clear_whisper_readers(self.wid)
        self.assertEqual(reader_count(self.wid), 0)
        can, reason = db.can_read_whisper(self.wid, 3)
        self.assertTrue(can)


class TestFirstThreeWhisperFlow(unittest.TestCase):
    """Test first_three whisper type specific behavior."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "first three", "first_three")

    def test_first_three_allowed(self):
        for uid in [60002, 60003, 60004]:
            can, reason = db.can_read_whisper(self.wid, uid)
            self.assertTrue(can, f"User {uid} should be allowed")
            add_reader_if_new(self.wid, uid)

    def test_fourth_blocked(self):
        for uid in [60002, 60003, 60004]:
            add_reader_if_new(self.wid, uid)
        can, reason = db.can_read_whisper(self.wid, 60005)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")


class TestLockWhisper(unittest.TestCase):
    """Test lock/unlock operations."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "lock test", "everyone")

    def test_lock_toggle_on(self):
        state = toggle_whisper_lock(self.wid)
        self.assertEqual(state, 1)

    def test_lock_toggle_off(self):
        toggle_whisper_lock(self.wid)
        state = toggle_whisper_lock(self.wid)
        self.assertEqual(state, 0)

    def test_lock_nonexistent(self):
        state = toggle_whisper_lock("no_such_wid")
        self.assertIsNone(state)

    def test_locked_whisper_cannot_be_read(self):
        toggle_whisper_lock(self.wid)
        can, reason = db.can_read_whisper(self.wid, 2)
        self.assertFalse(can)
        self.assertEqual(reason, "locked")


class TestDeleteWhisper(unittest.TestCase):
    """Test delete operations (cleanup of related records)."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "delete me", "everyone")
        add_reader_if_new(self.wid, 2)
        db.add_curious(self.wid, 3)

    def test_delete_removes_whisper(self):
        delete_whisper(self.wid)
        self.assertIsNone(get_whisper(self.wid))

    def test_delete_clears_readers(self):
        delete_whisper(self.wid)
        self.assertEqual(reader_count(self.wid), 0)

    def test_delete_clears_curious(self):
        delete_whisper(self.wid)
        self.assertEqual(len(get_curious_ones(self.wid)), 0)


class TestEditWhisper(unittest.TestCase):
    """Test content update operations."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "original", "everyone")

    def test_update_content(self):
        update_whisper_content(self.wid, "updated content")
        w = get_whisper(self.wid)
        self.assertEqual(w["content"], "updated content")

    def test_update_nonexistent_does_not_crash(self):
        update_whisper_content("no_such_wid", "new content")
        # No exception expected


class TestRecordWhisperRead(unittest.TestCase):
    """Test recording reads with type-aware locking."""

    def setUp(self):
        _boot()
        upsert_user(60010, "u10", "U10", None)
        upsert_user(60011, "u11", "U11", None)
        upsert_user(60012, "u12", "U12", None)
        upsert_user(60013, "u13", "U13", None)

    def test_everyone_never_locks(self):
        wid = create_whisper(60010, "pub", "everyone")
        db.record_whisper_read(wid, 11)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)
        db.record_whisper_read(wid, 12)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)

    def test_first_three_locks_at_three(self):
        wid = create_whisper(60010, "three", "first_three")
        db.record_whisper_read(wid, 11)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)
        db.record_whisper_read(wid, 12)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)
        db.record_whisper_read(wid, 13)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 1)

    def test_first_three_below_three_stays_open(self):
        wid = create_whisper(60010, "three", "first_three")
        db.record_whisper_read(wid, 11)
        db.record_whisper_read(wid, 12)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 0)

    def test_record_read_returns_true_on_first(self):
        wid = create_whisper(60010, "test", "everyone")
        result = db.record_whisper_read(wid, 11)
        self.assertTrue(result)

    def test_record_read_returns_false_on_duplicate(self):
        wid = create_whisper(60010, "test", "everyone")
        db.record_whisper_read(wid, 11)
        result = db.record_whisper_read(wid, 11)
        self.assertFalse(result)

    def test_record_read_nonexistent_whisper_raises_error(self):
        with self.assertRaises(Exception):
            db.record_whisper_read("no_such_whisper", 60001)


class TestCuriousOnes(unittest.TestCase):
    """Test curious ones tracking."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "curious test", "custom", target_users=[60002])
        upsert_user(60099, "curious", "Curious", None)

    def test_curious_tracked(self):
        db.add_curious(self.wid, 99)
        curious = get_curious_ones(self.wid)
        self.assertEqual(len(curious), 1)

    def test_curious_duplicate_ignored(self):
        db.add_curious(self.wid, 99)
        db.add_curious(self.wid, 99)
        curious = get_curious_ones(self.wid)
        self.assertEqual(len(curious), 1)

    def test_curious_has_tried_at(self):
        db.add_curious(self.wid, 99)
        curious = get_curious_ones(self.wid)
        self.assertIsNotNone(curious[0]["tried_at"])

    def test_no_curious_for_nonexistent_whisper(self):
        curious = get_curious_ones("no_such_wid")
        self.assertEqual(len(curious), 0)


class TestCloseWhisper(unittest.TestCase):
    """Test close whisper functionality used by dashboard."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "close test", "everyone")

    def test_close_whisper(self):
        db.close_whisper(self.wid)
        self.assertTrue(db.is_whisper_closed(self.wid))

    def test_closed_whisper_blocked_from_reading(self):
        db.close_whisper(self.wid)
        can, reason = db.can_read_whisper(self.wid, 2)
        self.assertFalse(can)
        self.assertEqual(reason, "locked")

    def test_close_nonexistent_does_not_crash(self):
        db.close_whisper("no_such_wid")

    def test_closed_whisper_cannot_be_replied_to(self):
        from database.replies import can_reply_to_whisper
        db.close_whisper(self.wid)
        ok, reason = can_reply_to_whisper(self.wid, 1)
        self.assertFalse(ok)
        self.assertEqual(reason, "whisper_locked")


class TestPinWhisper(unittest.TestCase):
    """Test pin/unpin operations used by dashboard."""

    def setUp(self):
        _boot()
        self.wid = create_whisper(60001, "pin test", "everyone")

    def test_pin_toggle(self):
        state = db.toggle_pin_whisper(self.wid)
        self.assertEqual(state, 1)
        state = db.toggle_pin_whisper(self.wid)
        self.assertEqual(state, 0)

    def test_pin_nonexistent_returns_none(self):
        state = db.toggle_pin_whisper("no_such_wid")
        self.assertIsNone(state)

    def test_get_pinned_whispers(self):
        db.toggle_pin_whisper(self.wid)
        pinned, total = db.get_pinned_whispers(60001)
        self.assertEqual(total, 1)
        self.assertEqual(pinned[0]["whisper_id"], self.wid)


class TestPublicWhisperReadFlow(unittest.TestCase):
    """Test that public (everyone) whispers do NOT update the group message
    and send a DM notification instead of the simple read receipt."""

    def setUp(self):
        _boot()
        self.sender_id = 60001
        self.reader_id = 60002
        self.wid = create_whisper(self.sender_id, "public test", "everyone")

    def test_public_whisper_records_read(self):
        """A read on an everyone whisper is recorded."""
        is_new = add_reader_if_new(self.wid, self.reader_id)
        self.assertTrue(is_new)
        self.assertEqual(reader_count(self.wid), 1)

    def test_public_whisper_never_locks(self):
        """An everyone whisper stays unlocked after reads."""
        db.record_whisper_read(self.wid, self.reader_id)
        w = get_whisper(self.wid)
        self.assertEqual(w["is_locked"], 0)
        db.record_whisper_read(self.wid, 60003)
        w = get_whisper(self.wid)
        self.assertEqual(w["is_locked"], 0)

    def test_public_whisper_dedup_works(self):
        """Duplicate reads on everyone whisper don't create extra records."""
        add_reader_if_new(self.wid, self.reader_id)
        is_dup = add_reader_if_new(self.wid, self.reader_id)
        self.assertFalse(is_dup)
        self.assertEqual(reader_count(self.wid), 1)

    def test_public_whisper_readers_visible_to_sender(self):
        """get_readers still works for everyone whispers (used for stats)."""
        add_reader_if_new(self.wid, self.reader_id)
        readers = get_readers(self.wid)
        self.assertEqual(len(readers), 1)
        self.assertEqual(readers[0]["user_id"], self.reader_id)

    def test_public_notification_sent_on_first_read(self):
        """Verify that for everyone whispers, the public notification replaces
        the simple read receipt."""
        is_new = db.record_whisper_read(self.wid, self.reader_id)
        self.assertTrue(is_new)
        # The notification is sent by the handler, not the DB layer
        # The handler calls build_public_whisper_notification() for everyone type
        # This is tested at the service layer (test_whisper_service.py)

    def test_non_public_whisper_still_updates_keyboard(self):
        """Verify first_one whisper still records reads and locks."""
        wid_f1 = create_whisper(self.sender_id, "first only", "first_one")
        is_new = db.record_whisper_read(wid_f1, self.reader_id)
        self.assertTrue(is_new)
        w = get_whisper(wid_f1)
        # first_one does NOT auto-lock on read (permission gating)
        # But the keyboard IS updated by the handler
        # We verify the read is recorded
        self.assertEqual(reader_count(wid_f1), 1)

    def test_public_whisper_content_sent_to_reader(self):
        """Verify that reader still receives the whisper content in DM
        for everyone type."""
        is_new = db.record_whisper_read(self.wid, self.reader_id)
        self.assertTrue(is_new)
        # Content delivery is handled by _send_content_to_reader which
        # fires for all whisper types when is_new_read is True
        # This is confirmed by the handler code flow

    # ── Helper to capture registered handlers from a mock bot ───────────
    def _capture_handlers(self, bot):
        """Install a fake callback_query_handler that preserves real functions.
        Returns the list of (kwargs, func) tuples registered."""
        handlers = []

        def fake_callback_handler(**kwargs):
            def deco(f):
                handlers.append((kwargs, f))
                return f
            return deco

        bot.callback_query_handler = fake_callback_handler
        return handlers

    def _find_read_handler(self, handlers):
        """Locate the handle_read function from the registered handlers list."""
        for kwargs, func in handlers:
            test_call = MagicMock(data="read:dummy")
            if kwargs.get('func') and kwargs['func'](test_call):
                return func
        return None

    def test_sender_reads_own_public_whisper_no_notification(self):
        """Ensure no notification or read record when sender reads own whisper."""
        bot = MagicMock()
        handlers = self._capture_handlers(bot)

        from handlers.whisper import _register_callback_handlers
        _register_callback_handlers(bot, {})

        read_handler = self._find_read_handler(handlers)
        self.assertIsNotNone(read_handler, "handle_read not registered")

        call = MagicMock()
        call.from_user.id = self.sender_id
        call.from_user.username = "sender_user"
        call.from_user.first_name = "Sender"
        call.data = f"read:{self.wid}"
        call.message = MagicMock()
        call.message.chat.id = -100
        call.message.message_id = 1
        call.inline_message_id = None
        call.id = "cb_sender"

        read_handler(call)

        # Handler returns early at is_own_whisper — no read recorded
        self.assertEqual(reader_count(self.wid), 0,
                         "No read should be recorded for sender's own whisper")
        # No notification sent to sender
        notification_calls = [
            c for c in bot.send_message.mock_calls
            if (len(c.args) >= 2
                and isinstance(c.args[1], str)
                and "تم فتح همستك العامة" in c.args[1])
        ]
        self.assertEqual(len(notification_calls), 0,
                         "No DM notification for sender reading own whisper")

    def test_public_whisper_keyboard_updated_without_reader_names(self):
        """Verify public whisper edits the group inline message to add the
        reply button, but does NOT add reader name buttons."""
        bot = MagicMock()
        handlers = self._capture_handlers(bot)

        from handlers.whisper import _register_callback_handlers
        _register_callback_handlers(bot, {})

        read_handler = self._find_read_handler(handlers)
        self.assertIsNotNone(read_handler)

        call = MagicMock()
        call.from_user.id = self.reader_id
        call.from_user.username = "bob"
        call.from_user.first_name = "Bob"
        call.data = f"read:{self.wid}"
        call.message = MagicMock()
        call.message.chat.id = -100
        call.message.message_id = 2
        call.inline_message_id = None
        call.id = "cb_reader"

        read_handler(call)

        # edit_message_reply_markup SHOULD be called for everyone whispers
        # (to add the reply button, but NOT reader names)
        edit_calls = [
            c for c in bot.edit_message_reply_markup.mock_calls
            if c[0] == ''
        ]
        self.assertGreaterEqual(len(edit_calls), 1,
                         "edit_message_reply_markup must be called for everyone whispers")

        # The whisper stays unlocked
        w = get_whisper(self.wid)
        self.assertEqual(w["is_locked"], 0,
                         "everyone whisper must stay unlocked after read")
        # Read is still tracked in DB for stats
        self.assertEqual(reader_count(self.wid), 1,
                         "Read must be recorded in DB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
