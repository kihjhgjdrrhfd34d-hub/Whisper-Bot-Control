"""
tests/test_access_policy.py — Regression tests for the centralised access-policy
layer (database/access_policy.py).

These tests exercise the pure policy helpers directly.  The end-to-end
behaviour of can_reply_to_whisper (sender/reader/outsider, open/closed/locked,
cap reached) is already covered by tests/test_replies.py, tests/test_dashboard.py
and tests/test_reply_deep_link.py, so it is NOT duplicated here — the point of
this file is the layer itself: it must keep classifying exactly as before and
must never let a non-participant reply or block an authorised participant.
"""
import os
import sys
import unittest

os.environ.setdefault("BOT_TOKEN", "0:test_token_placeholder")
os.environ.setdefault("ADMIN_IDS", "99999")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.access_policy import (  # noqa: E402
    actor_role,
    is_participant_role,
    reply_gate,
)


class TestActorRole(unittest.TestCase):
    """actor_role must classify sender first, then reader, else outsider."""

    def test_sender_wins_over_reader(self):
        # A user who is BOTH sender and reader is classified as sender.
        self.assertEqual(actor_role(10, 10, is_reader=True), "sender")

    def test_sender(self):
        self.assertEqual(actor_role(10, 10, is_reader=False), "sender")

    def test_reader(self):
        self.assertEqual(actor_role(10, 20, is_reader=True), "reader")

    def test_outsider(self):
        self.assertEqual(actor_role(10, 20, is_reader=False), "outsider")


class TestIsParticipantRole(unittest.TestCase):
    def test_sender_is_participant(self):
        self.assertTrue(is_participant_role("sender"))

    def test_reader_is_participant(self):
        self.assertTrue(is_participant_role("reader"))

    def test_outsider_is_not_participant(self):
        self.assertFalse(is_participant_role("outsider"))


class TestReplyGate(unittest.TestCase):
    """reply_gate mirrors can_reply_to_whisper's participant + cap branch."""

    def test_sender_allowed(self):
        ok, reason = reply_gate("sender", reply_count=0, max_replies=50)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_reader_allowed(self):
        ok, reason = reply_gate("reader", reply_count=3, max_replies=50)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_outsider_blocked_even_when_room(self):
        ok, reason = reply_gate("outsider", reply_count=0, max_replies=50)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_participant")

    def test_outsider_blocked_even_at_cap(self):
        # The participant check comes first — outsiders stay blocked even if
        # the reply cap is also reached.
        ok, reason = reply_gate("outsider", reply_count=50, max_replies=50)
        self.assertFalse(ok)
        self.assertEqual(reason, "not_participant")

    def test_cap_reached_blocks_participant(self):
        ok, reason = reply_gate("reader", reply_count=50, max_replies=50)
        self.assertFalse(ok)
        self.assertEqual(reason, "reply_cap_reached")

    def test_at_cap_minus_one_allowed(self):
        ok, reason = reply_gate("sender", reply_count=49, max_replies=50)
        self.assertTrue(ok)

    def test_gate_ignores_closed_locked_state(self):
        # The gate has no closed/locked input on purpose: closing/locking a
        # whisper stops new reads, never replies from authorised participants.
        ok, reason = reply_gate("reader", reply_count=1, max_replies=50)
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
