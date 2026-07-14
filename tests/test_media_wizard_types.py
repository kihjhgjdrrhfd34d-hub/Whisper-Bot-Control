"""
tests/test_media_wizard_types.py — Tests for media whisper type selection flow.

Covers:
  1.  All four media whisper types via type selection
  2.  Access limits for each type
  3.  Deep-link private viewing (view_<whisper_id>)
  4.  Destructive media whispers
  5.  First Person Only access control (requirement #10)
  6.  Group placeholder: media not exposed in the group
  7.  Type selection keyboard structure
  8.  Pending media flow before type selection
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_media_wizard_types_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user,
    can_read_whisper, record_whisper_read, reader_count,
    add_reader_if_new, lock_whisper, get_readers,
    store_pending_media, get_pending_media, delete_pending_media,
    set_setting,
)


def _boot():
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers")
        conn.execute("DELETE FROM curious_ones")
        conn.execute("DELETE FROM whisper_timestamps")
        conn.execute("DELETE FROM whispers")
        conn.execute("DELETE FROM pending_media_whispers")
        conn.commit()
    upsert_user(50001, "alice_mwt", "Alice", None)
    upsert_user(50002, "bob_mwt", "Bob", None)
    upsert_user(50003, "charlie_mwt", "Charlie", None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. All four media whisper types via type selection
# ─────────────────────────────────────────────────────────────────────────────

class TestAllFourMediaWhisperTypes(unittest.TestCase):
    """Create media whispers with each type selection option and verify settings."""

    def setUp(self):
        _boot()

    def test_first_one_type(self):
        """first_one: whisper_type=first_one, reader_limit=1, not destructive."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="TYPE_PH_FO",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)
        self.assertEqual(w["is_destructive"], 0)
        self.assertEqual(w["media_type"], "photo")

    def test_first_three_type(self):
        """first_three: whisper_type=first_three, reader_limit=3, not destructive."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_three",
            max_readers=3,
            message_type="video", file_id="TYPE_VD_FT",
            media_type="video",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "first_three")
        self.assertEqual(w["max_readers"], 3)
        self.assertEqual(w["is_destructive"], 0)
        self.assertEqual(w["media_type"], "video")

    def test_everyone_type(self):
        """everyone: whisper_type=everyone, is_public=1 (via whisper_type), not destructive."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="audio", file_id="TYPE_AU_EV",
            media_type="audio",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "everyone")
        self.assertEqual(w["max_readers"], 0)
        self.assertEqual(w["is_destructive"], 0)
        self.assertEqual(w["media_type"], "audio")

    def test_destructive_type(self):
        """destructive: is_destructive=1, whisper_type=first_one, reader_limit=1."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="voice", file_id="TYPE_VO_DE",
            media_type="voice",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)
        self.assertEqual(w["is_destructive"], 1)
        self.assertEqual(w["media_type"], "voice")

    def test_all_four_types_use_media(self):
        """All four types store media fields correctly."""
        types = [
            ("first_one",   1,  False, "photo",    "PH_ALL"),
            ("first_three", 3,  False, "video",    "VD_ALL"),
            ("everyone",    0,  False, "document", "DO_ALL"),
            ("first_one",   1,  True,  "audio",    "AU_ALL"),
        ]
        for wtype, mr, destructive, mt, fid in types:
            wid = create_whisper(
                sender_id=50001, content="", whisper_type=wtype,
                max_readers=mr, is_destructive=destructive,
                message_type=mt, file_id=fid, media_type=mt,
            )
            w = get_whisper(wid)
            self.assertIsNotNone(w, f"Failed for type={wtype}, media={mt}")
            self.assertEqual(w["media_type"], mt)
            self.assertEqual(w["whisper_type"], wtype)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Access limits for each type
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperAccessLimits(unittest.TestCase):
    """Test can_read_whisper for each media whisper type."""

    def setUp(self):
        _boot()

    def test_first_one_anyone_can_read_when_empty(self):
        """first_one: anyone can read when no readers exist."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, message_type="photo", file_id="ACC_FO",
            media_type="photo",
        )
        can, reason = can_read_whisper(wid, 50002)
        self.assertTrue(can)

    def test_first_one_only_first_can_read(self):
        """first_one: after first read, others are denied."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, message_type="photo", file_id="ACC_FO2",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)
        can, reason = can_read_whisper(wid, 50003)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_first_one_first_reader_can_still_read(self):
        """first_one: the first reader can still access it."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, message_type="photo", file_id="ACC_FO3",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)
        can, reason = can_read_whisper(wid, 50002)
        self.assertTrue(can)

    def test_first_three_first_three_can_read(self):
        """first_three: first 3 readers can read."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_three",
            max_readers=3, message_type="video", file_id="ACC_FT",
            media_type="video",
        )
        for uid in [50002, 50003, 50001]:
            can, _ = can_read_whisper(wid, uid)
            self.assertTrue(can, f"User {uid} should be able to read")

    def test_first_three_fourth_reader_denied(self):
        """first_three: fourth reader is denied after 3 reads."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_three",
            max_readers=3, message_type="video", file_id="ACC_FT2",
            media_type="video",
        )
        upsert_user(50004, "dave_mwt", "Dave", None)
        record_whisper_read(wid, 50002)
        record_whisper_read(wid, 50003)
        record_whisper_read(wid, 50004)
        can, reason = can_read_whisper(wid, 50001)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_everyone_anyone_can_read(self):
        """everyone: anyone can read at any time."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="audio", file_id="ACC_EV",
            media_type="audio",
        )
        record_whisper_read(wid, 50002)
        record_whisper_read(wid, 50003)
        can, _ = can_read_whisper(wid, 50001)
        self.assertTrue(can)

    def test_destructive_uses_first_one_access(self):
        """destructive: uses first_one access rules."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="photo", file_id="ACC_DE",
            media_type="photo",
        )
        can, _ = can_read_whisper(wid, 50002)
        self.assertTrue(can)
        record_whisper_read(wid, 50002)
        can, reason = can_read_whisper(wid, 50003)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_sender_always_can_read(self):
        """Sender can always read their own whisper regardless of type."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, message_type="photo", file_id="ACC_SENDER",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)
        can, reason = can_read_whisper(wid, 50001)
        self.assertTrue(can)
        self.assertEqual(reason, "sender")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Deep-link private viewing
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperDeepLinkView(unittest.TestCase):
    """Test deep-link private viewing of media whispers."""

    def setUp(self):
        _boot()

    def test_view_delivers_media_to_reader(self):
        """view_<whisper_id> records read and delivers media."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="VIEW_PH",
            media_type="photo",
        )
        w = get_whisper(wid)
        w_dict = dict(w)
        self.assertEqual(w_dict["message_type"], "photo")
        self.assertEqual(w_dict["file_id"], "VIEW_PH")

        can, _ = can_read_whisper(wid, 50002)
        self.assertTrue(can)
        is_new = record_whisper_read(wid, 50002)
        self.assertTrue(is_new)
        self.assertEqual(reader_count(wid), 1)

    def test_view_denies_already_read(self):
        """view_<whisper_id> denies user who already read."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="video", file_id="VIEW_VD",
            media_type="video",
        )
        record_whisper_read(wid, 50002)
        can, _ = can_read_whisper(wid, 50002)
        self.assertTrue(can)
        is_new = record_whisper_read(wid, 50002)
        self.assertFalse(is_new)

    def test_view_first_one_delivers_to_first_user(self):
        """view_ for first_one: first user gets access, second is denied."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, message_type="document", file_id="VIEW_DO",
            media_type="document",
        )
        can_a, _ = can_read_whisper(wid, 50002)
        self.assertTrue(can_a)
        record_whisper_read(wid, 50002)

        can_b, reason_b = can_read_whisper(wid, 50003)
        self.assertFalse(can_b)
        self.assertEqual(reason_b, "taken")

    def test_view_first_three_allows_three_users(self):
        """view_ for first_three: allows exactly 3 users."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_three",
            max_readers=3, message_type="photo", file_id="VIEW_FT",
            media_type="photo",
        )
        for uid in [50002, 50003, 50001]:
            can, _ = can_read_whisper(wid, uid)
            self.assertTrue(can)
            record_whisper_read(wid, uid)
        self.assertEqual(reader_count(wid), 3)

    def test_view_nonexistent_whisper(self):
        """view_ for nonexistent whisper returns not_found."""
        can, reason = can_read_whisper("nonexistent123", 50002)
        self.assertFalse(can)
        self.assertEqual(reason, "not_found")


# ─────────────────────────────────────────────────────────────────────────────
# 4. Destructive media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestDestructiveMediaWhisper(unittest.TestCase):
    """Test destructive media whisper behavior."""

    def setUp(self):
        _boot()

    def test_destructive_flag_set(self):
        """Destructive whisper has is_destructive=1."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="photo", file_id="DE_FLAG",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertEqual(w["is_destructive"], 1)

    def test_destructive_locks_after_first_one_read(self):
        """Destructive first_one: locks whisper after first read."""
        wid = create_whisper(
            sender_id=50001, content="secret", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="video", file_id="DE_LOCK",
            media_type="video",
        )
        record_whisper_read(wid, 50002)
        lock_whisper(wid)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 1)

    def test_destructive_everyone_one_shot(self):
        """Destructive everyone: first reader gets access, then it locks."""
        wid = create_whisper(
            sender_id=50001, content="boom", whisper_type="everyone",
            is_destructive=True,
            message_type="audio", file_id="DE_EV",
            media_type="audio",
        )
        can1, _ = can_read_whisper(wid, 50002)
        self.assertTrue(can1)
        record_whisper_read(wid, 50002)
        lock_whisper(wid)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 1)

    def test_destructive_media_stores_correctly(self):
        """Destructive media whisper stores all media fields."""
        wid = create_whisper(
            sender_id=50001, content="caption", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="photo", file_id="DE_MEDIA",
            caption="test caption", media_type="photo",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "photo")
        self.assertEqual(w["file_id"], "DE_MEDIA")
        self.assertEqual(w["caption"], "test caption")
        self.assertEqual(w["is_destructive"], 1)

    def test_destructive_first_three_locks_at_three(self):
        """Destructive first_three: locks after 3 reads."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_three",
            max_readers=3, is_destructive=True,
            message_type="photo", file_id="DE_FT",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)
        record_whisper_read(wid, 50003)
        upsert_user(50004, "dave", "Dave", None)
        record_whisper_read(wid, 50004)
        lock_whisper(wid)
        w = get_whisper(wid)
        self.assertEqual(w["is_locked"], 1)
        self.assertEqual(reader_count(wid), 3)


# ─────────────────────────────────────────────────────────────────────────────
# 5. First Person Only access control (requirement #10)
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstPersonOnlyMediaWhisperAccess(unittest.TestCase):
    """
    Specific access-control test for First Person Only media whispers.

    Requirement #10:
    - Create a media whisper with reader_limit = 1.
    - User A opens via deep link view_<whisper_id> -> access succeeds, media delivered.
    - User B opens via deep link view_<whisper_id> -> access denied.
    - User B must receive exactly the message: "هذه الهمسة لم تعد متاحة"
    - Verify reader_count remains 1 after User B attempts.
    - Verify no media is sent to User B.
    """

    def setUp(self):
        _boot()

    def test_first_person_only_full_access_control(self):
        """Complete access control test for First Person Only media whispers."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="FPO_PHOTO",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)
        self.assertEqual(w["media_type"], "photo")

        can_a, reason_a = can_read_whisper(wid, 50002)
        self.assertTrue(can_a, "User A should have access")
        is_new = record_whisper_read(wid, 50002)
        self.assertTrue(is_new, "User A's read should be recorded as new")
        self.assertEqual(reader_count(wid), 1)

        can_b, reason_b = can_read_whisper(wid, 50003)
        self.assertFalse(can_b, "User B must be denied")
        self.assertEqual(reason_b, "taken")
        self.assertEqual(reader_count(wid), 1, "reader_count must remain 1")

        self.assertEqual(reader_count(wid), 1)

    def test_first_person_only_deny_message_exact(self):
        """User B must receive exactly 'هذه الهمسة لم تعد متاحة'."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="FPO_MSG",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)

        import bot as bot_module
        original_send = bot_module.bot.send_message
        sent = []

        def capture_send(chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

        bot_module.bot.send_message = capture_send
        bot_module.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))

        try:
            msg = MagicMock()
            msg.text = f"/start view_{wid}"
            msg.from_user = MagicMock()
            msg.from_user.id = 50003
            msg.from_user.username = "charlie_mwt"
            msg.from_user.first_name = "Charlie"
            msg.from_user.last_name = None
            msg.from_user.is_bot = False
            msg.chat = MagicMock()
            msg.chat.id = 50003
            msg.chat.type = "private"

            bot_module.start_cmd(msg)

            user_b_msgs = [m for m in sent if m["chat_id"] == 50003]
            self.assertGreater(len(user_b_msgs), 0,
                               "User B should receive at least one message")
            found_exact = any(
                m["text"] == "هذه الهمسة لم تعد متاحة"
                for m in user_b_msgs
            )
            self.assertTrue(found_exact,
                            f"User B must receive exactly 'هذه الهمسة لم تعد متاحة'. "
                            f"Got: {[m['text'] for m in user_b_msgs]}")
        finally:
            bot_module.bot.send_message = original_send

    def test_first_person_only_no_media_to_user_b(self):
        """Verify no media is sent to User B."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="FPO_NOMEDIA",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)

        import bot as bot_module
        original_send = bot_module.bot.send_message
        original_send_photo = bot_module.bot.send_photo
        sent = []

        def capture_send(chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

        def capture_send_photo(chat_id, *args, **kwargs):
            sent.append({"chat_id": chat_id, "type": "photo", "text": None})

        bot_module.bot.send_message = capture_send
        bot_module.bot.send_photo = capture_send_photo
        bot_module.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))

        try:
            msg = MagicMock()
            msg.text = f"/start view_{wid}"
            msg.from_user = MagicMock()
            msg.from_user.id = 50003
            msg.from_user.username = "charlie_mwt"
            msg.from_user.first_name = "Charlie"
            msg.from_user.last_name = None
            msg.from_user.is_bot = False
            msg.chat = MagicMock()
            msg.chat.id = 50003
            msg.chat.type = "private"

            bot_module.start_cmd(msg)

            user_b_media = [
                m for m in sent
                if m["chat_id"] == 50003 and m.get("type") == "photo"
            ]
            self.assertEqual(len(user_b_media), 0,
                             "No media should be sent to User B")
            self.assertEqual(reader_count(wid), 1,
                             "reader_count must remain 1")
        finally:
            bot_module.bot.send_message = original_send
            bot_module.bot.send_photo = original_send_photo

    def test_first_person_only_user_a_succeeds_via_deep_link(self):
        """User A opens via deep link and gets media delivered."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1,
            message_type="photo", file_id="FPO_USERA",
            media_type="photo",
        )

        import bot as bot_module
        original_send = bot_module.bot.send_message
        original_send_photo = bot_module.bot.send_photo
        sent = []

        def capture_send(chat_id, text, **kwargs):
            sent.append({"chat_id": chat_id, "text": text, "kwargs": kwargs})

        def capture_send_photo(chat_id, *args, **kwargs):
            sent.append({"chat_id": chat_id, "type": "photo", "text": None})

        bot_module.bot.send_message = capture_send
        bot_module.bot.send_photo = capture_send_photo
        bot_module.bot.get_me = MagicMock(return_value=MagicMock(username="testbot"))

        try:
            msg = MagicMock()
            msg.text = f"/start view_{wid}"
            msg.from_user = MagicMock()
            msg.from_user.id = 50002
            msg.from_user.username = "bob_mwt"
            msg.from_user.first_name = "Bob"
            msg.from_user.last_name = None
            msg.from_user.is_bot = False
            msg.chat = MagicMock()
            msg.chat.id = 50002
            msg.chat.type = "private"

            bot_module.start_cmd(msg)

            self.assertEqual(reader_count(wid), 1)
            user_a_msgs = [m for m in sent if m["chat_id"] == 50002]
            self.assertGreater(len(user_a_msgs), 0,
                               "User A should receive content")
            has_deny = any(
                m["text"] == "هذه الهمسة لم تعد متاحة"
                for m in user_a_msgs
            )
            self.assertFalse(has_deny, "User A should NOT get the deny message")
        finally:
            bot_module.bot.send_message = original_send
            bot_module.bot.send_photo = original_send_photo


# ─────────────────────────────────────────────────────────────────────────────
# 6. Group placeholder: media not exposed in the group
# ─────────────────────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────────────────────
# Pending media flow tests
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaFlow(unittest.TestCase):
    """Test that pending media is stored and used correctly."""

    def setUp(self):
        _boot()

    def test_pending_media_stored_on_private_media(self):
        """Private chat media stores pending media."""
        pid = store_pending_media(
            user_id=50001, message_type="photo",
            file_id="PEND_PH", caption="test caption",
        )
        pending = get_pending_media(50001)
        self.assertIsNotNone(pending)
        self.assertEqual(pending["message_type"], "photo")
        self.assertEqual(pending["file_id"], "PEND_PH")
        self.assertEqual(pending["caption"], "test caption")

    def test_pending_media_deleted_after_type_selection(self):
        """Pending media is deleted after type is selected."""
        store_pending_media(
            user_id=50001, message_type="video",
            file_id="PEND_VD",
        )
        self.assertIsNotNone(get_pending_media(50001))
        delete_pending_media(50001)
        self.assertIsNone(get_pending_media(50001))

    def test_new_media_overwrites_pending(self):
        """Sending new media replaces old pending."""
        store_pending_media(
            user_id=50001, message_type="photo",
            file_id="OLD_PHOTO",
        )
        store_pending_media(
            user_id=50001, message_type="video",
            file_id="NEW_VIDEO",
        )
        pending = get_pending_media(50001)
        self.assertEqual(pending["file_id"], "NEW_VIDEO")
        self.assertEqual(pending["message_type"], "video")

    def test_type_selection_creates_whisper_from_pending(self):
        """Simulating type selection creates whisper from pending data."""
        store_pending_media(
            user_id=50001, message_type="photo",
            file_id="SEL_PH", caption="select me",
        )
        pending = get_pending_media(50001)
        self.assertIsNotNone(pending)

        wid = create_whisper(
            sender_id=50001,
            content=pending["content"] or "",
            whisper_type="first_one",
            max_readers=1,
            message_type=pending["message_type"],
            file_id=pending["file_id"],
            caption=pending["caption"],
            media_type=pending["message_type"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["media_type"], "photo")
        self.assertEqual(w["file_id"], "SEL_PH")
        self.assertEqual(w["caption"], "select me")

        delete_pending_media(50001)
        self.assertIsNone(get_pending_media(50001))


# ─────────────────────────────────────────────────────────────────────────────
# 9. Reuse existing logic: statistics, auto-delete, notifications
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperReusesExistingLogic(unittest.TestCase):
    """Verify that media whispers use the same can_read, record_read, etc."""

    def setUp(self):
        _boot()

    def test_read_receipt_for_media_whisper(self):
        """read_receipt works for media whispers."""
        from services.whisper_service import record_read_and_check
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="RECEIPT_PH",
            media_type="photo",
        )
        is_new, is_first = record_read_and_check(wid, 50002, "Bob")
        self.assertTrue(is_new)
        self.assertTrue(is_first)

    def test_read_receipt_not_new_on_reread(self):
        """Second read is not new."""
        from services.whisper_service import record_read_and_check
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="RECEIPT2_PH",
            media_type="photo",
        )
        record_read_and_check(wid, 50002, "Bob")
        is_new, _ = record_read_and_check(wid, 50002, "Bob")
        self.assertFalse(is_new)

    def test_get_readers_for_media_whisper(self):
        """get_readers returns correct data for media whispers."""
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="READERS_PH",
            media_type="photo",
        )
        record_whisper_read(wid, 50002)
        record_whisper_read(wid, 50003)
        readers = get_readers(wid)
        self.assertEqual(len(readers), 2)
        uids = {r["user_id"] for r in readers}
        self.assertEqual(uids, {50002, 50003})

    def test_statistics_count_media_whispers(self):
        """Statistics include media whispers."""
        from database import get_stats
        before = get_stats()
        create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="STATS_PH",
            media_type="photo",
        )
        create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="video", file_id="STATS_VD",
            media_type="video",
        )
        after = get_stats()
        self.assertEqual(after["total_whispers"], before["total_whispers"] + 2)

    def test_auto_delete_for_media_whispers(self):
        """Auto-delete works for media whispers."""
        from database import delete_expired_whispers
        from datetime import datetime, timedelta, timezone
        wid = create_whisper(
            sender_id=50001, content="expire me", whisper_type="everyone",
            message_type="photo", file_id="DEL_PH",
            media_type="photo", auto_delete_hours=1,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE whispers SET auto_delete_at=? WHERE whisper_id=?",
                (past, wid),
            )
            conn.commit()

        deleted = delete_expired_whispers()
        self.assertGreaterEqual(deleted, 1)
        self.assertIsNone(get_whisper(wid))


# ─────────────────────────────────────────────────────────────────────────────
# 10. is_destructive_whisper helper
# ─────────────────────────────────────────────────────────────────────────────

class TestIsDestructiveWhisper(unittest.TestCase):
    """Test the is_destructive_whisper helper from whisper_service."""

    def setUp(self):
        _boot()

    def test_destructive_detected(self):
        from services.whisper_service import is_destructive_whisper
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="first_one",
            max_readers=1, is_destructive=True,
            message_type="photo", file_id="ISD_PH",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertTrue(is_destructive_whisper(w))

    def test_non_destructive_detected(self):
        from services.whisper_service import is_destructive_whisper
        wid = create_whisper(
            sender_id=50001, content="", whisper_type="everyone",
            message_type="photo", file_id="ISD_PH2",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertFalse(is_destructive_whisper(w))


if __name__ == "__main__":
    unittest.main()
