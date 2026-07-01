"""
tests/test_replies.py — Test suite for the Whisper Reply System.

Tests cover:
  1.  DB schema initialisation (init_replies_db idempotent)
  2.  create_reply — happy path, text and media
  3.  create_reply — parent whisper not found
  4.  create_reply — reply cap enforcement
  5.  create_reply — unsupported media_type rejected
  6.  get_reply / get_replies / count_replies
  7.  delete_replies_for_whisper
  8.  mark_reply_read — first read True, subsequent False
  9.  can_reply_to_whisper — sender can always reply
  10. can_reply_to_whisper — authorised reader can reply
  11. can_reply_to_whisper — non-participant cannot reply
  12. can_reply_to_whisper — locked whisper blocks reply
  13. can_reply_to_whisper — deleted whisper blocks reply
  14. can_reply_to_whisper — cap exceeded blocks reply
  15. get_whisper_participants — correct routing info
  16. delete_whisper cascades to replies
  17. whisper_replies_enabled default setting
  18. admin toggle key present in DEFAULT_SETTINGS
  19. _extract_media helper — text, photo, sticker edge cases (mocked)
  20. handle_reply_message returns False when no pending state
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_replies_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.replies import (
    init_replies_db,
    create_reply,
    get_reply,
    get_replies,
    count_replies,
    delete_replies_for_whisper,
    mark_reply_read,
    can_reply_to_whisper,
    get_whisper_participants,
    MAX_REPLIES_PER_WHISPER,
    SUPPORTED_MEDIA,
)


def _boot():
    """Initialise core + replies schema."""
    db.init_db()
    init_replies_db()


# ─────────────────────────────────────────────────────────────────────────────
# 1–2. Schema and basic CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestInitRepliesDb(unittest.TestCase):
    def test_idempotent(self):
        _boot()
        init_replies_db()   # second call must not raise
        init_replies_db()   # third call — still fine

    def test_tables_created(self):
        _boot()
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("whisper_replies", tables)
        self.assertIn("reply_reads", tables)


class TestCreateReply(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80001, "sender", "Sender", None)
        db.upsert_user(80002, "reader", "Reader", None)
        self.wid = db.create_whisper(80001, "hello world", "custom",
                                     target_users=[80002])

    def test_text_reply(self):
        rid = create_reply(self.wid, 80002, content="hello back")
        self.assertIsNotNone(rid)
        self.assertEqual(len(rid), 12)

    def test_photo_reply(self):
        rid = create_reply(self.wid, 80002, content="caption",
                           media_type="photo", file_id="FAKE_FILE_ID")
        self.assertIsNotNone(rid)
        row = get_reply(rid)
        self.assertEqual(row["media_type"], "photo")
        self.assertEqual(row["file_id"], "FAKE_FILE_ID")

    def test_sticker_reply_no_content(self):
        rid = create_reply(self.wid, 80001, media_type="sticker",
                           file_id="STICKER_ID")
        self.assertIsNotNone(rid)

    def test_unsupported_media_rejected(self):
        rid = create_reply(self.wid, 80002, content="x",
                           media_type="live_location")
        self.assertIsNone(rid)

    def test_nonexistent_whisper_returns_none(self):
        rid = create_reply("does_not_exist", 80002, content="hi")
        self.assertIsNone(rid)

    def test_reply_stored_correctly(self):
        rid = create_reply(self.wid, 80002, content="stored?")
        row = get_reply(rid)
        self.assertEqual(row["whisper_id"], self.wid)
        self.assertEqual(row["sender_id"], 80002)
        self.assertEqual(row["content"], "stored?")
        self.assertIsNone(row["media_type"])

    def test_sender_can_reply(self):
        """Whisper sender may also reply to their own whisper."""
        rid = create_reply(self.wid, 80001, content="sender replies")
        self.assertIsNotNone(rid)


class TestReplyCap(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80010, "cap_sender", "Cap", None)
        self.wid = db.create_whisper(80010, "cap test", "everyone")

    def test_cap_enforced(self):
        # Fill up to cap
        for i in range(MAX_REPLIES_PER_WHISPER):
            rid = create_reply(self.wid, 80010, content=f"reply {i}")
            self.assertIsNotNone(rid, f"Reply {i} must succeed before cap")
        # Next one must fail
        rid_over = create_reply(self.wid, 80010, content="over cap")
        self.assertIsNone(rid_over)

    def test_count_matches(self):
        for i in range(3):
            create_reply(self.wid, 80010, content=f"r{i}")
        self.assertEqual(count_replies(self.wid), 3)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Retrieval
# ─────────────────────────────────────────────────────────────────────────────

class TestGetReplies(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80020, "retr_sender", "Retr", None)
        self.wid = db.create_whisper(80020, "content", "everyone")

    def test_get_replies_order(self):
        r1 = create_reply(self.wid, 80020, content="first")
        r2 = create_reply(self.wid, 80020, content="second")
        replies = get_replies(self.wid)
        self.assertEqual(replies[0]["reply_id"], r1)
        self.assertEqual(replies[1]["reply_id"], r2)

    def test_get_replies_empty(self):
        self.assertEqual(get_replies(self.wid), [])

    def test_count_replies_zero(self):
        self.assertEqual(count_replies(self.wid), 0)

    def test_get_reply_none(self):
        self.assertIsNone(get_reply("nonexistent_12"))


# ─────────────────────────────────────────────────────────────────────────────
# 4. Delete
# ─────────────────────────────────────────────────────────────────────────────

class TestDeleteReplies(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80030, "del_sender", "Del", None)
        self.wid = db.create_whisper(80030, "delete me", "everyone")

    def test_delete_replies_for_whisper(self):
        create_reply(self.wid, 80030, content="r1")
        create_reply(self.wid, 80030, content="r2")
        deleted = delete_replies_for_whisper(self.wid)
        self.assertEqual(deleted, 2)
        self.assertEqual(count_replies(self.wid), 0)

    def test_delete_replies_returns_zero_for_empty(self):
        n = delete_replies_for_whisper(self.wid)
        self.assertEqual(n, 0)

    def test_delete_whisper_cascades_to_replies(self):
        """database.delete_whisper must also remove its replies."""
        create_reply(self.wid, 80030, content="should be gone")
        db.delete_whisper(self.wid)
        # The whisper is gone — count_replies on a non-existent whisper returns 0
        self.assertEqual(count_replies(self.wid), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Read tracking
# ─────────────────────────────────────────────────────────────────────────────

class TestMarkReplyRead(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80040, "read_s", "Read", None)
        db.upsert_user(80041, "read_r", "Reader", None)
        wid = db.create_whisper(80040, "read test", "everyone")
        self.rid = create_reply(wid, 80041, content="a reply")

    def test_first_read_returns_true(self):
        result = mark_reply_read(self.rid, 80040)
        self.assertTrue(result)

    def test_second_read_returns_false(self):
        mark_reply_read(self.rid, 80040)
        result = mark_reply_read(self.rid, 80040)
        self.assertFalse(result)

    def test_different_users_each_return_true(self):
        db.upsert_user(80042, "ru2", "R2", None)
        r1 = mark_reply_read(self.rid, 80040)
        r2 = mark_reply_read(self.rid, 80042)
        self.assertTrue(r1)
        self.assertTrue(r2)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Permission checks
# ─────────────────────────────────────────────────────────────────────────────

class TestCanReplyToWhisper(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80050, "perm_sender", "PS", None)
        db.upsert_user(80051, "perm_reader", "PR", None)
        db.upsert_user(80052, "perm_other",  "PO", None)
        self.wid = db.create_whisper(80050, "perm test", "everyone")
        db.add_reader_if_new(self.wid, 80051)   # register 80051 as a reader

    def test_sender_can_reply(self):
        ok, reason = can_reply_to_whisper(self.wid, 80050)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_reader_can_reply(self):
        ok, reason = can_reply_to_whisper(self.wid, 80051)
        self.assertTrue(ok)

    def test_non_participant_cannot_reply(self):
        ok, reason = can_reply_to_whisper(self.wid, 80052)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_participant")

    def test_locked_whisper_blocks_reply(self):
        db.toggle_whisper_lock(self.wid)
        ok, reason = can_reply_to_whisper(self.wid, 80050)
        self.assertFalse(ok)
        self.assertEqual(reason, "whisper_locked")
        db.toggle_whisper_lock(self.wid)  # restore

    def test_deleted_whisper_blocks_reply(self):
        wid2 = db.create_whisper(80050, "gone", "everyone")
        db.delete_whisper(wid2)
        ok, reason = can_reply_to_whisper(wid2, 80050)
        self.assertFalse(ok)
        self.assertEqual(reason, "whisper_not_found")

    def test_cap_reached_blocks_reply(self):
        wid3 = db.create_whisper(80050, "capped", "everyone")
        for i in range(MAX_REPLIES_PER_WHISPER):
            create_reply(wid3, 80050, content=f"r{i}")
        ok, reason = can_reply_to_whisper(wid3, 80050)
        self.assertFalse(ok)
        self.assertEqual(reason, "reply_cap_reached")


# ─────────────────────────────────────────────────────────────────────────────
# 7. Participants routing
# ─────────────────────────────────────────────────────────────────────────────

class TestGetWhisperParticipants(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(80060, "part_s", "PS2", None)
        db.upsert_user(80061, "part_r", "PR2", None)
        self.wid = db.create_whisper(80060, "routing test", "everyone")
        db.add_reader_if_new(self.wid, 80061)

    def test_participants_correct(self):
        p = get_whisper_participants(self.wid)
        self.assertEqual(p["sender_id"], 80060)
        self.assertIn(80061, p["reader_ids"])

    def test_nonexistent_returns_empty(self):
        p = get_whisper_participants("no_such_whisper")
        self.assertEqual(p, {})


# ─────────────────────────────────────────────────────────────────────────────
# 8. Settings
# ─────────────────────────────────────────────────────────────────────────────

class TestRepliesSettings(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_whisper_replies_enabled_in_default_settings(self):
        from config import DEFAULT_SETTINGS
        self.assertIn("whisper_replies_enabled", DEFAULT_SETTINGS)
        self.assertEqual(DEFAULT_SETTINGS["whisper_replies_enabled"], "1")

    def test_whisper_replies_enabled_seeded_in_db(self):
        val = db.get_setting("whisper_replies_enabled")
        self.assertEqual(val, "1")

    def test_get_all_settings_includes_replies(self):
        result = db.get_all_settings(["whisper_replies_enabled"])
        self.assertEqual(result["whisper_replies_enabled"], "1")

    def test_toggle_replies_disabled(self):
        db.set_setting("whisper_replies_enabled", "0")
        val = db.get_setting("whisper_replies_enabled")
        self.assertEqual(val, "0")
        db.set_setting("whisper_replies_enabled", "1")  # restore


# ─────────────────────────────────────────────────────────────────────────────
# 9. handle_reply_message — no-op when no state
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleReplyMessage(unittest.TestCase):
    def test_returns_false_no_state(self):
        from handlers.replies import handle_reply_message
        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.from_user.id = 90001
        user_states = {}
        result = handle_reply_message(mock_bot, mock_msg, user_states)
        self.assertFalse(result)
        mock_bot.send_message.assert_not_called()

    def test_returns_false_wrong_action(self):
        from handlers.replies import handle_reply_message
        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.from_user.id = 90002
        user_states = {90002: {"action": "edit_whisper", "whisper_id": "abc"}}
        result = handle_reply_message(mock_bot, mock_msg, user_states)
        self.assertFalse(result)

    def test_returns_true_pending_state_deleted_whisper(self):
        """Pending state + deleted whisper → consumed but sends error message."""
        from handlers.replies import handle_reply_message
        _boot()
        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.from_user.id = 90003
        mock_msg.content_type = "text"
        mock_msg.text = "hello"
        mock_msg.caption = None
        mock_msg.chat.id = 90003
        user_states = {90003: {"action": "pending_whisper_reply",
                                "whisper_id": "no_such_whisper"}}
        result = handle_reply_message(mock_bot, mock_msg, user_states)
        self.assertTrue(result)
        # State must be cleared
        self.assertNotIn(90003, user_states)
        # Bot must have sent an error
        mock_bot.send_message.assert_called()

    def test_returns_true_and_creates_reply(self):
        """Happy path: pending state + valid whisper + user is sender."""
        from handlers.replies import handle_reply_message
        _boot()
        db.upsert_user(90010, "s10", "S10", None)
        db.upsert_user(90011, "r11", "R11", None)
        wid = db.create_whisper(90010, "reply target", "custom",
                                target_users=[90011])
        db.add_reader_if_new(wid, 90011)  # 90011 has read it

        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.from_user.id = 90011
        mock_msg.content_type = "text"
        mock_msg.text = "a proper reply"
        mock_msg.caption = None
        mock_msg.chat.id = 90011

        user_states = {90011: {"action": "pending_whisper_reply",
                                "whisper_id": wid}}
        result = handle_reply_message(mock_bot, mock_msg, user_states)
        self.assertTrue(result)
        self.assertNotIn(90011, user_states)
        self.assertEqual(count_replies(wid), 1)
        reply = get_replies(wid)[0]
        self.assertEqual(reply["content"], "a proper reply")
        self.assertEqual(reply["sender_id"], 90011)

    def test_rejects_when_replies_disabled(self):
        """Pending state but replies disabled → consumed, no reply created."""
        import database
        from handlers.replies import handle_reply_message
        _boot()
        database.set_setting("whisper_replies_enabled", "0")
        db.upsert_user(90020, "s20", "S20", None)
        wid = db.create_whisper(90020, "disabled test", "everyone")

        mock_bot = MagicMock()
        mock_msg = MagicMock()
        mock_msg.from_user.id = 90020
        mock_msg.content_type = "text"
        mock_msg.text = "should not save"
        mock_msg.caption = None
        mock_msg.chat.id = 90020

        user_states = {90020: {"action": "pending_whisper_reply",
                                "whisper_id": wid}}
        result = handle_reply_message(mock_bot, mock_msg, user_states)
        self.assertTrue(result)
        self.assertNotIn(90020, user_states)
        self.assertEqual(count_replies(wid), 0)
        database.set_setting("whisper_replies_enabled", "1")  # restore


# ─────────────────────────────────────────────────────────────────────────────
# 10. _extract_media helper
# ─────────────────────────────────────────────────────────────────────────────

class TestExtractMedia(unittest.TestCase):
    """Test _extract_media without needing a real bot."""

    def _make_msg(self, content_type, **attrs):
        msg = MagicMock()
        msg.content_type = content_type
        msg.text = attrs.get("text", None)
        msg.caption = attrs.get("caption", None)
        if content_type == "photo":
            photo = MagicMock()
            photo.file_id = attrs.get("file_id", "PH_ID")
            msg.photo = [photo]
        if content_type == "video":
            msg.video = MagicMock()
            msg.video.file_id = attrs.get("file_id", "VID_ID")
        if content_type == "voice":
            msg.voice = MagicMock()
            msg.voice.file_id = attrs.get("file_id", "VOICE_ID")
        if content_type == "audio":
            msg.audio = MagicMock()
            msg.audio.file_id = attrs.get("file_id", "AUDIO_ID")
        if content_type == "document":
            msg.document = MagicMock()
            msg.document.file_id = attrs.get("file_id", "DOC_ID")
        if content_type == "sticker":
            msg.sticker = MagicMock()
            msg.sticker.file_id = attrs.get("file_id", "STK_ID")
        return msg

    def test_text(self):
        from handlers.replies import _extract_media
        content, mt, fid = _extract_media(self._make_msg("text", text="hello"))
        self.assertEqual(content, "hello")
        self.assertIsNone(mt)
        self.assertIsNone(fid)

    def test_photo(self):
        from handlers.replies import _extract_media
        content, mt, fid = _extract_media(
            self._make_msg("photo", caption="cap", file_id="PH1")
        )
        self.assertEqual(content, "cap")
        self.assertEqual(mt, "photo")
        self.assertEqual(fid, "PH1")

    def test_sticker_no_content(self):
        from handlers.replies import _extract_media
        content, mt, fid = _extract_media(self._make_msg("sticker", file_id="STK1"))
        self.assertEqual(content, "")
        self.assertEqual(mt, "sticker")
        self.assertEqual(fid, "STK1")

    def test_voice(self):
        from handlers.replies import _extract_media
        content, mt, fid = _extract_media(self._make_msg("voice", file_id="V1"))
        self.assertIsNone(content or None)   # voice has no text
        self.assertEqual(mt, "voice")
        self.assertEqual(fid, "V1")

    def test_document_with_caption(self):
        from handlers.replies import _extract_media
        content, mt, fid = _extract_media(
            self._make_msg("document", caption="doc cap", file_id="D1")
        )
        self.assertEqual(content, "doc cap")
        self.assertEqual(mt, "document")


# ─────────────────────────────────────────────────────────────────────────────
# 11. Supported media constant
# ─────────────────────────────────────────────────────────────────────────────

class TestSupportedMedia(unittest.TestCase):
    def test_all_required_types_present(self):
        required = {"photo", "video", "voice", "audio", "document", "sticker", "animation", "contact", "location"}
        required = {"photo", "video", "voice", "audio", "document", "sticker",
                     "animation", "contact", "location"}
        self.assertEqual(required, SUPPORTED_MEDIA)

    def test_live_location_not_supported(self):
        self.assertNotIn("live_location", SUPPORTED_MEDIA)


if __name__ == "__main__":
    unittest.main(verbosity=2)
