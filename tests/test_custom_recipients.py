"""
tests/test_custom_recipients.py — Custom whisper recipient rules.

Covers the two features:
  1. Legacy custom whispers whose targets were stored as @username strings
     must still resolve (case-insensitively) for the right user.
  2. Multi-recipient custom whispers (user IDs and/or usernames) with
     per-recipient single-read semantics ("already_read").

Also guards that the other whisper types (everyone / first_one / first_three)
are untouched by the changes.
"""
import os
import sys
import unittest
import tempfile
import atexit

_tmpdb = tempfile.mktemp(suffix="_custom_recipients_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import (
    create_whisper,
    upsert_user,
    can_read_whisper,
    record_whisper_read,
)
from services.whisper_service import resolve_recipients

SENDER = 90001
ALICE = 90010
BOB = 90020
CAROL = 90030


class CustomRecipientsBase(unittest.TestCase):
    def setUp(self):
        db.init_db()
        upsert_user(SENDER, "sender", "Sender", None)
        upsert_user(ALICE, "alice", "Alice", None)
        upsert_user(BOB, "bob", "Bob", None)
        upsert_user(CAROL, "carol", "Carol", None)


class TestLegacyUsernameTargets(CustomRecipientsBase):
    """Old rows may store targets as username strings instead of user IDs."""

    def test_legacy_username_string_allows_target(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["alice"])
        can, reason = can_read_whisper(wid, ALICE)
        self.assertTrue(can)
        self.assertEqual(reason, "allowed")

    def test_legacy_username_string_blocks_others(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["alice"])
        can, reason = can_read_whisper(wid, BOB)
        self.assertFalse(can)
        self.assertEqual(reason, "not_target")

    def test_legacy_username_case_insensitive(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["ALICE"])
        can, _ = can_read_whisper(wid, ALICE)
        self.assertTrue(can)

    def test_legacy_username_with_at_prefix(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["@bob"])
        can, _ = can_read_whisper(wid, BOB)
        self.assertTrue(can)

    def test_legacy_mixed_ints_and_usernames(self):
        wid = create_whisper(
            SENDER, "legacy", "custom", target_users=[BOB, "carol"]
        )
        self.assertTrue(can_read_whisper(wid, BOB)[0])
        self.assertTrue(can_read_whisper(wid, CAROL)[0])
        self.assertFalse(can_read_whisper(wid, ALICE)[0])

    def test_legacy_string_number_matches_user(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["90010"])
        can, _ = can_read_whisper(wid, ALICE)
        self.assertTrue(can)

    def test_legacy_username_read_then_already_read(self):
        wid = create_whisper(SENDER, "legacy", "custom", target_users=["alice"])
        self.assertTrue(record_whisper_read(wid, ALICE))
        can, reason = can_read_whisper(wid, ALICE)
        self.assertFalse(can)
        self.assertEqual(reason, "already_read")


class TestMultiRecipient(CustomRecipientsBase):
    def test_each_recipient_allowed(self):
        wid = create_whisper(
            SENDER, "multi", "custom", target_users=[ALICE, BOB, CAROL]
        )
        for uid in (ALICE, BOB, CAROL):
            can, reason = can_read_whisper(wid, uid)
            self.assertTrue(can, f"uid {uid} should be allowed")
            self.assertEqual(reason, "allowed")

    def test_intruder_blocked(self):
        wid = create_whisper(SENDER, "multi", "custom", target_users=[ALICE, BOB])
        can, reason = can_read_whisper(wid, CAROL)
        self.assertFalse(can)
        self.assertEqual(reason, "not_target")

    def test_sender_not_a_target_blocked(self):
        wid = create_whisper(SENDER, "multi", "custom", target_users=[ALICE])
        can, reason = can_read_whisper(wid, SENDER)
        self.assertFalse(can)
        self.assertEqual(reason, "not_target")

    def test_each_recipient_opens_once(self):
        wid = create_whisper(SENDER, "once", "custom", target_users=[ALICE, BOB])
        self.assertTrue(record_whisper_read(wid, ALICE))
        can, reason = can_read_whisper(wid, ALICE)
        self.assertFalse(can)
        self.assertEqual(reason, "already_read")
        # The other recipient is still allowed.
        can, reason = can_read_whisper(wid, BOB)
        self.assertTrue(can)
        self.assertEqual(reason, "allowed")

    def test_duplicate_read_not_recorded(self):
        wid = create_whisper(SENDER, "dup", "custom", target_users=[ALICE])
        self.assertTrue(record_whisper_read(wid, ALICE))
        self.assertFalse(record_whisper_read(wid, ALICE))


class TestResolveRecipients(CustomRecipientsBase):
    def test_mixed_usernames_and_ids_dedup(self):
        resolved, unresolved = resolve_recipients(
            ["@alice", "90020", "alice", BOB, "@ALICE"]
        )
        self.assertEqual(unresolved, [])
        self.assertEqual(sorted(resolved), [ALICE, BOB])

    def test_unresolved_username_reported(self):
        resolved, unresolved = resolve_recipients(["@ghost", "90020"])
        self.assertEqual(resolved, [BOB])
        self.assertEqual(unresolved, ["@ghost"])

    def test_empty_input(self):
        self.assertEqual(resolve_recipients([]), ([], []))
        self.assertEqual(resolve_recipients(None), ([], []))


class TestOtherTypesUnaffected(CustomRecipientsBase):
    def test_first_one_still_works(self):
        wid = create_whisper(SENDER, "fo", "first_one")
        self.assertTrue(can_read_whisper(wid, ALICE)[0])
        record_whisper_read(wid, ALICE)
        can, reason = can_read_whisper(wid, BOB)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_first_three_still_works(self):
        upsert_user(90040, "dan", "Dan", None)
        wid = create_whisper(SENDER, "f3", "first_three")
        self.assertTrue(record_whisper_read(wid, ALICE))
        self.assertTrue(record_whisper_read(wid, BOB))
        self.assertTrue(record_whisper_read(wid, CAROL))
        # First three readers are registered and the whisper auto-locks.
        can, reason = can_read_whisper(wid, 90040)
        self.assertFalse(can)
        self.assertEqual(reason, "taken")

    def test_everyone_still_works(self):
        wid = create_whisper(SENDER, "ev", "everyone")
        self.assertTrue(can_read_whisper(wid, ALICE)[0])
        record_whisper_read(wid, ALICE)
        self.assertTrue(can_read_whisper(wid, BOB)[0])


if __name__ == "__main__":
    unittest.main()
