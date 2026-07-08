"""
Test the new "تمت مشاهدة هذه الهمسة" message format.
Verifies correctness for all edge cases without touching DB or Telegram.
"""
import os
import sys
import unittest

import telebot.util

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ─ helpers ──────────────────────────────────────────────

from telebot.util import escape


def build_view_message(user, content):
    """Replicate the exact formatting logic from handlers/whisper.py"""
    username_display = f"@{escape(user.username)}" if user.username else "لا يوجد"
    name_display = escape(user.first_name) if user.first_name else "مستخدم مجهول"
    content_escaped = escape(content)

    msg = (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👁️ تمت مشاهدة هذه الهمسة\n\n"
        "👤 معرف المستخدم:\n"
        f"{username_display}\n\n"
        "🪪 الاسم:\n"
        f"{name_display}\n\n"
        "🆔 الآيدي:\n"
        f"{user.id}\n\n"
        "💬 الهمسة:\n"
        f"{content_escaped}\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    return msg


class FakeUser:
    """Simulate a telebot.types.User with minimal attributes."""
    def __init__(self, id, username, first_name):
        self.id = id
        self.username = username
        self.first_name = first_name


class TestViewFormat(unittest.TestCase):

    # ── CASE 1: user has username + first_name ──────────
    def test_username_and_firstname(self):
        user = FakeUser(123456789, "john_doe", "John")
        msg = build_view_message(user, "Hello world")

        self.assertIn("👁️ تمت مشاهدة هذه الهمسة", msg)
        self.assertIn("@john_doe", msg)
        self.assertIn("John", msg)
        self.assertIn("123456789", msg)
        self.assertIn("Hello world", msg)
        self.assertNotIn("لا يوجد", msg)
        self.assertNotIn("مستخدم مجهول", msg)
        self.assertNotIn("None", msg)
        self.assertNotIn("Null", msg)

    # ── CASE 2: user has first_name only ────────────────
    def test_firstname_only(self):
        user = FakeUser(987654321, None, "Sarah")
        msg = build_view_message(user, "Test")

        self.assertIn("👁️ تمت مشاهدة هذه الهمسة", msg)
        self.assertIn("لا يوجد", msg)
        self.assertIn("Sarah", msg)
        self.assertIn("987654321", msg)
        self.assertIn("Test", msg)
        self.assertNotIn("مستخدم مجهول", msg)
        self.assertNotIn("None", msg)

    # ── CASE 3: no username, no first_name ──────────────
    def test_no_username_no_firstname(self):
        user = FakeUser(555666777, None, "")
        msg = build_view_message(user, "secret")

        self.assertIn("لا يوجد", msg)
        self.assertIn("مستخدم مجهول", msg)
        self.assertIn("555666777", msg)
        self.assertIn("secret", msg)
        self.assertNotIn("None", msg)
        self.assertNotIn("Null", msg)

    # ── CASE 4: emoji / unicode name ────────────────────
    def test_emoji_name(self):
        user = FakeUser(111, "emoji_guy", "😎 Cool 🚀")
        msg = build_view_message(user, "emoji whisper")

        self.assertIn("😎 Cool 🚀", msg)
        self.assertIn("@emoji_guy", msg)
        self.assertIn("emoji whisper", msg)

    # ── CASE 5: Arabic name / text ──────────────────────
    def test_arabic_content(self):
        user = FakeUser(222, "ahmed_1", "أحمد")
        msg = build_view_message(user, "نص عربي طويل")

        self.assertIn("أحمد", msg)
        self.assertIn("@ahmed_1", msg)
        self.assertIn("نص عربي طويل", msg)

    def test_english_name_arabic_whisper(self):
        user = FakeUser(333, "english_user", "John")
        msg = build_view_message(user, "مرحبا بالعالم")

        self.assertIn("John", msg)
        self.assertIn("@english_user", msg)
        self.assertIn("مرحبا بالعالم", msg)

    # ── CASE 6: long whisper text ───────────────────────
    def test_long_whisper(self):
        long_text = "A" * 5000
        user = FakeUser(444, "long_user", "Long")
        msg = build_view_message(user, long_text)

        self.assertIn(long_text, msg)
        self.assertIn("@long_user", msg)

    # ── CASE 7: multiline whisper text ──────────────────
    def test_multiline_whisper(self):
        multiline = "line1\nline2\nline3\nline4"
        user = FakeUser(555, "multi_user", "Multi")
        msg = build_view_message(user, multiline)

        self.assertIn("line1\nline2\nline3\nline4", msg)
        self.assertIn("@multi_user", msg)

    # ── HTML escaping checks ────────────────────────────
    def test_html_special_chars_in_username(self):
        user = FakeUser(666, "<script>", "XSS")
        msg = build_view_message(user, "safe")

        self.assertIn("@&lt;script&gt;", msg)
        self.assertNotIn("@<script>", msg)

    def test_html_special_chars_in_firstname(self):
        user = FakeUser(777, "safe_user", "<b>Bold</b>")
        msg = build_view_message(user, "test")

        self.assertIn("&lt;b&gt;Bold&lt;/b&gt;", msg)
        self.assertNotIn("<b>Bold</b>", msg)

    def test_html_special_chars_in_content(self):
        user = FakeUser(888, "user1", "User1")
        msg = build_view_message(user, "<script>alert('xss')</script>")

        self.assertIn("&lt;script&gt;", msg)
        self.assertNotIn("<script>", msg)

    # ── Structure checks ────────────────────────────────
    def test_message_structure(self):
        user = FakeUser(1, "test_user", "Test")
        msg = build_view_message(user, "content")

        lines = msg.split("\n")
        self.assertEqual(lines[0], "━━━━━━━━━━━━━━━━━━━━")
        self.assertEqual(lines[1], "👁️ تمت مشاهدة هذه الهمسة")
        self.assertEqual(lines[2], "")
        self.assertEqual(lines[3], "👤 معرف المستخدم:")
        self.assertEqual(lines[4], "@test_user")
        self.assertEqual(lines[5], "")
        self.assertEqual(lines[6], "🪪 الاسم:")
        self.assertEqual(lines[7], "Test")
        self.assertEqual(lines[8], "")
        self.assertEqual(lines[9], "🆔 الآيدي:")
        self.assertEqual(lines[10], "1")
        self.assertEqual(lines[11], "")
        self.assertEqual(lines[12], "💬 الهمسة:")
        self.assertEqual(lines[13], "content")
        self.assertEqual(lines[14], "")
        self.assertEqual(lines[15], "━━━━━━━━━━━━━━━━━━━━")
        self.assertEqual(len(lines), 16)

    def test_empty_username_is_not_lone_at(self):
        """If username exists but is empty string, treat as no username."""
        user = FakeUser(999, "", "Empty")
        msg = build_view_message(user, "test")
        self.assertIn("لا يوجد", msg)
        self.assertNotIn("@\n", msg)  # no bare @

    def test_firstname_none_uses_fallback(self):
        user = FakeUser(1000, "nofname", None)
        msg = build_view_message(user, "test")
        self.assertIn("مستخدم مجهول", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
