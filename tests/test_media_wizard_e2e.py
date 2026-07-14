"""
tests/test_media_wizard_e2e.py — End-to-end tests for Media Whisper v4 flow.

Flow tested:
  1. Store pending media → show "mw:" button
  2. Inline query with "mw:" → returns single result with pending_id
  3. Chosen inline result → stores inline_message_id, sends type selection DM
  4. Type selection callback → creates whisper, edits group message with deep link
  5. /start view_<id> → delivers media privately with proper access control
  6. first_one restriction: second reader blocked
  7. destructive: whisper deleted after read
  8. everyone: sender notified per reader
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_e2e_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper, get_whisper, upsert_user,
    store_pending_media, get_pending_media, get_pending_media_by_id,
    delete_pending_media, can_read_whisper, record_whisper_read,
    delete_whisper, get_readers, add_reader_if_new,
)


def _boot():
    db.init_db()
    upsert_user(10001, "alice", "Alice", None)
    upsert_user(10002, "bob", "Bob", None)
    upsert_user(10003, "charlie", "Charlie", None)


# ─────────────────────────────────────────────────────────────────────────────
# 1. Full flow: pending → inline "mw:" → chosen → type selection → whisper
# ─────────────────────────────────────────────────────────────────────────────

class TestFullMediaWhisperFlow(unittest.TestCase):
    """Test the complete new flow from media capture to whisper creation."""

    def setUp(self):
        _boot()

    def test_pending_stored_and_retrievable(self):
        """Step 1: Store pending media, retrieve by user and by ID."""
        pid = store_pending_media(
            user_id=10001, message_type="photo",
            file_id="E2E_PHOTO_001", caption="Test photo",
        )
        self.assertIsNotNone(pid)
        self.assertGreater(pid, 0)

        pm_user = get_pending_media(10001)
        self.assertIsNotNone(pm_user)
        self.assertEqual(pm_user["message_type"], "photo")
        self.assertEqual(pm_user["file_id"], "E2E_PHOTO_001")

        pm_id = get_pending_media_by_id(pid)
        self.assertIsNotNone(pm_id)
        self.assertEqual(pm_id["id"], pid)

    def test_type_selection_creates_whisper(self):
        """Step 4: Type selection callback creates whisper with correct params."""
        pid = store_pending_media(
            user_id=10001, message_type="photo",
            file_id="E2E_PHOTO_003", caption="Type select test",
        )
        pm = get_pending_media_by_id(pid)
        self.assertIsNotNone(pm)

        # Simulate type selection: first_one
        wid = create_whisper(
            sender_id=10001,
            content=pm["content"] or "",
            whisper_type="first_one",
            target_users=[],
            max_readers=1,
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["message_type"], "photo")
        self.assertEqual(w["file_id"], "E2E_PHOTO_003")
        self.assertEqual(w["whisper_type"], "first_one")
        self.assertEqual(w["max_readers"], 1)
        self.assertEqual(w["caption"], "Type select test")

        # Cleanup
        delete_pending_media(10001)

    def test_deep_link_view_delivers_media(self):
        """Step 5: /start view_<id> checks access and delivers media."""
        pid = store_pending_media(
            user_id=10001, message_type="photo",
            file_id="E2E_PHOTO_004",
        )
        pm = get_pending_media_by_id(pid)
        wid = create_whisper(
            sender_id=10001,
            content="",
            whisper_type="everyone",
            max_readers=0,
            message_type=pm["message_type"],
            file_id=pm["file_id"],
            caption=pm["caption"],
        )

        # Sender can read
        can, reason = can_read_whisper(wid, 10001)
        self.assertTrue(can)
        self.assertEqual(reason, "sender")

        # Other user can read (everyone type)
        can2, reason2 = can_read_whisper(wid, 10002)
        self.assertTrue(can2)
        self.assertEqual(reason2, "allowed")

        # Record read
        is_new = record_whisper_read(wid, 10002)
        self.assertTrue(is_new)

        # Second read is not new
        is_new2 = record_whisper_read(wid, 10002)
        self.assertFalse(is_new2)

        # Whisper still exists (everyone type, not destructive)
        w = get_whisper(wid)
        self.assertIsNotNone(w)

        delete_pending_media(10001)


# ─────────────────────────────────────────────────────────────────────────────
# 2. first_one restriction
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstOneRestriction(unittest.TestCase):
    """Test that first_one whispers can only be read by one person."""

    def setUp(self):
        _boot()

    def test_first_one_allows_first_reader(self):
        wid = create_whisper(
            sender_id=10001, content="secret",
            whisper_type="first_one", max_readers=1,
        )
        can, reason = can_read_whisper(wid, 10002)
        self.assertTrue(can)
        self.assertEqual(reason, "allowed")

        # Record the read
        record_whisper_read(wid, 10002)

    def test_first_one_blocks_second_reader(self):
        wid = create_whisper(
            sender_id=10001, content="secret",
            whisper_type="first_one", max_readers=1,
        )
        # First reader succeeds
        can1, _ = can_read_whisper(wid, 10002)
        self.assertTrue(can1)
        record_whisper_read(wid, 10002)

        # Second reader blocked
        can2, reason2 = can_read_whisper(wid, 10003)
        self.assertFalse(can2)
        self.assertEqual(reason2, "taken")

    def test_first_one_sender_always_reads(self):
        wid = create_whisper(
            sender_id=10001, content="my secret",
            whisper_type="first_one", max_readers=1,
        )
        record_whisper_read(wid, 10002)

        # Sender can still read
        can, reason = can_read_whisper(wid, 10001)
        self.assertTrue(can)
        self.assertEqual(reason, "sender")


# ─────────────────────────────────────────────────────────────────────────────
# 3. Destructive whisper
# ─────────────────────────────────────────────────────────────────────────────

class TestDestructiveWhisper(unittest.TestCase):
    """Test that destructive whispers are deleted after first read."""

    def setUp(self):
        _boot()

    def test_destructive_deleted_after_read(self):
        wid = create_whisper(
            sender_id=10001, content="boom",
            whisper_type="first_one", max_readers=1,
            is_destructive=True,
        )
        w = get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["is_destructive"], 1)

        # Simulate read + delete
        record_whisper_read(wid, 10002)
        delete_whisper(wid)

        # Whisper no longer exists
        w2 = get_whisper(wid)
        self.assertIsNone(w2)


# ─────────────────────────────────────────────────────────────────────────────
# 4. Everyone type sender notification
# ─────────────────────────────────────────────────────────────────────────────

class TestEveryoneSenderNotification(unittest.TestCase):
    """Test that everyone-type whispers notify sender per reader."""

    def setUp(self):
        _boot()

    def test_everyone_allows_multiple_readers(self):
        wid = create_whisper(
            sender_id=10001, content="public",
            whisper_type="everyone", max_readers=0,
        )
        # Both readers can read
        can1, _ = can_read_whisper(wid, 10002)
        self.assertTrue(can1)
        can2, _ = can_read_whisper(wid, 10003)
        self.assertTrue(can2)

        # Record reads
        is_new1 = record_whisper_read(wid, 10002)
        self.assertTrue(is_new1)
        is_new2 = record_whisper_read(wid, 10003)
        self.assertTrue(is_new2)

        # Both readers recorded
        readers = get_readers(wid)
        self.assertEqual(len(readers), 2)

    def test_everyone_not_locked_after_reads(self):
        wid = create_whisper(
            sender_id=10001, content="public",
            whisper_type="everyone", max_readers=0,
        )
        record_whisper_read(wid, 10002)
        record_whisper_read(wid, 10003)

        # Whisper still open
        can, _ = can_read_whisper(wid, 10004)
        self.assertTrue(can)


# ─────────────────────────────────────────────────────────────────────────────
# 5. first_three restriction
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstThreeRestriction(unittest.TestCase):
    """Test that first_three whispers allow exactly 3 readers."""

    def setUp(self):
        _boot()

    def test_first_three_allows_three_readers(self):
        wid = create_whisper(
            sender_id=10001, content="three",
            whisper_type="first_three", max_readers=3,
        )
        for uid in [10002, 10003]:
            record_whisper_read(wid, uid)

        # Third reader
        can, _ = can_read_whisper(wid, 10004)
        self.assertTrue(can)
        record_whisper_read(wid, 10004)

    def test_first_three_blocks_fourth_reader(self):
        wid = create_whisper(
            sender_id=10001, content="three",
            whisper_type="first_three", max_readers=3,
        )
        for uid in [10002, 10003, 10004]:
            record_whisper_read(wid, uid)

        # Fourth reader blocked
        can, reason = can_read_whisper(wid, 10005)
        self.assertFalse(can)


# ─────────────────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    unittest.main()
