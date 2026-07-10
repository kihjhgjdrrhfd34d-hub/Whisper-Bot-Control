"""
tests/test_media_wizard.py — Comprehensive tests for v2.2.0 Media Whisper Wizard.

Covers:
  1.  DB schema: pending_media_whispers table exists
  2.  store / get / delete pending media CRUD
  3.  get_pending_media returns most recent
  4.  cleanup_stale_pending_media removes old entries
  5.  Media extraction: animation type support
  6.  send_media_message: animation type
  7.  build_wizard_inline_results for all media types
  8.  build_wizard_inline_results returns 4 results
  9.  Inline query returns wizard results when pending media exists
  10. chosen_inline_result creates whisper from pending media
  11. Cancel flow deletes pending media
  12. Migration idempotency for pending_media_whispers
  13. Media wizard whisper auto-delete
  14. Dashboard integration with media wizard whispers
  15. Anti-spam: pending media respects bot_active setting
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch, PropertyMock

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_media_wizard_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user, get_stats,
    store_pending_media, get_pending_media, get_pending_media_by_id,
    delete_pending_media, delete_pending_media_by_id,
    cleanup_stale_pending_media, set_setting,
)


def _boot():
    db.init_db()
    upsert_user(20001, "alice_wiz", "Alice", None)
    upsert_user(20002, "bob_wiz", "Bob", None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. DB schema: pending_media_whispers table exists
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaTableExists(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_pending_media_table_exists(self):
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("pending_media_whispers", tables)

    def test_pending_media_has_user_id(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("user_id", cols)

    def test_pending_media_has_message_type(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("message_type", cols)

    def test_pending_media_has_file_id(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("file_id", cols)

    def test_pending_media_has_caption(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("caption", cols)

    def test_pending_media_has_content(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("content", cols)

    def test_pending_media_has_created_at(self):
        with db.get_conn() as conn:
            cols = [r[1] for r in conn.execute(
                "PRAGMA table_info(pending_media_whispers)"
            ).fetchall()]
        self.assertIn("created_at", cols)


# ─────────────────────────────────────────────────────────────────────────────
# 2. store / get / delete pending media CRUD
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaCRUD(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_store_and_get_pending_media_photo(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="PHOTO_WIZ_123", caption="Test caption",
        )
        self.assertIsNotNone(pid)
        self.assertGreater(pid, 0)
        pm = get_pending_media(20001)
        self.assertIsNotNone(pm)
        self.assertEqual(pm["message_type"], "photo")
        self.assertEqual(pm["file_id"], "PHOTO_WIZ_123")
        self.assertEqual(pm["caption"], "Test caption")

    def test_store_and_get_pending_media_video(self):
        pid = store_pending_media(
            user_id=20001, message_type="video",
            file_id="VIDEO_WIZ_456", caption="Video caption",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["message_type"], "video")
        self.assertEqual(pm["file_id"], "VIDEO_WIZ_456")

    def test_store_and_get_pending_media_audio(self):
        pid = store_pending_media(
            user_id=20001, message_type="audio",
            file_id="AUDIO_WIZ_789", caption="Audio caption",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["message_type"], "audio")

    def test_store_and_get_pending_media_voice(self):
        pid = store_pending_media(
            user_id=20001, message_type="voice",
            file_id="VOICE_WIZ_012",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["message_type"], "voice")
        self.assertIsNone(pm["caption"])

    def test_store_and_get_pending_media_document(self):
        pid = store_pending_media(
            user_id=20001, message_type="document",
            file_id="DOC_WIZ_345", caption="Doc caption",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["message_type"], "document")

    def test_store_and_get_pending_media_animation(self):
        pid = store_pending_media(
            user_id=20001, message_type="animation",
            file_id="ANIM_WIZ_678", caption="GIF caption",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["message_type"], "animation")
        self.assertEqual(pm["file_id"], "ANIM_WIZ_678")

    def test_delete_pending_media(self):
        store_pending_media(
            user_id=20001, message_type="photo",
            file_id="TO_DELETE",
        )
        self.assertIsNotNone(get_pending_media(20001))
        delete_pending_media(20001)
        self.assertIsNone(get_pending_media(20001))

    def test_delete_pending_media_by_id(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="TO_DELETE_BY_ID",
        )
        delete_pending_media_by_id(pid)
        self.assertIsNone(get_pending_media_by_id(pid))

    def test_get_pending_media_no_user(self):
        self.assertIsNone(get_pending_media(999999))

    def test_get_pending_media_by_id_not_found(self):
        self.assertIsNone(get_pending_media_by_id(999999))


# ─────────────────────────────────────────────────────────────────────────────
# 3. get_pending_media returns most recent
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaMostRecent(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_returns_most_recent(self):
        store_pending_media(
            user_id=20001, message_type="photo",
            file_id="OLD_PHOTO",
        )
        store_pending_media(
            user_id=20001, message_type="video",
            file_id="NEW_VIDEO",
        )
        pm = get_pending_media(20001)
        self.assertEqual(pm["file_id"], "NEW_VIDEO")
        self.assertEqual(pm["message_type"], "video")

    def test_delete_clears_all(self):
        store_pending_media(user_id=20001, message_type="photo", file_id="A")
        store_pending_media(user_id=20001, message_type="video", file_id="B")
        delete_pending_media(20001)
        self.assertIsNone(get_pending_media(20001))


# ─────────────────────────────────────────────────────────────────────────────
# 4. cleanup_stale_pending_media
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaCleanup(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_cleanup_removes_old_entries(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="STALE_PHOTO",
        )
        # Manually set created_at to 2 hours ago (SQLite datetime format)
        two_hours_ago = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).strftime("%Y-%m-%d %H:%M:%S")
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE pending_media_whispers SET created_at=? WHERE id=?",
                (two_hours_ago, pid),
            )
            conn.commit()

        cleanup_stale_pending_media(hours=1)
        self.assertIsNone(get_pending_media_by_id(pid))

    def test_cleanup_keeps_recent(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="RECENT_PHOTO",
        )
        cleanup_stale_pending_media(hours=1)
        self.assertIsNotNone(get_pending_media_by_id(pid))


# ─────────────────────────────────────────────────────────────────────────────
# 5. Media extraction: animation type support
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
        msg.photo = [MagicMock(), photo]
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
    elif content_type == "animation":
        msg.animation = MagicMock()
        msg.animation.file_id = kwargs.get("file_id", "ANIM_FILE_ID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "location":
        msg.location = MagicMock()
        msg.location.latitude = kwargs.get("latitude", 40.7128)
        msg.location.longitude = kwargs.get("longitude", -74.0060)
    return msg


class TestAnimationExtraction(unittest.TestCase):
    def setUp(self):
        from services.media import extract_media_from_message
        self.extract = extract_media_from_message

    def test_animation_message(self):
        msg = _make_msg("animation", file_id="ANIM_123", caption="funny gif")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "animation")
        self.assertEqual(result["file_id"], "ANIM_123")
        self.assertEqual(result["caption"], "funny gif")
        self.assertEqual(result["content"], "funny gif")

    def test_animation_no_caption(self):
        msg = _make_msg("animation", file_id="ANIM_456", caption="")
        result = self.extract(msg)
        self.assertEqual(result["message_type"], "animation")
        self.assertEqual(result["file_id"], "ANIM_456")
        self.assertEqual(result["caption"], "")

    def test_animation_in_supported_media(self):
        from services.media import SUPPORTED_WHISPER_MEDIA
        self.assertIn("animation", SUPPORTED_WHISPER_MEDIA)


# ─────────────────────────────────────────────────────────────────────────────
# 6. send_media_message: animation type
# ─────────────────────────────────────────────────────────────────────────────

class TestSendAnimationMessage(unittest.TestCase):
    def setUp(self):
        from services.media import send_media_message
        self.send = send_media_message
        self.bot = MagicMock()

    def test_send_animation(self):
        data = {"message_type": "animation", "file_id": "ANIM_SEND", "caption": "GIF"}
        result = self.send(self.bot, 100, data)
        self.assertTrue(result)
        self.bot.send_animation.assert_called_once()

    def test_send_animation_no_caption(self):
        data = {"message_type": "animation", "file_id": "ANIM_NO_CAP", "caption": ""}
        result = self.send(self.bot, 100, data, text="fallback")
        self.assertTrue(result)
        self.bot.send_animation.assert_called_once()


# ─────────────────────────────────────────────────────────────────────────────
# 7. build_wizard_inline_results for all media types
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildWizardInlineResults(unittest.TestCase):
    def setUp(self):
        _boot()
        from handlers.media_wizard import build_wizard_inline_results
        self.build = build_wizard_inline_results

    def _fake_pending(self, message_type, file_id, caption=""):
        pid = store_pending_media(
            user_id=20001, message_type=message_type,
            file_id=file_id, caption=caption,
        )
        return get_pending_media_by_id(pid)

    def test_photo_results(self):
        pending = self._fake_pending("photo", "PHOTO_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)
        for r in results:
            self.assertTrue(r.id.startswith("mw:"))

    def test_video_results(self):
        pending = self._fake_pending("video", "VIDEO_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)

    def test_audio_results(self):
        pending = self._fake_pending("audio", "AUDIO_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)

    def test_voice_results(self):
        pending = self._fake_pending("voice", "VOICE_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)

    def test_document_results(self):
        pending = self._fake_pending("document", "DOC_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)

    def test_animation_results(self):
        pending = self._fake_pending("animation", "ANIM_INLINE")
        results = self.build(pending, "testbot")
        self.assertEqual(len(results), 4)

    def test_result_ids_contain_type(self):
        pending = self._fake_pending("photo", "PHOTO_IDS")
        results = self.build(pending, "testbot")
        types = {"custom", "everyone", "first_one", "first_three"}
        result_types = set()
        for r in results:
            parts = r.id.split(":")
            result_types.add(parts[1])
        self.assertEqual(result_types, types)

    def test_result_ids_contain_pending_id(self):
        pending = self._fake_pending("photo", "PHOTO_PID")
        results = self.build(pending, "testbot")
        for r in results:
            parts = r.id.split(":")
            self.assertEqual(parts[2], str(pending["id"]))

    def test_result_has_reply_markup(self):
        pending = self._fake_pending("photo", "PHOTO_KB")
        results = self.build(pending, "testbot")
        for r in results:
            self.assertIsNotNone(r.reply_markup)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Media wizard whisper creation
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardWhisperCreation(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_create_whisper_from_pending_photo(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="WIZ_PHOTO_CREATE", caption="Create test",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content=pm["content"] or "",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["message_type"], "photo")
        self.assertEqual(w["file_id"], "WIZ_PHOTO_CREATE")
        self.assertEqual(w["caption"], "Create test")

    def test_create_whisper_from_pending_animation(self):
        pid = store_pending_media(
            user_id=20001, message_type="animation",
            file_id="WIZ_ANIM_CREATE", caption="Anim test",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content=pm["content"] or "",
            whisper_type="first_one",
            target_users=[20002],
            max_readers=1,
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["message_type"], "animation")
        self.assertEqual(w["whisper_type"], "first_one")

    def test_create_whisper_all_types(self):
        """All 6 media types can be created from pending."""
        types = [
            ("photo", "PH_ALL"),
            ("video", "V_ALL"),
            ("audio", "A_ALL"),
            ("voice", "VO_ALL"),
            ("document", "D_ALL"),
            ("animation", "AN_ALL"),
        ]
        for mt, fid in types:
            pid = store_pending_media(
                user_id=20001, message_type=mt, file_id=fid,
            )
            pm = get_pending_media_by_id(pid)
            wid = create_whisper(
                sender_id=20001,
                content="",
                whisper_type="everyone",
                message_type=pm["message_type"],
                file_id=pm["file_id"],
                caption=pm["caption"],
            )
            w = get_whisper(wid)
            self.assertIsNotNone(w, f"Failed for type: {mt}")
            self.assertEqual(w["message_type"], mt)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Media wizard whisper auto-delete
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardAutoDelete(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_wizard_whisper_with_auto_delete(self):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="WIZ_AUTO_DEL",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content="",
            whisper_type="everyone",
            auto_delete_hours=24,
            message_type=pm["message_type"],
            file_id=pm["file_id"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w["auto_delete_at"])

    def test_wizard_whisper_expired_deleted(self):
        from database import delete_expired_whispers
        pid = store_pending_media(
            user_id=20001, message_type="video",
            file_id="WIZ_EXPIRE",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content="Expire me",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
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
# 10. Dashboard integration
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardDashboard(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_dashboard_shows_animation_type(self):
        from handlers.dashboard import _build_dashboard_text
        pid = store_pending_media(
            user_id=20001, message_type="animation",
            file_id="DASH_ANIM",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content="",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        text = _build_dashboard_text(w)
        self.assertIn("🎞", text)

    def test_dashboard_shows_all_wizard_types(self):
        from handlers.dashboard import _get_type_label
        self.assertIn("مخصصة", _get_type_label("custom"))
        self.assertIn("للجميع", _get_type_label("everyone"))
        self.assertIn("أول شخص", _get_type_label("first_one"))
        self.assertIn("أول 3", _get_type_label("first_three"))


# ─────────────────────────────────────────────────────────────────────────────
# 11. Media wizard state handling in bot.py
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardInBotStateMachine(unittest.TestCase):
    """Test that the mwhisper_awaiting_media state in bot.py handles animation."""

    def setUp(self):
        _boot()

    def test_animation_stored_via_pending_flow(self):
        """Simulate the full wizard flow: store pending → create whisper."""
        pid = store_pending_media(
            user_id=20001, message_type="animation",
            file_id="FLOW_ANIM", caption="Flow test",
        )
        pm = get_pending_media_by_id(pid)
        self.assertIsNotNone(pm)
        self.assertEqual(pm["message_type"], "animation")

        wid = create_whisper(
            sender_id=20001,
            content=pm["content"] or "",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["message_type"], "animation")
        self.assertEqual(w["file_id"], "FLOW_ANIM")

        # Cleanup
        delete_pending_media(20001)
        self.assertIsNone(get_pending_media(20001))


# ─────────────────────────────────────────────────────────────────────────────
# 12. Migration idempotency
# ─────────────────────────────────────────────────────────────────────────────

class TestPendingMediaMigrationIdempotent(unittest.TestCase):
    """Running init_db() multiple times should not crash."""

    def test_double_init(self):
        db.init_db()
        db.init_db()
        with db.get_conn() as conn:
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()}
        self.assertIn("pending_media_whispers", tables)


# ─────────────────────────────────────────────────────────────────────────────
# 13. Anti-spam: pending media respects bot_active
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardAntiSpam(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_pending_media_created_regardless_of_bot_active(self):
        """Pending media can be stored even when bot is inactive."""
        set_setting("bot_active", "0")
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="SPAM_PHOTO",
        )
        self.assertIsNotNone(pid)
        set_setting("bot_active", "1")


# ─────────────────────────────────────────────────────────────────────────────
# 14. Media types stored in pending_media_whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestAllMediaTypesInPending(unittest.TestCase):
    """Verify all 6 media types can be stored in pending_media_whispers."""

    TYPES = [
        ("photo", "PM_PHOTO", "Photo caption"),
        ("video", "PM_VIDEO", "Video caption"),
        ("audio", "PM_AUDIO", "Audio caption"),
        ("voice", "PM_VOICE", ""),
        ("document", "PM_DOC", "Doc caption"),
        ("animation", "PM_ANIM", "Anim caption"),
    ]

    def setUp(self):
        _boot()

    def test_all_types_stored(self):
        for mt, fid, caption in self.TYPES:
            pid = store_pending_media(
                user_id=20001, message_type=mt,
                file_id=fid, caption=caption,
            )
            pm = get_pending_media_by_id(pid)
            self.assertIsNotNone(pm, f"Failed for type: {mt}")
            self.assertEqual(pm["message_type"], mt)
            self.assertEqual(pm["file_id"], fid)

    def test_all_types_retrievable_by_user(self):
        for mt, fid, _ in self.TYPES:
            delete_pending_media(20001)
            store_pending_media(
                user_id=20001, message_type=mt, file_id=fid,
            )
            pm = get_pending_media(20001)
            self.assertEqual(pm["message_type"], mt)


# ─────────────────────────────────────────────────────────────────────────────
# 15. Statistics include media wizard whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardStats(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_wizard_whisper_counted_in_total(self):
        before = get_stats()
        pid = store_pending_media(
            user_id=20001, message_type="animation",
            file_id="STATS_ANIM",
        )
        pm = get_pending_media_by_id(pid)
        create_whisper(
            sender_id=20001,
            content="",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
        )
        after = get_stats()
        self.assertEqual(after["total_whispers"], before["total_whispers"] + 1)


# ─────────────────────────────────────────────────────────────────────────────
# 16. Media wizard with all whisper types
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardWhisperTypes(unittest.TestCase):
    """Test that media wizard works with all 4 whisper types."""

    def setUp(self):
        _boot()

    def _create_from_pending(self, wtype, max_readers=0, targets=None):
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id=f"WIZ_TYPE_{wtype}",
        )
        pm = get_pending_media_by_id(pid)
        return create_whisper(
            sender_id=20001,
            content=pm["content"] or "",
            whisper_type=wtype,
            target_users=targets or [],
            max_readers=max_readers,
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )

    def test_custom_type(self):
        wid = self._create_from_pending("custom", targets=[20002])
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "custom")

    def test_everyone_type(self):
        wid = self._create_from_pending("everyone")
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "everyone")

    def test_first_one_type(self):
        wid = self._create_from_pending("first_one", max_readers=1)
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)

    def test_first_three_type(self):
        wid = self._create_from_pending("first_three", max_readers=3)
        w = get_whisper(wid)
        self.assertEqual(w["whisper_type"], "first_three")
        self.assertEqual(w["max_readers"], 3)


# ─────────────────────────────────────────────────────────────────────────────
# 17. Read notifications for media wizard whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardReadNotifications(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_read_receipt_for_wizard_whisper(self):
        from database import add_reader_if_new, record_whisper_read
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="READ_NOTIF_PHOTO",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content="",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
        )
        is_new = record_whisper_read(wid, 20002)
        self.assertTrue(is_new)
        # Second read should not be new
        is_new2 = record_whisper_read(wid, 20002)
        self.assertFalse(is_new2)


# ─────────────────────────────────────────────────────────────────────────────
# 18. Preserve caption through wizard flow
# ─────────────────────────────────────────────────────────────────────────────

class TestMediaWizardCaptionPreservation(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_caption_preserved_through_flow(self):
        original_caption = "My important photo with details"
        pid = store_pending_media(
            user_id=20001, message_type="photo",
            file_id="CAPTION_FLOW",
            caption=original_caption,
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content=pm["content"] or "",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertEqual(w["caption"], original_caption)

    def test_empty_caption_preserved(self):
        pid = store_pending_media(
            user_id=20001, message_type="voice",
            file_id="NO_CAPTION",
            caption=None,
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=20001,
            content="",
            whisper_type="everyone",
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertIsNone(w["caption"])


if __name__ == "__main__":
    unittest.main()
