"""
tests/test_media_reply_identity.py — E2E test: media whisper reply delivers
real sender identity (first name + username), not anonymous.

Verifies:
  1. mwreply: button is attached when media whisper is opened in private
  2. Callback stores pending_media_reply state
  3. Reply message is saved to whisper_replies DB
  4. Delivered message header shows real sender identity
  5. Delivered message is NOT labeled anonymous
  6. Reply is linked to original whisper for statistics
  7. media_whisper_read_keyboard includes mwreply: callback (not wsp_reply:)
  8. All access rules (first_one, first_three, everyone, destructive) still work
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_media_reply_id_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user, set_setting,
    record_whisper_read, can_read_whisper,
)
from database.replies import (
    create_reply, get_reply, get_replies, count_replies, can_reply_to_whisper,
)

# User IDs used throughout tests
SENDER = 40001   # whisper sender ("Bob")
READER = 40002   # whisper reader ("Alice")


def _boot():
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM whisper_replies")
        conn.execute("DELETE FROM reply_reads")
        conn.execute("DELETE FROM whisper_readers")
        conn.execute("DELETE FROM curious_ones")
        conn.execute("DELETE FROM whispers")
        conn.execute("DELETE FROM users")
        conn.commit()
    # Use first_name / username so _get_sender_display returns predictable values
    upsert_user(SENDER, "bob_id", "Bob", None)
    upsert_user(READER, "alice_id", "Alice", None)
    set_setting("whisper_replies_enabled", "1")
    set_setting("bot_active", "1")


def _make_text_msg(text, sender_id=READER, chat_id=READER):
    msg = MagicMock()
    msg.content_type = "text"
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = sender_id
    msg.from_user.username = f"user_{sender_id}"
    msg.from_user.first_name = f"User{sender_id}"
    msg.from_user.last_name = None
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    return msg


def _make_photo_msg(file_id="REPLY_PHOTO", caption="My reply photo",
                    sender_id=READER, chat_id=READER):
    msg = MagicMock()
    msg.content_type = "photo"
    msg.caption = caption
    msg.from_user = MagicMock()
    msg.from_user.id = sender_id
    msg.from_user.username = f"user_{sender_id}"
    msg.from_user.first_name = f"User{sender_id}"
    msg.from_user.last_name = None
    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "private"
    photo = MagicMock()
    photo.file_id = file_id
    small = MagicMock()
    small.file_id = "SMALL"
    msg.photo = [small, photo]
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# 1. media_whisper_read_keyboard uses mwreply: callback
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperReadKeyboard(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_keyboard_uses_mwreply_callback(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="KB_PHOTO", media_type="photo",
        )
        from handlers.media_whispers import media_whisper_read_keyboard
        kb = media_whisper_read_keyboard(wid)

        found_mwreply = False
        found_wsp_reply = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("mwreply:"):
                    found_mwreply = True
                    self.assertIn(wid, btn.callback_data)
                if btn.callback_data and btn.callback_data.startswith("wsp_reply:"):
                    found_wsp_reply = True
        self.assertTrue(found_mwreply, "mwreply: button not found in keyboard")
        self.assertFalse(found_wsp_reply, "wsp_reply: should NOT be in media keyboard")

    def test_keyboard_no_conv_when_no_replies(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="KB_NO_CONV", media_type="photo",
        )
        from handlers.media_whispers import media_whisper_read_keyboard
        kb = media_whisper_read_keyboard(wid)

        has_conv = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("wsp_conv:"):
                    has_conv = True
        self.assertFalse(has_conv, "Conversation button should not appear with 0 replies")

    def test_keyboard_has_conv_when_replies_exist(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="KB_CONV", media_type="photo",
        )
        create_reply(whisper_id=wid, sender_id=READER, content="first reply")

        from handlers.media_whispers import media_whisper_read_keyboard
        kb = media_whisper_read_keyboard(wid)

        has_conv = False
        for row in kb.keyboard:
            for btn in row:
                if btn.callback_data and btn.callback_data.startswith("wsp_conv:"):
                    has_conv = True
        self.assertTrue(has_conv, "Conversation button should appear when replies exist")


# ─────────────────────────────────────────────────────────────────────────────
# 2. mwreply: callback stores correct state (reader must have read whisper)
# ─────────────────────────────────────────────────────────────────────────────

class TestMwReplyCallbackState(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_callback_stores_pending_media_reply_state(self):
        wid = create_whisper(
            sender_id=SENDER, content="Secret photo", whisper_type="everyone",
            message_type="photo", file_id="CB_STATE_PHOTO", media_type="photo",
        )
        record_whisper_read(wid, READER)  # reader must have read the whisper

        bot = MagicMock()
        user_states = {}

        captured = {}
        def fake_callback_handler(**kwargs):
            def deco(f):
                captured["handler"] = f
                return f
            return deco
        bot.callback_query_handler = fake_callback_handler

        from handlers.media_whispers import register_media_whisper_handlers
        register_media_whisper_handlers(bot, user_states)

        call = MagicMock()
        call.id = "cb1"
        call.data = f"mwreply:{wid}"
        call.from_user = MagicMock()
        call.from_user.id = READER

        captured["handler"](call)

        self.assertIn(READER, user_states)
        state = user_states[READER]
        self.assertEqual(state["action"], "pending_media_reply")
        self.assertEqual(state["whisper_id"], wid)

    def test_callback_sends_prompt_message(self):
        wid = create_whisper(
            sender_id=SENDER, content="Photo content", whisper_type="everyone",
            message_type="photo", file_id="CB_PROMPT", media_type="photo",
        )
        record_whisper_read(wid, READER)

        bot = MagicMock()
        user_states = {}

        captured = {}
        def fake_callback_handler(**kwargs):
            def deco(f):
                captured["handler"] = f
                return f
            return deco
        bot.callback_query_handler = fake_callback_handler

        from handlers.media_whispers import register_media_whisper_handlers
        register_media_whisper_handlers(bot, user_states)

        call = MagicMock()
        call.id = "cb2"
        call.data = f"mwreply:{wid}"
        call.from_user = MagicMock()
        call.from_user.id = READER

        captured["handler"](call)

        # Bot should have sent a prompt message
        bot.send_message.assert_called()
        prompt_text = bot.send_message.call_args[0][1]
        self.assertIn("الهمسة الأصلية", prompt_text)
        self.assertIn("صورة", prompt_text)
        self.assertIn("أرسل ردّك الآن", prompt_text)

    def test_sender_always_allowed_to_reply(self):
        """Whisper sender can always reply without being a reader."""
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="CB_SENDER", media_type="photo",
        )
        # No record_whisper_read for SENDER — sender doesn't need to be a reader

        bot = MagicMock()
        user_states = {}

        captured = {}
        def fake_callback_handler(**kwargs):
            def deco(f):
                captured["handler"] = f
                return f
            return deco
        bot.callback_query_handler = fake_callback_handler

        from handlers.media_whispers import register_media_whisper_handlers
        register_media_whisper_handlers(bot, user_states)

        call = MagicMock()
        call.id = "cb3"
        call.data = f"mwreply:{wid}"
        call.from_user = MagicMock()
        call.from_user.id = SENDER

        captured["handler"](call)

        self.assertIn(SENDER, user_states)
        self.assertEqual(user_states[SENDER]["action"], "pending_media_reply")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Reply saved to DB and linked to original whisper
# ─────────────────────────────────────────────────────────────────────────────

class TestReplySavedToDatabase(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_reply_saved_with_correct_whisper_id(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="DB_PHOTO", media_type="photo",
        )
        reply_id = create_reply(
            whisper_id=wid, sender_id=READER, content="Nice photo!",
            media_type=None, file_id=None,
        )
        self.assertIsNotNone(reply_id)

        reply = get_reply(reply_id)
        self.assertIsNotNone(reply)
        self.assertEqual(reply["whisper_id"], wid)
        self.assertEqual(reply["sender_id"], READER)
        self.assertEqual(reply["content"], "Nice photo!")

    def test_reply_count_increments(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="DB_CNT", media_type="photo",
        )
        self.assertEqual(count_replies(wid), 0)
        create_reply(whisper_id=wid, sender_id=READER, content="R1")
        self.assertEqual(count_replies(wid), 1)
        create_reply(whisper_id=wid, sender_id=SENDER, content="R2")
        self.assertEqual(count_replies(wid), 2)

    def test_media_reply_saved_with_media_type(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="DB_MEDIA", media_type="photo",
        )
        reply_id = create_reply(
            whisper_id=wid, sender_id=READER, content="Photo reply",
            media_type="photo", file_id="REPLY_PHOTO_FID",
        )
        reply = get_reply(reply_id)
        self.assertEqual(reply["media_type"], "photo")
        self.assertEqual(reply["file_id"], "REPLY_PHOTO_FID")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Delivery shows REAL sender identity (not anonymous)
# ─────────────────────────────────────────────────────────────────────────────

class TestDeliveryShowsRealIdentity(unittest.TestCase):
    """Core requirement: delivered reply shows real sender name + username."""

    def setUp(self):
        _boot()

    def test_delivered_header_contains_sender_first_name(self):
        """The delivered message must contain the sender's first name."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_PHOTO", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Great photo!", None, None)

        mock_bot.send_message.assert_called()
        sent_text = mock_bot.send_message.call_args[0][1]
        # Reader's DB name is "Alice" (first_name from upsert_user)
        self.assertIn("Alice", sent_text,
                       "Delivered message must contain real sender first name")

    def test_delivered_header_contains_sender_username(self):
        """The delivered message must contain @username."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_UNAME", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Nice!", None, None)

        sent_text = mock_bot.send_message.call_args[0][1]
        self.assertIn("@alice_id", sent_text,
                       "Delivered message must contain @username")

    def test_delivered_header_is_not_anonymous(self):
        """The delivered message must NOT contain the word 'مجهول' (anonymous)."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_ANON", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Test", None, None)

        sent_text = mock_bot.send_message.call_args[0][1]
        self.assertNotIn("مجهول", sent_text,
                         "Delivered message must NOT be labeled anonymous")

    def test_delivered_header_format(self):
        """Header must follow '💬 رد من:\\nName (@username)' format."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_FMT", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Format test", None, None)

        sent_text = mock_bot.send_message.call_args[0][1]
        self.assertIn("رد من:", sent_text)
        self.assertIn("Alice", sent_text)
        self.assertIn("@alice_id", sent_text)

    def test_delivered_photo_reply_sends_photo_with_header(self):
        """Photo reply sent to sender with real identity in caption."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_PHDEL", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(
            mock_bot, wid, READER, "Check this photo", "photo", "REPLY_PH_FID",
        )

        mock_bot.send_photo.assert_called_once()
        call_args = mock_bot.send_photo.call_args
        self.assertEqual(call_args[0][0], SENDER)  # sent to original sender
        self.assertEqual(call_args[0][1], "REPLY_PH_FID")  # correct file_id
        # Caption is in kwargs
        caption = call_args[1].get("caption", "")
        self.assertIn("Alice", caption,
                       "Photo caption must contain real sender identity")

    def test_delivered_reply_has_reply_button(self):
        """Delivered reply should have a deep-link reply button for reply chain."""
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_BTN", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Test", None, None)

        call_kwargs = mock_bot.send_message.call_args[1]
        kb = call_kwargs.get("reply_markup")
        self.assertIsNotNone(kb)

        found_reply = False
        for row in kb.keyboard:
            for btn in row:
                if btn.url and f"reply_{wid}" in btn.url:
                    found_reply = True
                if btn.callback_data and btn.callback_data.startswith("mwreply:"):
                    found_reply = True
        self.assertTrue(found_reply,
                        "Delivered reply must have a reply button (URL or callback)")

    def test_delivered_video_reply_uses_send_video(self):
        from handlers.media_whispers import _deliver_media_reply

        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="ID_VD", media_type="photo",
        )

        mock_bot = MagicMock()
        _deliver_media_reply(mock_bot, wid, READER, "Video reply", "video", "VD_FID")

        mock_bot.send_video.assert_called_once()
        call_args = mock_bot.send_video.call_args
        self.assertEqual(call_args[0][0], SENDER)
        self.assertEqual(call_args[0][1], "VD_FID")
        caption = call_args[1].get("caption", "")
        self.assertIn("Alice", caption)


