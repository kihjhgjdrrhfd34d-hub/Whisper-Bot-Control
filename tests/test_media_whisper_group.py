"""
tests/test_media_whisper_group.py — Tests for direct media whispers from group replies.

Covers:
  1. All 6 media types create whispers when replying in a group
  2. Permission checks (banned, bot_active, public_whispers_enabled)
  3. Anti-spam rate limiting
  4. Self-reply is silently ignored
  5. Non-group messages are ignored
  6. Non-reply messages are ignored
  7. Media labels in group messages
  8. Inline keyboard structure
  9. Auto-delete behavior
  10. Dashboard notification is sent
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_media_group_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    get_whisper, upsert_user,
    check_whisper_rate_limit, record_whisper_timestamp,
    ensure_group_settings, update_group_setting,
)


def _boot():
    """Full reset: re-init DB and create fresh users."""
    db.init_db()
    with db.get_conn() as conn:
        conn.execute("DELETE FROM whispers")
        conn.execute("DELETE FROM whisper_readers")
        conn.execute("DELETE FROM curious_ones")
        conn.execute("DELETE FROM whisper_timestamps")
        conn.execute("DELETE FROM group_settings")
        conn.execute("UPDATE users SET is_banned=0")
        conn.commit()
    upsert_user(70001, "alice", "Alice", None)
    upsert_user(70002, "bob", "Bob", None)
    upsert_user(70003, "charlie", "Charlie", None)


def _make_media_msg(content_type, sender_id, chat_id, replied_to_id=None, **kwargs):
    """Create a mock Telegram message with media content."""
    msg = MagicMock()
    msg.content_type = content_type
    msg.from_user = MagicMock()
    msg.from_user.id = sender_id
    msg.from_user.username = f"user{sender_id}"
    msg.from_user.first_name = f"User{sender_id}"
    msg.from_user.last_name = None

    msg.chat = MagicMock()
    msg.chat.id = chat_id
    msg.chat.type = "group"

    msg.message_id = kwargs.get("message_id", 100)

    if replied_to_id is not None:
        msg.reply_to_message = MagicMock()
        msg.reply_to_message.from_user = MagicMock()
        msg.reply_to_message.from_user.id = replied_to_id
        msg.reply_to_message.from_user.username = f"user{replied_to_id}"
        msg.reply_to_message.from_user.first_name = f"User{replied_to_id}"
        msg.reply_to_message.from_user.last_name = None
    else:
        msg.reply_to_message = None

    if content_type == "photo":
        photo = MagicMock()
        photo.file_id = kwargs.get("file_id", "PHOTO_GID")
        msg.photo = [MagicMock(), photo]
        msg.caption = kwargs.get("caption", "Nice photo")
    elif content_type == "video":
        msg.video = MagicMock()
        msg.video.file_id = kwargs.get("file_id", "VIDEO_GID")
        msg.caption = kwargs.get("caption", "Cool video")
    elif content_type == "voice":
        msg.voice = MagicMock()
        msg.voice.file_id = kwargs.get("file_id", "VOICE_GID")
        msg.caption = kwargs.get("caption", "")
    elif content_type == "audio":
        msg.audio = MagicMock()
        msg.audio.file_id = kwargs.get("file_id", "AUDIO_GID")
        msg.caption = kwargs.get("caption", "My song")
    elif content_type == "document":
        msg.document = MagicMock()
        msg.document.file_id = kwargs.get("file_id", "DOC_GID")
        msg.caption = kwargs.get("caption", "File doc")
    elif content_type == "location":
        msg.location = MagicMock()
        msg.location.latitude = kwargs.get("latitude", 33.3128)
        msg.location.longitude = kwargs.get("longitude", 44.3615)

    return msg


# ── Module-level cached handlers (registered once) ──────────────────────────
_cached_handlers = None
_cached_bot = None


def _get_handlers():
    global _cached_handlers, _cached_bot
    if _cached_handlers is None:
        from handlers.whisper import _register_message_handlers

        bot = MagicMock()
        bot.get_me.return_value = MagicMock(username="testbot")
        captured = {}

        def cap(**kwargs):
            def deco(f):
                ct = kwargs.get("content_types", [])
                for c in ct:
                    captured[c] = f
                return f
            return deco

        bot.message_handler = cap
        _register_message_handlers(bot, {})
        _cached_handlers = captured
        _cached_bot = bot
    return _cached_handlers, _cached_bot


def _handler(content_type):
    handlers, bot = _get_handlers()
    return handlers[content_type], bot


# ─────────────────────────────────────────────────────────────────────────────
# 1. All 6 media types create whispers
# ─────────────────────────────────────────────────────────────────────────────

class TestAllMediaTypesCreateWhispers(unittest.TestCase):
    def setUp(self):
        _boot()

    def _count_whispers(self, sender_id, media_type):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM whispers WHERE sender_id=? AND message_type=?",
                (sender_id, media_type),
            ).fetchone()[0]

    def test_photo(self):
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002,
                              file_id="PHOTO_123", caption="Test photo")
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "photo"), 1)
        with db.get_conn() as conn:
            w = conn.execute(
                "SELECT * FROM whispers WHERE sender_id=70001 AND message_type='photo'"
            ).fetchone()
        self.assertEqual(dict(w)["file_id"], "PHOTO_123")

    def test_video(self):
        fn, bot = _handler("video")
        msg = _make_media_msg("video", 70001, -100100, replied_to_id=70002,
                              file_id="VIDEO_123", caption="Test video")
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "video"), 1)

    def test_audio(self):
        fn, bot = _handler("audio")
        msg = _make_media_msg("audio", 70001, -100100, replied_to_id=70002,
                              file_id="AUDIO_123", caption="Test audio")
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "audio"), 1)

    def test_voice(self):
        fn, bot = _handler("voice")
        msg = _make_media_msg("voice", 70001, -100100, replied_to_id=70002,
                              file_id="VOICE_123")
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "voice"), 1)

    def test_document(self):
        fn, bot = _handler("document")
        msg = _make_media_msg("document", 70001, -100100, replied_to_id=70002,
                              file_id="DOC_123", caption="Test doc")
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "document"), 1)

    def test_location(self):
        fn, bot = _handler("location")
        msg = _make_media_msg("location", 70001, -100100, replied_to_id=70002,
                              latitude=33.3128, longitude=44.3615)
        fn(msg)
        self.assertEqual(self._count_whispers(70001, "location"), 1)
        with db.get_conn() as conn:
            w = conn.execute(
                "SELECT * FROM whispers WHERE sender_id=70001 AND message_type='location'"
            ).fetchone()
        self.assertAlmostEqual(dict(w)["location_lat"], 33.3128)
        self.assertAlmostEqual(dict(w)["location_lon"], 44.3615)


# ─────────────────────────────────────────────────────────────────────────────
# 2. Permission checks
# ─────────────────────────────────────────────────────────────────────────────

class TestPermissionChecks(unittest.TestCase):
    def setUp(self):
        _boot()

    def _count_whispers(self, sender_id):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (sender_id,)
            ).fetchone()[0]

    def test_banned_user_ignored(self):
        db.ban_user(70001)
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002)
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)

    def test_bot_inactive_ignored(self):
        db.set_setting("bot_active", "0")
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002)
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)
        db.set_setting("bot_active", "1")

    def test_public_whispers_disabled_ignored(self):
        ensure_group_settings(-100100)
        update_group_setting(-100100, "public_whispers_enabled", 0)
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002)
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)

    def test_self_reply_ignored(self):
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70001)
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)

    def test_non_group_ignored(self):
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, 70002, replied_to_id=70003)
        msg.chat.type = "private"
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)

    def test_non_reply_ignored(self):
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100)
        fn(msg)
        self.assertEqual(self._count_whispers(70001), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Anti-spam
# ─────────────────────────────────────────────────────────────────────────────

class TestAntiSpam(unittest.TestCase):
    def setUp(self):
        _boot()
        ensure_group_settings(-100100)
        _, bot = _get_handlers()
        bot.reset_mock()

    def _count_whispers(self, sender_id):
        with db.get_conn() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM whispers WHERE sender_id=?", (sender_id,)
            ).fetchone()[0]

    def test_rate_limited_blocks(self):
        for _ in range(5):
            record_whisper_timestamp(70001, -100100)
        fn, bot = _handler("photo")
        msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002)
        fn(msg)
        bot.reply_to.assert_called()
        self.assertIn("تجاوزت", bot.reply_to.call_args[0][1])
        self.assertEqual(self._count_whispers(70001), 0)

    def test_disabled_allows_unlimited(self):
        update_group_setting(-100100, "spam_limit_enabled", 0)
        fn, bot = _handler("photo")
        for _ in range(10):
            msg = _make_media_msg("photo", 70001, -100100, replied_to_id=70002)
            fn(msg)
        self.assertEqual(self._count_whispers(70001), 10)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Group message content and keyboard
# ─────────────────────────────────────────────────────────────────────────────

class TestGroupMessage(unittest.TestCase):
    def setUp(self):
        _boot()
        _, bot = _get_handlers()
        bot.reset_mock()

    def _get_sent_text(self, bot):
        calls = bot.send_message.call_args_list
        for c in reversed(calls):
            if c[0][0] == -100100:
                return c[0][1]
        return None

    def _get_sent_kb(self, bot):
        calls = bot.send_message.call_args_list
        for c in reversed(calls):
            if c[0][0] == -100100:
                if len(c.args) > 2:
                    return c.args[2]
                return c[1].get("reply_markup")
        return None

    def test_photo_label(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        self.assertIn("صورة", self._get_sent_text(bot))

    def test_video_label(self):
        fn, bot = _handler("video")
        fn(_make_media_msg("video", 70001, -100100, replied_to_id=70002))
        self.assertIn("فيديو", self._get_sent_text(bot))

    def test_voice_label(self):
        fn, bot = _handler("voice")
        fn(_make_media_msg("voice", 70001, -100100, replied_to_id=70002))
        self.assertIn("تسجيل صوتي", self._get_sent_text(bot))

    def test_audio_label(self):
        fn, bot = _handler("audio")
        fn(_make_media_msg("audio", 70001, -100100, replied_to_id=70002))
        self.assertIn("ملف صوتي", self._get_sent_text(bot))

    def test_document_label(self):
        fn, bot = _handler("document")
        fn(_make_media_msg("document", 70001, -100100, replied_to_id=70002))
        self.assertIn("مستند", self._get_sent_text(bot))

    def test_location_label(self):
        fn, bot = _handler("location")
        fn(_make_media_msg("location", 70001, -100100, replied_to_id=70002))
        self.assertIn("موقع", self._get_sent_text(bot))

    def test_keyboard_has_read_button(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        kb = self._get_sent_kb(bot)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if "اضغط للرؤيه" in btn.text:
                    found = True
                    self.assertTrue(btn.callback_data.startswith("read:"))
        self.assertTrue(found)

    def test_keyboard_has_reply_button(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        kb = self._get_sent_kb(bot)
        found = False
        for row in kb.keyboard:
            for btn in row:
                if "رد على الهمسة" in btn.text:
                    found = True
                    self.assertIn("t.me/testbot", btn.url)
        self.assertTrue(found)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Auto-delete
# ─────────────────────────────────────────────────────────────────────────────

class TestAutoDelete(unittest.TestCase):
    def setUp(self):
        _boot()
        ensure_group_settings(-100100)
        _, bot = _get_handlers()
        bot.reset_mock()

    def _get_last_whisper(self):
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT auto_delete_at FROM whispers ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def test_auto_delete_set_when_enabled(self):
        db.set_setting("auto_delete_enabled", "1")
        db.set_setting("auto_delete_hours", "48")
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        w = self._get_last_whisper()
        self.assertIsNotNone(w["auto_delete_at"])
        db.set_setting("auto_delete_enabled", "0")

    def test_no_auto_delete_when_disabled(self):
        db.set_setting("auto_delete_enabled", "0")
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        w = self._get_last_whisper()
        self.assertIsNone(w["auto_delete_at"])

    def test_group_auto_delete_minutes(self):
        update_group_setting(-100100, "auto_delete_minutes", 30)
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        w = self._get_last_whisper()
        self.assertIsNotNone(w["auto_delete_at"])


# ─────────────────────────────────────────────────────────────────────────────
# 6. Dashboard notification
# ─────────────────────────────────────────────────────────────────────────────

class TestDashboardNotification(unittest.TestCase):
    def setUp(self):
        _boot()
        _, bot = _get_handlers()
        bot.reset_mock()

    def test_dashboard_sent_to_sender(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        dm_calls = [c for c in bot.send_message.call_args_list if c[0][0] == 70001]
        self.assertGreater(len(dm_calls), 0)


# ─────────────────────────────────────────────────────────────────────────────
# 7. Timestamp recorded
# ─────────────────────────────────────────────────────────────────────────────

class TestTimestampRecorded(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_timestamp_after_whisper(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        allowed, count = check_whisper_rate_limit(70001, -100100)
        self.assertTrue(allowed)
        self.assertEqual(count, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 8. Whisper type and target
# ─────────────────────────────────────────────────────────────────────────────

class TestWhisperTypeAndTarget(unittest.TestCase):
    def setUp(self):
        _boot()

    def _last_whisper(self):
        with db.get_conn() as conn:
            row = conn.execute(
                "SELECT whisper_type, target_users FROM whispers ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None

    def test_type_is_everyone(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        w = self._last_whisper()
        self.assertEqual(w["whisper_type"], "everyone")

    def test_target_contains_replied_user(self):
        fn, bot = _handler("photo")
        fn(_make_media_msg("photo", 70001, -100100, replied_to_id=70002))
        w = self._last_whisper()
        targets = json.loads(w["target_users"])
        self.assertIn(70002, targets)


# ─────────────────────────────────────────────────────────────────────────────
# 9. Backward compatibility
# ─────────────────────────────────────────────────────────────────────────────

class TestBackwardCompatibility(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_text_whisper_still_works(self):
        from database import create_whisper
        wid = create_whisper(70001, "test text", "everyone")
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["content"], "test text")

    def test_media_whisper_via_create_whisper(self):
        from database import create_whisper
        wid = create_whisper(
            sender_id=70001, content="cap", whisper_type="everyone",
            message_type="photo", file_id="FID",
        )
        w = get_whisper(wid)
        self.assertEqual(w["message_type"], "photo")
        self.assertEqual(w["file_id"], "FID")


if __name__ == "__main__":
    unittest.main(verbosity=2)
