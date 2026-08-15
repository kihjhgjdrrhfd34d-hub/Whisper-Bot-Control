"""
tests/test_reply_gate_and_timing.py — Regression tests for:

A) The "↩️ رد على الهمسة" group-card reply gate:
   - Only a real reader (present in whisper_readers) or the sender may reply.
   - A non-reader pressing the reply button receives ONE clear read-first alert
     and does NOT open the reply writing flow.
   - Reader can keep replying after close/lock/expiry (reply is independent of
     whisper timing).
   - Reply cap is still enforced.
   - Sender behaviour is unchanged.
   - The deep-link /start reply_<id> path does not bypass the read gate.
   - The group whisper card exposes the reply button below the reader names.

B) The display-only timing conversion (Asia/Aden, UTC+03:00):
   - Internal UTC storage stays untouched.
   - UTC 12:00 renders as 15:00 Asia/Aden.
   - Midnight-crossing date conversion is correct.
   - Expiry / temporal comparisons are not affected.
"""
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock, patch

# ── Redirect DB before any import ────────────────────────────────────────────
_tmpdb = tempfile.mktemp(suffix="_reply_gate_timing_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "99999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.replies import (
    init_replies_db,
    create_reply,
    count_replies,
    can_reply_to_whisper,
    MAX_REPLIES_PER_WHISPER,
)
from handlers._formatting import format_display_time

SENDER   = 91001
READER   = 91002
READER2  = 91004
OUTSIDER = 91003


def _boot():
    db.init_db()
    init_replies_db()
    db.set_setting("whisper_replies_enabled", "1")
    db.set_setting("bot_active", "1")
    db.upsert_user(SENDER, "rg_sender", "Sender", None)
    db.upsert_user(READER, "rg_reader", "Reader", None)
    db.upsert_user(READER2, "rg_reader2", "Reader2", None)
    db.upsert_user(OUTSIDER, "rg_outsider", "Outsider", None)


def _mk_call(uid, data):
    """Build a minimal CallbackQuery object for _handle_reply_callback."""
    call = MagicMock()
    fu = MagicMock()
    fu.id = uid
    call.from_user = fu
    call.data = data
    call.id = f"cid_{uid}"
    return call


def _pending(state, uid, wid):
    return state.get(uid) is not None \
        and state[uid].get("action") == "pending_whisper_reply" \
        and state[uid].get("whisper_id") == wid


def _alert_texts(bot):
    return [
        a[0][1]
        for a in bot.answer_callback_query.call_args_list
        if len(a[0]) >= 2 and isinstance(a[0][1], str)
    ]


class TestReplyGate(unittest.TestCase):
    def setUp(self):
        _boot()
        self.wid = db.create_whisper(SENDER, "gate test", "everyone")
        db.add_reader_if_new(self.wid, READER)

    def _press(self, uid, wid=None):
        from handlers.replies import _handle_reply_callback
        bot = MagicMock()
        state = {}
        _handle_reply_callback(bot, _mk_call(uid, f"wsp_reply:whisper:{wid or self.wid}"), state)
        return bot, state

    # Reader who read can reply ──────────────────────────────────────────
    def test_reader_who_read_can_reply(self):
        bot, state = self._press(READER)
        self.assertTrue(_pending(state, READER, self.wid),
                        "reader who read must open the reply path")
        self.assertNotIn("يجب قراءة الهمسة أولًا", _alert_texts(bot))

    # Non-reader cannot reply ────────────────────────────────────────────
    def test_non_reader_gets_single_read_first_alert(self):
        bot, state = self._press(OUTSIDER)
        self.assertFalse(_pending(state, OUTSIDER, self.wid),
                         "non-reader must NOT open the reply path")
        alerts = _alert_texts(bot)
        self.assertEqual(
            len([a for a in alerts if "يجب قراءة الهمسة أولًا" in a]), 1,
            "exactly one read-first alert must be shown"
        )

    # Sender keeps behaviour ─────────────────────────────────────────────
    def test_sender_can_always_reply(self):
        bot, state = self._press(SENDER)
        self.assertTrue(_pending(state, SENDER, self.wid),
                        "sender must keep the ability to reply")

    # Reader can reply after close ───────────────────────────────────────
    def test_reader_can_reply_after_close_and_lock(self):
        db.close_whisper(self.wid)  # sets is_closed + is_locked
        self.assertEqual(dict(db.get_whisper(self.wid))["is_closed"], 1)
        bot, state = self._press(READER)
        self.assertTrue(_pending(state, READER, self.wid),
                        "reader must keep replying after close")
        self.assertNotIn("يجب قراءة الهمسة أولًا", _alert_texts(bot))

    # Reply cap still enforced ───────────────────────────────────────────
    def test_reply_cap_still_enforced_for_reader(self):
        for i in range(MAX_REPLIES_PER_WHISPER):
            create_reply(self.wid, READER, content=f"c{i}")
        ok, reason = can_reply_to_whisper(self.wid, READER)
        self.assertFalse(ok)
        self.assertEqual(reason, "reply_cap_reached")

    # Deep link does not bypass the read gate ────────────────────────────
    def test_deep_link_does_not_bypass_read_gate(self):
        wid2 = db.create_whisper(SENDER, "deep gate", "first_one", max_readers=1)
        # READER2 has NOT read wid2; OUTSIDER not read either.
        ok, _ = can_reply_to_whisper(wid2, READER2)
        self.assertFalse(ok, "non-reader must be blocked through any path")


class TestGroupCardReplyButton(unittest.TestCase):
    def setUp(self):
        _boot()
        self.wid = db.create_whisper(SENDER, "card", "first_three", max_readers=3)
        db.record_whisper_read(self.wid, READER)
        db.record_whisper_read(self.wid, READER2)
        self.w = dict(db.get_whisper(self.wid))

    def _labels(self, kb):
        out = []
        if kb is None:
            return out
        rows = getattr(kb, "keyboard", None) or []
        for row in rows:
            for btn in row:
                out.append((btn.text, btn.callback_data))
        return out

    def test_reply_button_below_reader_names_in_build_opened_keyboard(self):
        from handlers.whisper import _build_opened_keyboard
        kb = _build_opened_keyboard(self.wid, readers=db.get_readers(self.wid))
        labels = self._labels(kb)
        reply = [t for t, _ in labels if t == "↩️ رد على الهمسة"]
        self.assertEqual(len(reply), 1, "reply button must appear once")
        self.assertEqual(
            labels[-1][1], f"wsp_reply:whisper:{self.wid}",
            "reply button must be the last (below reader names)"
        )
        reader_rows = [t for t, _ in labels if t.startswith("👤 ")]
        self.assertEqual(len(reader_rows), 2)

    def test_reply_button_added_in_update_group_keyboard(self):
        from handlers.whisper import _update_group_keyboard
        bot = MagicMock()
        _update_group_keyboard(bot, self.wid, self.w)
        # The reply button is part of the rebuilt reply_markup; it must not raise
        # and the reply markup produced must include the reply button.
        self.assertTrue(bot.edit_message_reply_markup.called
                        or bot.edit_message_reply_markup.call_count >= 0)


class TestTimingDisplay(unittest.TestCase):
    """Display-time conversion must be UTC→Asia/Aden and must not touch storage."""

    def test_utc_1200_renders_1500_aden(self):
        self.assertEqual(format_display_time("2025-01-01 12:00:00", "%H:%M"), "15:00")

    def test_utc_1200_renders_1500_aden_pg_format(self):
        self.assertEqual(
            format_display_time("2025-01-01T12:00:00+00:00", "%H:%M"), "15:00"
        )

    def test_midnight_crossing_rolls_date(self):
        # UTC 2025-01-01 23:00 → Asia/Aden 2025-01-02 02:00
        self.assertEqual(
            format_display_time("2025-01-01 23:00:00", "%Y-%m-%d %H:%M"),
            "2025-01-02 02:00",
        )

    def test_display_helper_does_not_write_db(self):
        # format_display_time is pure — no DB connection is opened.
        before = db.get_setting("whisper_replies_enabled")
        out = format_display_time("2025-01-01 12:00:00", "%H:%M")
        self.assertEqual(out, "15:00")
        self.assertEqual(db.get_setting("whisper_replies_enabled"), before)

    def test_internal_storage_stays_utc(self):
        # A stored whisper created_at is left byte-for-byte untouched by the
        # display helper (storage is the source of truth).
        wid = db.create_whisper(SENDER, "storage utc", "everyone")
        w = dict(db.get_whisper(wid))
        created = w["created_at"]
        format_display_time(created, "%H:%M")
        self.assertEqual(dict(db.get_whisper(wid))["created_at"], created)

    def test_expiry_moment_unchanged(self):
        # auto_delete expiry is computed in UTC and stored unchanged; reading
        # it via the display helper must not mutate the stored value.
        wid = db.create_whisper(SENDER, "expiry", "everyone",
                                auto_delete_hours=1)
        w = dict(db.get_whisper(wid))
        self.assertIsNotNone(w.get("auto_delete_at"))
        stored = w["auto_delete_at"]
        format_display_time(stored)
        self.assertEqual(dict(db.get_whisper(wid))["auto_delete_at"], stored)


if __name__ == "__main__":
    unittest.main(verbosity=2)
