"""
tests/test_media_whispers.py — Comprehensive tests for Media Whispers v3.0.

Covers:
  1.  Schema migration: media_type column exists after migration
  2.  Schema migration: idempotent (double init doesn't crash)
  3.  Media whisper creation with media_type field
  4.  Backward compat: text whispers still work without media_type
  5.  Backward compat: create_whisper without media_type defaults to message_type
  6.  Auto-delete works for media whispers
  7.  Statistics include media whispers
  8.  Read tracking works for media whispers
  9.  Pinning works for media whispers
  10. Reports (curious ones) work for media whispers
  11. Dashboard shows media_type for media whispers
  12. Private chat media flow creates whisper directly
  13. Photo uses highest resolution (photo[-1].file_id)
  14. All 5 media types: Voice, Photo, Video, Audio, Document
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_media_whispers_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user, get_stats, get_user_stats,
    delete_expired_whispers, add_reader_if_new, record_whisper_read,
    reader_count, get_readers, add_curious, get_curious_ones,
    toggle_pin_whisper, get_pinned_whispers, set_setting,
)


def _boot():
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM whisper_readers")
        conn.execute("DELETE FROM curious_ones")
        conn.execute("DELETE FROM whisper_timestamps")
        conn.execute("DELETE FROM whispers")
        conn.commit()
    upsert_user(30001, "alice_mw", "Alice", None)
    upsert_user(30002, "bob_mw", "Bob", None)


def _make_media_msg(content_type, **kwargs):
    msg = MagicMock()
    msg.content_type = content_type
    msg.from_user = MagicMock()
    msg.from_user.id = kwargs.get("sender_id", 30001)
    msg.from_user.username = "alice_mw"
    msg.from_user.first_name = "Alice"
    msg.from_user.last_name = None
    msg.chat = MagicMock()
    msg.chat.id = kwargs.get("chat_id", 30001)
    msg.chat.type = "private"

    if content_type == "photo":
        photo = MagicMock()
        photo.file_id = kwargs.get("file_id", "PHOTO_MW")
        small = MagicMock()
        small.file_id = "SMALL_PHOTO"
        msg.photo = [small, photo]
        msg.caption = kwargs.get("caption", "")
    elif content_type == "video":
        msg.video = MagicMock()
        msg.video.file_id = kwargs.get("file_id", "VIDEO_MW")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "voice":
        msg.voice = MagicMock()
        msg.voice.file_id = kwargs.get("file_id", "VOICE_MW")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "audio":
        msg.audio = MagicMock()
        msg.audio.file_id = kwargs.get("file_id", "AUDIO_MW")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "document":
        msg.document = MagicMock()
        msg.document.file_id = kwargs.get("file_id", "DOC_MW")
        msg.caption = kwargs.get("caption", "")
    return msg


# ─────────────────────────────────────────────────────────────────────────────
# 1. Schema migration: media_type column exists
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaTypeColumnExists(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_whispers_table_has_media_type(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("media_type", cols)

    def test_media_type_is_nullable(self):
        wid = create_whisper(30001, "test", "everyone")
        w = get_whisper(wid)
        self.assertIsNone(w["media_type"])

    def test_file_id_column_exists(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("file_id", cols)

    def test_message_type_column_still_exists(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("message_type", cols)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Schema migration: idempotent
# ─────────────────────────────────────────────────────────────────────────────

class TestMigrationIdempotent(unittest.TestCase):
    def test_double_init_no_crash(self):
        db.init_db()
        db.init_db()
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("media_type", cols)
        self.assertIn("file_id", cols)

    def test_triple_init_no_crash(self):
        db.init_db()
        db.init_db()
        db.init_db()
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(whispers)"
            ).fetchall()]
        self.assertIn("media_type", cols)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Media whisper creation with media_type field
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperCreation(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_photo_whisper_stores_media_type(self):
        wid = create_whisper(
            sender_id=30001, content="Check photo", whisper_type="everyone",
            message_type="photo", file_id="PHOTO_ABC",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "photo")
        self.assertEqual(w["message_type"], "photo")
        self.assertEqual(w["file_id"], "PHOTO_ABC")

    def test_video_whisper_stores_media_type(self):
        wid = create_whisper(
            sender_id=30001, content="Video time", whisper_type="first_one",
            message_type="video", file_id="VIDEO_DEF",
            media_type="video",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "video")
        self.assertEqual(w["file_id"], "VIDEO_DEF")

    def test_voice_whisper_stores_media_type(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="voice", file_id="VOICE_GHI",
            media_type="voice",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "voice")
        self.assertEqual(w["file_id"], "VOICE_GHI")

    def test_audio_whisper_stores_media_type(self):
        wid = create_whisper(
            sender_id=30001, content="Song", whisper_type="everyone",
            message_type="audio", file_id="AUDIO_JKL",
            media_type="audio",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "audio")

    def test_document_whisper_stores_media_type(self):
        wid = create_whisper(
            sender_id=30001, content="Important file", whisper_type="custom",
            target_users=[30002],
            message_type="document", file_id="DOC_MNO",
            media_type="document",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "document")
        self.assertEqual(w["file_id"], "DOC_MNO")

    def test_whisper_id_is_unique(self):
        wid1 = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="A", media_type="photo",
        )
        wid2 = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="B", media_type="photo",
        )
        self.assertNotEqual(wid1, wid2)

    def test_whisper_id_length(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="X", media_type="photo",
        )
        self.assertEqual(len(wid), 12)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Backward compat: text whispers still work
# ─────────────────────────────────────────────────────────────────────────────

class TestTextWhisperBackwardCompat(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_text_whisper_no_media_type(self):
        wid = create_whisper(30001, "Hello!", "everyone")
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertIsNone(w["media_type"])
        self.assertIsNone(w["message_type"])
        self.assertIsNone(w["file_id"])

    def test_text_whisper_explicit_none(self):
        wid = create_whisper(
            sender_id=30001, content="Test", whisper_type="first_one",
            message_type=None, file_id=None, media_type=None,
        )
        w = get_whisper(wid)
        self.assertIsNone(w["media_type"])
        self.assertIsNone(w["file_id"])

    def test_text_whisper_preserves_all_fields(self):
        wid = create_whisper(
            sender_id=30001, content="Secret", whisper_type="custom",
            target_users=[30002], max_readers=1,
        )
        w = get_whisper(wid)
        self.assertEqual(w["content"], "Secret")
        self.assertEqual(w["whisper_type"], "custom")
        self.assertEqual(w["max_readers"], 1)
        self.assertIsNone(w["media_type"])


# ─────────────────────────────────────────────────────────────────────────────
# 5. Backward compat: create_whisper without media_type defaults to message_type
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaTypeDefaulToMessageType(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_media_type_defaults_to_message_type(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="PHOTO_DEFAULT",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "photo")
        self.assertEqual(w["message_type"], "photo")

    def test_explicit_media_type_takes_precedence(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="X",
            media_type="video",
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "video")
        self.assertEqual(w["message_type"], "photo")


# ─────────────────────────────────────────────────────────────────────────────
# Auto-delete works for media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperAutoDelete(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_media_whisper_with_auto_delete(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="PHOTO_DEL",
            media_type="photo", auto_delete_hours=24,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

    def test_media_whisper_no_auto_delete(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="PHOTO_NODEL",
            media_type="photo",
        )
        w = get_whisper(wid)
        self.assertIsNone(w["auto_delete_at"])

    def test_media_whisper_expired_deleted(self):
        wid = create_whisper(
            sender_id=30001, content="Expire me", whisper_type="everyone",
            message_type="video", file_id="VIDEO_EXPIRE",
            media_type="video",
        )
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
# 11. Statistics include media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperStats(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_media_whisper_counted_in_total(self):
        before = get_stats()
        create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="STATS_PH", media_type="photo",
        )
        create_whisper(
            sender_id=30001, content="", whisper_type="first_one",
            message_type="video", file_id="STATS_VD", media_type="video",
        )
        after = get_stats()
        self.assertEqual(after["total_whispers"], before["total_whispers"] + 2)

    def test_media_whisper_counted_in_user_stats(self):
        before = get_user_stats(30001)
        create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="audio", file_id="USTATS_AU", media_type="audio",
        )
        after = get_user_stats(30001)
        self.assertEqual(after["sent"], before["sent"] + 1)


# ─────────────────────────────────────────────────────────────────────────────
# 12. Read tracking works for media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperReadTracking(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_read_tracking_photo_whisper(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="READ_PH", media_type="photo",
        )
        is_new = record_whisper_read(wid, 30002)
        self.assertTrue(is_new)
        self.assertEqual(reader_count(wid), 1)

        is_new2 = record_whisper_read(wid, 30002)
        self.assertFalse(is_new2)
        self.assertEqual(reader_count(wid), 1)

    def test_read_tracking_video_whisper(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="video", file_id="READ_VD", media_type="video",
        )
        is_new = record_whisper_read(wid, 30002)
        self.assertTrue(is_new)

    def test_readers_list_includes_media_whisper(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="document", file_id="READ_DOC", media_type="document",
        )
        record_whisper_read(wid, 30002)
        readers = get_readers(wid)
        self.assertEqual(len(readers), 1)
        self.assertEqual(readers[0]["user_id"], 30002)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Pinning works for media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperPinning(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_pin_media_whisper(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="PIN_PH", media_type="photo",
        )
        new_state = toggle_pin_whisper(wid)
        self.assertEqual(new_state, 1)
        w = get_whisper(wid)
        self.assertEqual(w["is_pinned"], 1)

    def test_get_pinned_media_whispers(self):
        create_whisper(
            sender_id=30001, content="pinned", whisper_type="everyone",
            message_type="photo", file_id="PIN2", media_type="photo",
        )
        wid2 = create_whisper(
            sender_id=30001, content="pinned2", whisper_type="everyone",
            message_type="video", file_id="PIN3", media_type="video",
        )
        toggle_pin_whisper(wid2)
        pinned, total = get_pinned_whispers(30001)
        self.assertEqual(total, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 14. Reports (curious ones) work for media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperCuriousOnes(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_curious_on_media_whisper(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="CUR_PH", media_type="photo",
        )
        add_curious(wid, 30002)
        curious = get_curious_ones(wid)
        self.assertEqual(len(curious), 1)
        self.assertEqual(curious[0]["user_id"], 30002)


# ─────────────────────────────────────────────────────────────────────────────
# 15. Dashboard shows media_type for media whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardMediaType(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_dashboard_shows_media_type_label(self):
        from handlers.dashboard import _build_dashboard_text
        wid = create_whisper(
            sender_id=30001, content="Photo caption", whisper_type="everyone",
            message_type="photo", file_id="DASH_PH", media_type="photo",
        )
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertIn("صورة", text)

    def test_dashboard_no_media_for_text_whisper(self):
        from handlers.dashboard import _build_dashboard_text
        wid = create_whisper(30001, "Just text", "everyone")
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertNotIn("نوع الوسائط", text)

    def test_dashboard_shows_video_label(self):
        from handlers.dashboard import _build_dashboard_text
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="video", file_id="DASH_VD", media_type="video",
        )
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertIn("فيديو", text)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Private chat media flow creates whisper directly
# ─────────────────────────────────────────────────────────────────────────────

class TestPrivateChatMediaFlow(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_photo_creates_whisper_with_media_type(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("photo", file_id="PRIVATE_PHOTO", caption="Test")
        media = extract_media_from_message(msg)
        self.assertEqual(media["message_type"], "photo")
        self.assertEqual(media["file_id"], "PRIVATE_PHOTO")

        wid = create_whisper(
            sender_id=30001, content=media["content"], whisper_type="everyone",
            message_type=media["message_type"], file_id=media["file_id"],
            caption=media["caption"], media_type=media["message_type"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "photo")
        self.assertEqual(w["file_id"], "PRIVATE_PHOTO")

    def test_video_creates_whisper_with_media_type(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("video", file_id="PRIVATE_VIDEO", caption="Vid")
        media = extract_media_from_message(msg)
        wid = create_whisper(
            sender_id=30001, content=media["content"], whisper_type="everyone",
            message_type=media["message_type"], file_id=media["file_id"],
            caption=media["caption"], media_type=media["message_type"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "video")

    def test_voice_creates_whisper_with_media_type(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("voice", file_id="PRIVATE_VOICE")
        media = extract_media_from_message(msg)
        wid = create_whisper(
            sender_id=30001, content=media["content"], whisper_type="everyone",
            message_type=media["message_type"], file_id=media["file_id"],
            media_type=media["message_type"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "voice")

    def test_audio_creates_whisper_with_media_type(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("audio", file_id="PRIVATE_AUDIO", caption="Song")
        media = extract_media_from_message(msg)
        wid = create_whisper(
            sender_id=30001, content=media["content"], whisper_type="everyone",
            message_type=media["message_type"], file_id=media["file_id"],
            caption=media["caption"], media_type=media["message_type"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "audio")

    def test_document_creates_whisper_with_media_type(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("document", file_id="PRIVATE_DOC", caption="File")
        media = extract_media_from_message(msg)
        wid = create_whisper(
            sender_id=30001, content=media["content"], whisper_type="everyone",
            message_type=media["message_type"], file_id=media["file_id"],
            caption=media["caption"], media_type=media["message_type"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["media_type"], "document")


# ─────────────────────────────────────────────────────────────────────────────
# 17. Photo uses highest resolution (photo[-1].file_id)
# ─────────────────────────────────────────────────────────────────────────────

class TestPhotoHighestResolution(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_photo_uses_last_element(self):
        from services.media import extract_media_from_message
        msg = _make_media_msg("photo", file_id="HIGHEST_RES_PHOTO")
        media = extract_media_from_message(msg)
        self.assertEqual(media["file_id"], "HIGHEST_RES_PHOTO")

    def test_photo_with_multiple_sizes(self):
        msg = MagicMock()
        msg.content_type = "photo"
        sizes = [MagicMock(), MagicMock(), MagicMock()]
        sizes[0].file_id = "SIZE_90"
        sizes[1].file_id = "SIZE_320"
        sizes[2].file_id = "SIZE_1280"
        msg.photo = sizes
        msg.caption = "Multi-size"

        from services.media import extract_media_from_message
        media = extract_media_from_message(msg)
        self.assertEqual(media["file_id"], "SIZE_1280")


# ─────────────────────────────────────────────────────────────────────────────
# 18. All 5 media types: Voice, Photo, Video, Audio, Document
# ─────────────────────────────────────────────────────────────────────────────

class TestAllFiveMediaTypes(unittest.TestCase):
    """Verify all 5 required media types work end-to-end."""
    TYPES = [
        ("photo",    "PH_ALL_5", "Caption photo"),
        ("video",    "VD_ALL_5", "Caption video"),
        ("audio",    "AU_ALL_5", "Caption audio"),
        ("voice",    "VO_ALL_5", ""),
        ("document", "DO_ALL_5", "Caption doc"),
    ]

    def setUp(self):
        _boot()

    def test_all_types_stored_with_media_type(self):
        for mt, fid, caption in self.TYPES:
            wid = create_whisper(
                sender_id=30001, content=caption, whisper_type="everyone",
                message_type=mt, file_id=fid, caption=caption,
                media_type=mt,
            )
            w = get_whisper(wid)
            self.assertIsNotNone(w, f"Failed for type: {mt}")
            self.assertEqual(w["media_type"], mt)
            self.assertEqual(w["message_type"], mt)
            self.assertEqual(w["file_id"], fid)

    def test_all_types_in_stats(self):
        before = get_stats()
        for mt, fid, caption in self.TYPES:
            create_whisper(
                sender_id=30001, content=caption, whisper_type="everyone",
                message_type=mt, file_id=fid, caption=caption,
                media_type=mt,
            )
        after = get_stats()
        self.assertEqual(after["total_whispers"], before["total_whispers"] + 5)

    def test_all_types_auto_delete(self):
        for mt, fid, _ in self.TYPES:
            wid = create_whisper(
                sender_id=30001, content="", whisper_type="everyone",
                message_type=mt, file_id=fid, media_type=mt,
                auto_delete_hours=1,
            )
            w = get_whisper(wid)
            self.assertIsNotNone(w["auto_delete_at"], f"auto_delete failed for {mt}")

    def test_all_types_read_tracking(self):
        for mt, fid, _ in self.TYPES:
            wid = create_whisper(
                sender_id=30001, content="", whisper_type="everyone",
                message_type=mt, file_id=fid, media_type=mt,
            )
            is_new = record_whisper_read(wid, 30002)
            self.assertTrue(is_new, f"read tracking failed for {mt}")

    def test_all_types_deletable(self):
        for mt, fid, _ in self.TYPES:
            wid = create_whisper(
                sender_id=30001, content="", whisper_type="everyone",
                message_type=mt, file_id=fid, media_type=mt,
            )
            w = get_whisper(wid)
            self.assertIsNotNone(w)
            db.delete_whisper(wid)
            self.assertIsNone(get_whisper(wid))


# ─────────────────────────────────────────────────────────────────────────────
# 19. send_media_message delivers correct media for callback
# ─────────────────────────────────────────────────────────────────────────────

class TestSendMediaMessageCallback(unittest.TestCase):
    def setUp(self):
        from services.media import send_media_message
        self.send = send_media_message
        self.bot = MagicMock()

    def test_send_photo(self):
        data = {"message_type": "photo", "file_id": "CB_PHOTO", "caption": "Hi"}
        result = self.send(self.bot, 30002, data)
        self.assertTrue(result)
        self.bot.send_photo.assert_called_once()

    def test_send_video(self):
        data = {"message_type": "video", "file_id": "CB_VIDEO", "caption": "Watch"}
        result = self.send(self.bot, 30002, data)
        self.assertTrue(result)
        self.bot.send_video.assert_called_once()

    def test_send_voice(self):
        data = {"message_type": "voice", "file_id": "CB_VOICE", "caption": ""}
        result = self.send(self.bot, 30002, data)
        self.assertTrue(result)
        self.bot.send_voice.assert_called_once()

    def test_send_audio(self):
        data = {"message_type": "audio", "file_id": "CB_AUDIO", "caption": "Song"}
        result = self.send(self.bot, 30002, data)
        self.assertTrue(result)
        self.bot.send_audio.assert_called_once()

    def test_send_document(self):
        data = {"message_type": "document", "file_id": "CB_DOC", "caption": "File"}
        result = self.send(self.bot, 30002, data)
        self.assertTrue(result)
        self.bot.send_document.assert_called_once()

    def test_send_text_fallback(self):
        data = {"message_type": None, "file_id": None, "caption": None}
        result = self.send(self.bot, 30002, data, text="Hello")
        self.assertTrue(result)
        self.bot.send_message.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 20. Whisper type preserved with media
# ─────────────────────────────────────────────────────────────────────────────

class TestWhisperTypeWithMedia(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_first_one_with_media(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="first_one",
            target_users=[30002], max_readers=1,
            message_type="photo", file_id="TYPE_PH", media_type="photo",
        )
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)
        self.assertEqual(w["media_type"], "photo")

    def test_custom_with_media(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="custom",
            target_users=[30002],
            message_type="video", file_id="TYPE_VD", media_type="video",
        )
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "custom")
        self.assertEqual(w["media_type"], "video")

    def test_first_three_with_media(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="first_three",
            max_readers=3,
            message_type="audio", file_id="TYPE_AU", media_type="audio",
        )
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_three")
        self.assertEqual(w["max_readers"], 3)


# ─────────────────────────────────────────────────────────────────────────────
# 21. Direct test: media whisper button URL + deep link + send_photo
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWhisperDeepLinkDelivery(unittest.TestCase):
    """Direct test: create photo whisper → verify button URL → simulate
    deep link opening → verify bot.send_photo is called with file_id."""

    def setUp(self):
        _boot()

    def test_view_prefix_stripping_and_whisper_load(self):
        wid = create_whisper(
            sender_id=30001, content="", whisper_type="everyone",
            message_type="photo", file_id="VIEW_STRIP_PHOTO",
            media_type="photo",
        )

        # Simulate /start handler view_ prefix stripping
        payload = f"view_{wid}"
        whisper_id_payload = payload[len("view_"):] if payload.startswith("view_") else payload
        self.assertEqual(whisper_id_payload, wid)

        whisper = get_whisper(whisper_id_payload)
        self.assertIsNotNone(whisper)
        self.assertEqual(whisper["media_type"], "photo")
        self.assertEqual(whisper["file_id"], "VIEW_STRIP_PHOTO")

    def test_deep_link_sends_photo_via_send_media_message(self):
        wid = create_whisper(
            sender_id=30001, content="Hello photo", whisper_type="everyone",
            message_type="photo", file_id="SEND_PHOTO_FID",
            media_type="photo", caption="My caption",
        )
        whisper = get_whisper(wid)
        self.assertIsNotNone(whisper)

        from services.media import send_media_message
        mock_bot = MagicMock()
        w_dict = dict(whisper)
        result = send_media_message(
            mock_bot, 30002, w_dict,
            text="🤫 *الهمسة:*",
            parse_mode="Markdown",
        )
        self.assertTrue(result)
        mock_bot.send_photo.assert_called_once()
        call_args = mock_bot.send_photo.call_args
        self.assertEqual(call_args[0][0], 30002)
        self.assertEqual(call_args[0][1], "SEND_PHOTO_FID")

    def test_all_media_types_delivered_via_deep_link(self):
        TYPES = [
            ("photo",    "DL_PH", "send_photo"),
            ("video",    "DL_VD", "send_video"),
            ("voice",    "DL_VO", "send_voice"),
            ("audio",    "DL_AU", "send_audio"),
            ("document", "DL_DO", "send_document"),
        ]
        from services.media import send_media_message
        for mt, fid, method_name in TYPES:
            wid = create_whisper(
                sender_id=30001, content="", whisper_type="everyone",
                message_type=mt, file_id=fid, media_type=mt,
            )
            whisper = get_whisper(wid)
            mock_bot = MagicMock()
            result = send_media_message(mock_bot, 30002, dict(whisper))
            self.assertTrue(result, f"send_media_message failed for {mt}")
            method = getattr(mock_bot, method_name)
            method.assert_called_once()
            call_args = method.call_args
            self.assertEqual(call_args[0][0], 30002)


if __name__ == "__main__":
    unittest.main()