# ─────────────────────────────────────────────────────────────────────────────
# 5. handle_media_reply_message end-to-end flow
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleMediaReplyMessage(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_text_reply_delivered_with_real_identity(self):
        wid = create_whisper(
            sender_id=SENDER, content="Secret", whisper_type="everyone",
            message_type="photo", file_id="E2E_PHOTO", media_type="photo",
        )

        bot = MagicMock()
        user_states = {READER: {"action": "pending_media_reply", "whisper_id": wid}}
        msg = _make_text_msg("Thanks for the photo!", sender_id=READER)

        from handlers.media_whispers import handle_media_reply_message
        consumed = handle_media_reply_message(bot, msg, user_states)

        self.assertTrue(consumed)
        self.assertNotIn(READER, user_states, "State should be cleared after reply")

        # Check delivery to original sender (SENDER)
        self.assertTrue(_sent_to(bot, SENDER),
                        "Reply must be delivered to original whisper sender")

        # Check that real identity is in the delivered message
        delivered_text = _last_text_to(bot, SENDER)
        self.assertIn("Alice", delivered_text,
                       "Delivered reply must show real sender name")
        self.assertIn("@alice_id", delivered_text,
                       "Delivered reply must show @username")
        self.assertNotIn("مجهول", delivered_text,
                         "Reply must NOT be labeled anonymous")

        # Check reply saved in DB
        self.assertEqual(count_replies(wid), 1)
        replies = get_replies(wid)
        self.assertEqual(replies[0]["sender_id"], READER)
        self.assertEqual(replies[0]["content"], "Thanks for the photo!")

    def test_photo_reply_delivered_with_real_identity(self):
        wid = create_whisper(
            sender_id=SENDER, content="Photo whisper", whisper_type="everyone",
            message_type="photo", file_id="E2E_PH2", media_type="photo",
        )

        bot = MagicMock()
        user_states = {READER: {"action": "pending_media_reply", "whisper_id": wid}}
        msg = _make_photo_msg(file_id="E2E_REPLY_PHOTO", caption="My photo reply",
                              sender_id=READER)

        from handlers.media_whispers import handle_media_reply_message
        consumed = handle_media_reply_message(bot, msg, user_states)

        self.assertTrue(consumed)

        # Photo delivered to original sender
        bot.send_photo.assert_called()
        call_args = bot.send_photo.call_args
        self.assertEqual(call_args[0][0], SENDER)
        caption = call_args[1].get("caption", "")
        self.assertIn("Alice", caption,
                       "Photo caption must contain real sender identity")
        self.assertNotIn("مجهول", caption, "Must NOT be anonymous")


# ─────────────────────────────────────────────────────────────────────────────
# 6. Access rules unchanged
# ─────────────────────────────────────────────────────────────────────────────

class TestAccessRulesUnchanged(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_first_one_sender_can_always_reply(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="AR_FO", media_type="photo",
        )
        ok, _ = can_reply_to_whisper(wid, SENDER)
        self.assertTrue(ok, "Sender should always be able to reply")

    def test_first_one_reader_can_reply_after_reading(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="AR_FO2", media_type="photo",
        )
        record_whisper_read(wid, READER)
        ok, _ = can_reply_to_whisper(wid, READER)
        self.assertTrue(ok, "Reader should be able to reply after reading")

    def test_first_three_reader_can_reply_before_lock(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="first_three",
            max_readers=3,
            message_type="photo", file_id="AR_FT", media_type="photo",
        )
        upsert_user(40003, "charlie", "Charlie", None)
        record_whisper_read(wid, READER)  # reader 1
        record_whisper_read(wid, 40003)  # reader 2

        ok, _ = can_reply_to_whisper(wid, READER)
        self.assertTrue(ok, "Reader should be able to reply before lock")

    def test_first_three_locks_after_three_readers(self):
        """After 3 readers, first_three whisper auto-locks and replies are blocked."""
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="first_three",
            max_readers=3,
            message_type="photo", file_id="AR_FT2", media_type="photo",
        )
        upsert_user(40003, "charlie", "Charlie", None)
        upsert_user(40004, "dave", "Dave", None)
        record_whisper_read(wid, READER)
        record_whisper_read(wid, 40003)
        record_whisper_read(wid, 40004)  # 3rd reader → auto-lock

        ok, reason = can_reply_to_whisper(wid, READER)
        self.assertFalse(ok, "After 3 readers, whisper is locked")
        self.assertEqual(reason, "whisper_locked")

    def test_everyone_any_reader_can_reply(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="AR_EV", media_type="photo",
        )
        upsert_user(40003, "eve", "Eve", None)
        record_whisper_read(wid, READER)
        record_whisper_read(wid, 40003)

        ok, _ = can_reply_to_whisper(wid, READER)
        self.assertTrue(ok)
        ok, _ = can_reply_to_whisper(wid, 40003)
        self.assertTrue(ok)

    def test_custom_only_target_can_reply(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="custom",
            target_users=[READER],
            message_type="photo", file_id="AR_CU", media_type="photo",
        )
        upsert_user(40003, "intruder", "Intruder", None)
        record_whisper_read(wid, READER)  # target has read

        ok, _ = can_reply_to_whisper(wid, READER)  # target
        self.assertTrue(ok)
        ok, reason = can_reply_to_whisper(wid, 40003)  # not target, not reader
        self.assertFalse(ok)

    def test_destructive_whisper_reply_allowed(self):
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="photo", file_id="AR_DES", media_type="photo",
        )
        record_whisper_read(wid, READER)

        ok, _ = can_reply_to_whisper(wid, READER)
        self.assertTrue(ok)

    def test_locked_whisper_cannot_be_replied_to(self):
        from database import lock_whisper
        wid = create_whisper(
            sender_id=SENDER, content="", whisper_type="everyone",
            message_type="photo", file_id="AR_LOCK", media_type="photo",
        )
        record_whisper_read(wid, READER)
        lock_whisper(wid)

        ok, reason = can_reply_to_whisper(wid, READER)
        self.assertFalse(ok)
        self.assertEqual(reason, "whisper_locked")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _sent_to(bot, user_id):
    """Check if bot sent any message to the given user_id."""
    for call in bot.send_message.call_args_list:
        if call[0] and call[0][0] == user_id:
            return True
    return False


def _last_text_to(bot, user_id):
    """Get the text of the last message sent to user_id."""
    for call in reversed(bot.send_message.call_args_list):
        if call[0] and call[0][0] == user_id:
            return call[0][1] if len(call[0]) > 1 else ""
    return ""


if __name__ == "__main__":
    unittest.main()
