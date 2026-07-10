"""
tests/test_media.py — Comprehensive tests for v2.1.0 media whisper support.

Covers:
  1.  DB schema: media columns exist after migration
  2.  create_whisper with media parameters
  3.  create_whisper without media (backward compat)
  4.  get_whisper returns media fields
  5.  Media extraction from messages (all types)
  6.  send_media_message (all types + text fallback)
  7.  Media whisper auto-delete
  8.  Statistics include media whispers
  9.  Dashboard shows media type
  10. Media in conversation view
  11. All 6 required message types: text, photo, video, audio, voice, document, location
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_media_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import create_whisper, get_whisper, upsert_user, get_stats, get_user_stats


def _boot():
    db.init_db()
    upsert_user(10001, "alice", "Alice", None)
    upsert_user(10002, "bob", "Bob", None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. DB schema: media columns exist after migration
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaColumnsExist(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_whispers_table_has_message_type(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("message_type", cols)

    def test_whispers_table_has_file_id(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("file_id", cols)

    def test_whispers_table_has_caption(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("caption", cols)

    def test_whispers_table_has_location_lat(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("location_lat", cols)

    def test_whispers_table_has_location_lon(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("location_lon", cols)


# ─────────────────────────────────────────────────────────────────────────────
# 2. create_whisper with media parameters
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateWhisperWithMedia(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_photo_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="Check this out", whisper_type="everyone",
            message_type="photo", file_id="AgACAgIAAx",
            caption="A nice photo",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["message_type"], "photo")
        self.assertEqual(w["file_id"], "AgACAgIAAx")
        self.assertEqual(w["caption"], "A nice photo")
        self.assertIsNone(w["location_lat"])
        self.assertIsNone(w["location_lon"])

    def test_video_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="Video time", whisper_type="first_one",
            message_type="video", file_id="AwACAgIAAx",
            caption="Funny video",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "video")
        self.assertEqual(w["file_id"], "AwACAgIAAx")
        self.assertEqual(w["caption"], "Funny video")

    def test_audio_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="audio", file_id="AgACAgIAAudio",
            caption="My song",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "audio")
        self.assertEqual(w["file_id"], "AgACAgIAAudio")
        self.assertEqual(w["caption"], "My song")

    def test_voice_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="first_one",
            message_type="voice", file_id="AwACAgIAVoice",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "voice")
        self.assertEqual(w["file_id"], "AwACAgIAVoice")

    def test_document_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="Important file", whisper_type="custom",
            target_users=[10002],
            message_type="document", file_id="BwACAgIAAug",
            caption="Secret document",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "document")
        self.assertEqual(w["file_id"], "BwACAgIAAug")
        self.assertEqual(w["caption"], "Secret document")

    def test_location_whisper(self):
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="location",
            file_id=json.dumps({"latitude": 33.3128, "longitude": 44.3615}),
            location_lat=33.3128,
            location_lon=44.3615,
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "location")
        self.assertAlmostEqual(w["location_lat"], 33.3128)
        self.assertAlmostEqual(w["location_lon"], 44.3615)


# ─────────────────────────────────────────────────────────────────────────────
# 3. create_whisper without media (backward compatibility)
# ─────────────────────────────────────────────────────────────────────────────

class TestCreateWhisperTextOnly(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_text_whisper_no_media_fields(self):
        wid = create_whisper(
            sender_id=10001, content="Hello!", whisper_type="everyone",
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertIsNone(w["message_type"])
        self.assertIsNone(w["file_id"])
        self.assertIsNone(w["caption"])
        self.assertIsNone(w["location_lat"])
        self.assertIsNone(w["location_lon"])

    def test_text_whisper_explicit_none_media(self):
        wid = create_whisper(
            sender_id=10001, content="Test", whisper_type="first_one",
            message_type=None, file_id=None, caption=None,
            location_lat=None, location_lon=None,
        )
        w = get_whisper(wid)
        self.assertIsNone(w["message_type"])
        self.assertIsNone(w["file_id"])


# ─────────────────────────────────────────────────────────────────────────────
# 4. Media extraction from messages (all types)
# ─────────────────────────────────────────────────────────────────────────────

def _make_msg(content_type, **kwargs):
    """Create a mock Telegram message with the given content type."""
    msg = MagicMock()
    msg.content_type = content_type

    if content_type == "text":
        msg.text = kwargs.get("text", "Hello")
        msg.caption = None
    elif content_type == "photo":
        photo = MagicMock()
        photo.file_id = kwargs.get("file_id", "PHOTO_FILE_ID")
        msg.photo = [MagicMock(), photo]  # last element is largest
        msg.caption = kwargs.get("caption", "")
    elif content_type == "video":
        msg.video = MagicMock()
        msg.video.file_id = kwargs.get("file_id", "VIDEO_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "voice":
        msg.voice = MagicMock()
        msg.voice.file_id = kwargs.get("file_id", "VOICE_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "audio":
        msg.audio = MagicMock()
        msg.audio.file_id = kwargs.get("file_id", "AUDIO_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "document":
        msg.document = MagicMock()
        msg.document.file_id = kwargs.get("file_id", "DOC_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "location":
        msg.location = MagicMock()
        msg.location.latitude = kwargs.get("latitude", 40.7128)
        msg.location.longitude = kwargs.get("longitude", -74.0060)
    elif content_type == "sticker":
        msg.sticker = MagicMock()
        msg.sticker.file_id = kwargs.get("file_id", "STICKER_FILE_ID")
    elif content_type == "animation":
        msg.animation = MagicMock()
        msg.animation.file_id = kwargs.get("file_id", "ANIM_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "contact":
        msg.contact = MagicMock()
        msg.contact.phone_number = kwargs.get("phone", "+1234567890")
        msg.contact.first_name = kwargs.get("first_name", "John")
        msg.contact.last_name = kwargs.get("last_name", "Doe")

    return msg


class TestExtractMediaFromMessage(unittest.TestCase):
    """Test services/media.py extract_media_from_message helper."""

    def setUp(self):
        from services.media import extract_media_from_message
        self.extract = extract_media_from_message

    def test_text_message(self):
        msg = _make_msg("text", text="Hello world")
        result = self.extract(msg)
        self.assertEqual(result["content"], "Hello world")
        self.assertIsNone(result["message_type"])
        self.assertIsNone(result["file_id"])

    def test_photo_message(self):
        msg = _make_msg("photo", file_id="PHOTO_123", caption="Nice pic")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "photo")
        self.assertEqual(result["file_id"], "PHOTO_123")
        self.assertEqual(result["caption"], "Nice pic")

    def test_photo_no_caption(self):
        msg = _make_msg("photo", file_id="PHOTO_456", caption="")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "photo")
        self.assertEqual(result["file_id"], "PHOTO_456")
        self.assertEqual(result["caption"], "")

    def test_video_message(self):
        msg = _make_msg("video", file_id="VIDEO_789", caption="Cool video")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "video")
        self.assertEqual(result["file_id"], "VIDEO_789")
        self.assertEqual(result["caption"], "Cool video")

    def test_voice_message(self):
        msg = _make_msg("voice", file_id="VOICE_ABC")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "voice")
        self.assertEqual(result["file_id"], "VOICE_ABC")

    def test_audio_message(self):
        msg = _make_msg("audio", file_id="AUDIO_DEF", caption="Song title")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "audio")
        self.assertEqual(result["file_id"], "AUDIO_DEF")
        self.assertEqual(result["caption"], "Song title")

    def test_document_message(self):
        msg = _make_msg("document", file_id="DOC_GHI", caption="File notes")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "document")
        self.assertEqual(result["file_id"], "DOC_GHI")
        self.assertEqual(result["caption"], "File notes")

    def test_location_message(self):
        msg = _make_msg("location", latitude=33.3128, longitude=44.3615)
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "location")
        self.assertAlmostEqual(result["location_lat"], 33.3128)
        self.assertAlmostEqual(result["location_lon"], 44.3615)
        self.assertIn("latitude", json.loads(result["file_id"]))

    def test_sticker_not_whisper_media(self):
        msg = _make_msg("sticker")
        result = self.extract(msg)
        self.assertIsNone(result["message_type"])
        self.assertIsNone(result["file_id"])

    def test_animation_not_whisper_media(self):
        msg = _make_msg("animation", caption="funny")
        result = self.extract(msg)
        self.assertIsNone(result["message_type"])

    def test_contact_not_whisper_media(self):
        msg = _make_msg("contact")
        result = self.extract(msg)
        self.assertIsNone(result["message_type"])


# ─────────────────────────────────────────────────────────────────────────────
# 5. send_media_message helper
# ─────────────────────────────────────────────────────────────────────────────

class TestSendMediaMessage(unittest.TestCase):
    def setUp(self):
        from services.media import send_media_message
        self.send = send_media_message
        self.bot = MagicMock()

    def test_send_photo(self):
        data = {"message_type": "photo", "file_id": "PHOTO_ID", "caption": "Hi"}
        result = self.send(self.bot, 100, data, text="fallback")
        self.assertTrue(result)
        self.bot.send_photo.assert_called_once()

    def test_send_video(self):
        data = {"message_type": "video", "file_id": "VIDEO_ID", "caption": "Watch"}
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_video.assert_called_once()

    def test_send_voice(self):
        data = {"message_type": "voice", "file_id": "VOICE_ID", "caption": "Listen"}
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_voice.assert_called_once()

    def test_send_audio(self):
        data = {"message_type": "audio", "file_id": "AUDIO_ID", "caption": "Song"}
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_audio.assert_called_once()

    def test_send_document(self):
        data = {"message_type": "document", "file_id": "DOC_ID", "caption": "File"}
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_document.assert_called_once()

    def test_send_location(self):
        data = {
            "message_type": "location",
            "file_id": "{}",
            "caption": "",
            "location_lat": 40.7128,
            "location_lon": -74.0060,
        }
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_location.assert_called_once()

    def test_send_text_only(self):
        data = {"message_type": None, "file_id": None, "caption": None}
        result = self.send(self.bot, 100, data, text="Hello")
        self.assertTrue(result)
        self.bot.send_message.assert_called_once()

    def test_send_failure_returns_false(self):
        self.bot.send_photo.side_effect = Exception("API error")
        data = {"message_type": "photo", "file_id": "X", "caption": ""}
        result = self.send(self.bot, 100, data)
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# 6. Media whisper auto-delete
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperAutoDelete(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_media_whisper_gets_auto_delete_at(self):
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="photo", file_id="PHOTO_X",
            auto_delete_hours=24,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

    def test_media_whisper_no_auto_delete(self):
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="video", file_id="VIDEO_X",
        )
        w = get_whisper(wid)
        self.assertIsNone(w["auto_delete_at"])

    def test_media_whisper_deleted_by_scheduler(self):
        """Media whispers should be deleted by delete_expired_whispers."""
        from database import delete_expired_whispers
        from datetime import datetime, timedelta, timezone

        # Create a whisper with auto_delete_at in the past
        wid = create_whisper(
            sender_id=10001, content="Expire me", whisper_type="everyone",
            message_type="photo", file_id="OLD_PHOTO",
        )
        # Manually set auto_delete_at to the past
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
# 7. Statistics include media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperStats(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_media_whisper_counted_in_total(self):
        before = get_stats()
        create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="photo", file_id="STATS_PHOTO",
        )
        create_whisper(
            sender_id=10001, content="", whisper_type="first_one",
            message_type="video", file_id="STATS_VIDEO",
        )
        after = get_stats()
        self.assertEqual(after["total_whispers"], before["total_whispers"] + 2)

    def test_media_whisper_counted_in_user_stats(self):
        before = get_user_stats(10001)
        create_whisper(
            sender_id=10001, content="", whisper_type="everyone",
            message_type="photo", file_id="USER_STATS_PHOTO",
        )
        after = get_user_stats(10001)
        self.assertEqual(after["sent"], before["sent"] + 1)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Dashboard shows media type
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardMediaDisplay(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_dashboard_text_shows_media_type(self):
        from handlers.dashboard import _build_dashboard_text
        wid = create_whisper(
            sender_id=10001, content="Photo caption", whisper_type="everyone",
            message_type="photo", file_id="DASH_PHOTO",
        )
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertIn("صورة", text)

    def test_dashboard_text_no_media_for_text_whisper(self):
        from handlers.dashboard import _build_dashboard_text
        wid = create_whisper(
            sender_id=10001, content="Just text", whisper_type="everyone",
        )
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertNotIn("نوع الوسائط", text)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Conversation view shows media labels
# ─────────────────────────────────────────────────────────────────────────────

class TestConversationMediaLabels(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_extract_media_from_conversation(self):
        """Verify _extract_media in replies.py handles all types."""
        from handlers.replies import _extract_media

        # Text
        msg = _make_msg("text", text="hello")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(content, "hello")
        self.assertIsNone(mt)
        self.assertIsNone(fid)

        # Photo
        msg = _make_msg("photo", file_id="PHOTO_CONV", caption="pic")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "photo")
        self.assertEqual(fid, "PHOTO_CONV")
        self.assertEqual(content, "pic")

        # Video
        msg = _make_msg("video", file_id="VIDEO_CONV", caption="vid")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "video")
        self.assertEqual(fid, "VIDEO_CONV")

        # Voice
        msg = _make_msg("voice", file_id="VOICE_CONV")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "voice")
        self.assertEqual(fid, "VOICE_CONV")

        # Audio
        msg = _make_msg("audio", file_id="AUDIO_CONV", caption="track")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "audio")
        self.assertEqual(fid, "AUDIO_CONV")

        # Document
        msg = _make_msg("document", file_id="DOC_CONV", caption="file")
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "document")
        self.assertEqual(fid, "DOC_CONV")

        # Location
        msg = _make_msg("location", latitude=10.0, longitude=20.0)
        content, mt, fid = _extract_media(msg)
        self.assertEqual(mt, "location")
        loc = json.loads(fid)
        self.assertAlmostEqual(loc["latitude"], 10.0)
        self.assertAlmostEqual(loc["longitude"], 20.0)


# ─────────────────────────────────────────────────────────────────────────────
# 10. All 6 required message types stored and retrieved
# ─────────────────────────────────────────────────────────────────────────────

class TestAllRequiredMessageTypes(unittest.TestCase):
    """Verify all 6 required message types can be stored and retrieved."""

    TYPES = [
        ("photo", "AgACAgIAPhoto", "Caption photo"),
        ("video", "AwACAgIAVideo", "Caption video"),
        ("audio", "AgACAgIAAudio", "Caption audio"),
        ("voice", "AwACAgIAVoice", ""),
        ("document", "BwACAgIADoc", "Caption doc"),
        ("location", None, ""),
    ]

    def setUp(self):
        _boot()

    def test_all_types_stored(self):
        for mt, fid, caption in self.TYPES:
            kwargs = {
                "sender_id": 10001,
                "content": f"Test {mt}",
                "whisper_type": "everyone",
                "message_type": mt,
            }
            if mt == "location":
                kwargs["file_id"] = json.dumps({"latitude": 1.0, "longitude": 2.0})
                kwargs["location_lat"] = 1.0
                kwargs["location_lon"] = 2.0
            else:
                kwargs["file_id"] = fid
                kwargs["caption"] = caption

            wid = create_whisper(**kwargs)
            w = get_whisper(wid)
            self.assertIsNotNone(w, f"Failed for type: {mt}")
            self.assertEqual(w["message_type"], mt)

    def test_whisper_type_field_preserved_with_media(self):
        """Media whispers should preserve whisper_type correctly."""
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="first_one",
            target_users=[10002], max_readers=1,
            message_type="photo", file_id="X",
        )
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)

    def test_media_whisper_with_auto_delete_and_type(self):
        """Media + auto_delete + whisper_type all work together."""
        wid = create_whisper(
            sender_id=10001, content="", whisper_type="custom",
            target_users=[10002], auto_delete_hours=48,
            message_type="video", file_id="VID_FULL",
            caption="Full test",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "video")
        self.assertEqual(w["whisper_type"], "custom")
        self.assertIsNotNone(w["auto_delete_at"])


# ─────────────────────────────────────────────────────────────────────────────
# 11. Migration idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaMigrationIdempotent(unittest.TestCase):
    """Running init_db() multiple times should not crash or duplicate columns."""

    def test_double_init(self):
        db.init_db()
        db.init_db()  # second call
        # Verify columns still exist
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("message_type", cols)
        self.assertIn("file_id", cols)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Resend preserves media fields
# ─────────────────────────────────────────────────────────────────────────────

class TestResendPreservesMedia(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_create_whisper_from_media_whisper(self):
        """Simulate what dashboard resend does — create new whisper with media fields."""
        wid1 = create_whisper(
            sender_id=10001, content="Original", whisper_type="everyone",
            message_type="photo", file_id="ORIG_PHOTO", caption="Original caption",
            location_lat=None, location_lon=None,
        )
        w1 = get_whisper(wid1)

        wid2 = create_whisper(
            sender_id=10001,
            content=w1["content"],
            whisper_type=w1["whisper_type"],
            message_type=w1["message_type"],
            file_id=w1["file_id"],
            caption=w1["caption"],
            location_lat=w1["location_lat"],
            location_lon=w1["location_lon"],
        )
        w2 = get_whisper(wid2)
        self.assertEqual(w2["message_type"], "photo")
        self.assertEqual(w2["file_id"], "ORIG_PHOTO")
        self.assertEqual(w2["caption"], "Original caption")
        self.assertNotEqual(wid1, wid2)


# ─────────────────────────────────────────────────────────────────────────────
# 13. _deliver_reply handles all media types (integration)
# ─────────────────────────────────────────────────────────────────────────────

class TestDeliverReplyMediaTypes(unittest.TestCase):
    """Test that _deliver_reply in handlers/replies.py sends correct media."""

    def setUp(self):
        from handlers.replies import _deliver_reply
        self._deliver = _deliver_reply
        self.bot = MagicMock()
        self.msg = MagicMock()

    def _kb(self):
        from telebot.types import InlineKeyboardMarkup
        return InlineKeyboardMarkup()

    def test_deliver_photo(self):
        self._deliver(self.bot, self.msg, 100, "Header", "caption",
                       "photo", "FILE_PHOTO", self._kb())
        self.bot.send_photo.assert_called_once()

    def test_deliver_video(self):
        self._deliver(self.bot, self.msg, 100, "Header", "caption",
                       "video", "FILE_VIDEO", self._kb())
        self.bot.send_video.assert_called_once()

    def test_deliver_voice(self):
        self._deliver(self.bot, self.msg, 100, "Header", "",
                       "voice", "FILE_VOICE", self._kb())
        self.bot.send_voice.assert_called_once()

    def test_deliver_audio(self):
        self._deliver(self.bot, self.msg, 100, "Header", "",
                       "audio", "FILE_AUDIO", self._kb())
        self.bot.send_audio.assert_called_once()

    def test_deliver_document(self):
        self._deliver(self.bot, self.msg, 100, "Header", "doc",
                       "document", "FILE_DOC", self._kb())
        self.bot.send_document.assert_called_once()

    def test_deliver_location(self):
        loc_data = json.dumps({"latitude": 10.0, "longitude": 20.0})
        self._deliver(self.bot, self.msg, 100, "Header", "",
                       "location", loc_data, self._kb())
        self.bot.send_location.assert_called_once()

    def test_deliver_text_fallback(self):
        self._deliver(self.bot, self.msg, 100, "Header", "Hello",
                       None, None, self._kb())
        self.bot.send_message.assert_called_once()


if __name__ == "__main__":
    unittest.main()
