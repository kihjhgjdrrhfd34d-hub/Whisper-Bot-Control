"""
tests/test_variant_read_notifications.py — Sender read-notification variant fix.

The sender's DM read notification must show the SAME deterministic variant the
specific reader actually saw (resolve_variant), for every whisper type, instead
of the stored ``content`` (which for variant whispers is the FIRST variant).

Verifies at builder level (services.whisper_service) and at integration level
(handlers.whisper._complete_read_flow, the exact path that fires the sender
notifications):

  1. variant + everyone  — reader A sees variant A, sender notification has
                           variant A; reader B sees variant B, sender has B.
  2. variant + first_three — each of 3 readers gets their own deterministic
                           variant, and each sender notification matches it.
  3. variant + first_five  — same for 5 readers.
  4. variant + first_one   — the notification shows the reader's actual variant,
                           NOT statically ``variants[0]``.
  5. non-variant whispers  — old notification text (``content``) unchanged.
  6. malformed/missing variants — resolve_variant falls back to ``content``
                           with no crash.
  7. no leak — a reader's notification never contains another variant from the
                           list, and the reader never receives any extra variant.
"""
import json
import os
import sys
import unittest
import tempfile
import atexit
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_variant_read_notifications.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.whisper_conditions import init_whisper_conditions_db
from services.whisper_service import (
    resolve_variant,
    build_read_receipt_message,
    build_first_three_read_notification,
    build_first_one_notification,
)
from handlers.whisper import _complete_read_flow

SENDER = 70001
READER_A = 70002
READER_B = 70003
READER_C = 70004
READER_D = 70005
READER_E = 70006


class _Reader:
    def __init__(self, uid, username=None, first_name=None):
        self.id = uid
        self.username = username or f"user{uid}"
        self.first_name = first_name or f"User{uid}"
        self.last_name = None


def _boot():
    db.init_db()
    init_whisper_conditions_db()
    for uid, uname, fname in [
        (SENDER, "sender70001", "Sender"),
        (READER_A, "reader70002", "ReaderA"),
        (READER_B, "reader70003", "ReaderB"),
        (READER_C, "reader70004", "ReaderC"),
        (READER_D, "reader70005", "ReaderD"),
        (READER_E, "reader70006", "ReaderE"),
    ]:
        db.upsert_user(uid, uname, fname, None)


def _make_variant(first_content, variants, wtype, max_readers=0, target_users=None):
    return db.create_whisper(
        SENDER, first_content, wtype,
        target_users=target_users or [],
        max_readers=max_readers,
        conditions_data=json.dumps({"variants": variants}, ensure_ascii=False),
    )


def _make_normal(content, wtype, max_readers=0, target_users=None):
    return db.create_whisper(
        SENDER, content, wtype,
        target_users=target_users or [],
        max_readers=max_readers,
    )


def _w(wid):
    return dict(db.get_whisper(wid))


def _variants_of(w):
    raw = w.get("conditions_data")
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [v for v in (raw or {}).get("variants") or [] if isinstance(v, str) and v.strip()]


def _msgs_to(bot, chat_id):
    """All texts bot.send_message sent to a given chat id."""
    out = []
    for args, kwargs in bot.send_message.call_args_list:
        target = args[0] if args else kwargs.get("chat_id")
        text = args[1] if len(args) > 1 else kwargs.get("text")
        if target == chat_id:
            out.append(text)
    return out


def _find_reader_away_from_first(w, variants):
    """Return a deterministic reader id whose resolved variant != variants[0]."""
    first = variants[0]
    for uid in range(71002, 71200):
        if resolve_variant(w, uid) != first:
            return uid
    return None


# ── Builder-level: everyone / read receipt ────────────────────────────────

class TestBuilderEveryoneVariant(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["أنت أول من يفهمني", "هناك من يفكر بك الآن", "عندك سر لا يعرفه أحد"]
        self.wid = _make_variant(self.variants[0], self.variants, "everyone")
        self.w = _w(self.wid)

    def test_receipt_matches_each_reader_variant_and_no_leak(self):
        for uid in (READER_A, READER_B, READER_C):
            expected = resolve_variant(self.w, uid)
            self.assertIn(expected, self.variants)
            receipt = build_read_receipt_message(_Reader(uid), self.w)
            self.assertIn(expected, receipt)
            for other in self.variants:
                if other != expected:
                    self.assertNotIn(other, receipt)

    def test_receipt_uses_reader_variant_not_first_variant(self):
        uid = _find_reader_away_from_first(self.w, self.variants)
        self.assertIsNotNone(uid)
        expected = resolve_variant(self.w, uid)
        self.assertNotEqual(expected, self.variants[0])
        receipt = build_read_receipt_message(_Reader(uid), self.w)
        self.assertIn(expected, receipt)
        self.assertNotIn(self.variants[0], receipt)

    def test_stored_content_unchanged(self):
        self.assertEqual(self.w["content"], self.variants[0])


# ── Builder-level: first_three / first_five ───────────────────────────────

class TestBuilderFirstThreeVariant(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["النسخة الأولى", "النسخة الثانية", "النسخة الثالثة"]
        self.wid = _make_variant(self.variants[0], self.variants, "first_three", max_readers=3)
        self.w = _w(self.wid)

    def test_first_three_notification_matches_each_reader_variant_no_leak(self):
        for uid in (READER_A, READER_B, READER_C):
            expected = resolve_variant(self.w, uid)
            notify = build_first_three_read_notification(_Reader(uid), self.w)
            self.assertIn(expected, notify)
            for other in self.variants:
                if other != expected:
                    self.assertNotIn(other, notify)


# ── Builder-level: first_one ──────────────────────────────────────────────

class TestBuilderFirstOneVariant(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["نسخة أول شخص أ", "نسخة أول شخص ب", "نسخة أول شخص ج"]
        self.wid = _make_variant(self.variants[0], self.variants, "first_one", max_readers=1)
        self.w = _w(self.wid)

    def test_first_one_notification_shows_reader_variant_not_first(self):
        uid = _find_reader_away_from_first(self.w, self.variants)
        self.assertIsNotNone(uid)
        expected = resolve_variant(self.w, uid)
        self.assertNotEqual(expected, self.variants[0])
        notify = build_first_one_notification(_Reader(uid), self.w)
        self.assertIn(expected, notify)
        self.assertNotIn(self.variants[0], notify)


# ── Builder-level: non-variant unchanged ──────────────────────────────────

class TestBuilderNonVariantUnchanged(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_read_receipt_non_variant_shows_content(self):
        wid = _make_normal("نص عادي للجميع", "everyone")
        w = _w(wid)
        receipt = build_read_receipt_message(_Reader(READER_A), w)
        self.assertIn("نص عادي للجميع", receipt)

    def test_first_three_notification_non_variant_shows_content(self):
        wid = _make_normal("نص عادي لثلاثة", "first_three", max_readers=3)
        w = _w(wid)
        notify = build_first_three_read_notification(_Reader(READER_A), w)
        self.assertIn("نص عادي لثلاثة", notify)

    def test_first_one_notification_non_variant_shows_content(self):
        wid = _make_normal("نص عادي لأول شخص", "first_one", max_readers=1)
        w = _w(wid)
        notify = build_first_one_notification(_Reader(READER_A), w)
        self.assertIn("نص عادي لأول شخص", notify)


# ── Builder-level: malformed / missing variants fallback ──────────────────

class TestBuilderMalformedVariantsFallback(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_missing_conditions_data_falls_back_to_content(self):
        wid = _make_normal("المحتوى الأصلي", "everyone")
        w = _w(wid)
        self.assertEqual(resolve_variant(w, READER_A), "المحتوى الأصلي")
        receipt = build_read_receipt_message(_Reader(READER_A), w)
        self.assertIn("المحتوى الأصلي", receipt)

    def test_bad_json_conditions_data_falls_back(self):
        wid = db.create_whisper(SENDER, "أصل", "everyone",
                                conditions_data="{not valid json")
        w = _w(wid)
        self.assertEqual(resolve_variant(w, READER_A), "أصل")
        self.assertIn("أصل", build_read_receipt_message(_Reader(READER_A), w))

    def test_empty_variants_list_falls_back(self):
        wid = _make_variant("أصل", [], "everyone")
        w = _w(wid)
        self.assertEqual(resolve_variant(w, READER_A), "أصل")
        self.assertIn("أصل", build_read_receipt_message(_Reader(READER_A), w))

    def test_blank_variants_falls_back(self):
        wid = _make_variant("أصل", ["   ", "", None], "everyone")
        w = _w(wid)
        self.assertEqual(resolve_variant(w, READER_A), "أصل")
        self.assertIn("أصل", build_read_receipt_message(_Reader(READER_A), w))

    def test_none_whisper_no_crash(self):
        self.assertEqual(resolve_variant(None, READER_A), "")
        self.assertEqual(build_read_receipt_message(_Reader(READER_A), None), "👁 قرأ User70002 همستك!")


# ── Integration: _complete_read_flow ──────────────────────────────────────

class _FlowCase(unittest.TestCase):

    def setUp(self):
        _boot()
        self.bot = MagicMock()
        self.bot.send_message = MagicMock()

    def _read(self, wid, reader):
        _complete_read_flow(
            self.bot, call=None, user=reader,
            whisper_id=wid, w=_w(wid), is_destructive=False,
        )
        sender_texts = _msgs_to(self.bot, SENDER)
        reader_texts = _msgs_to(self.bot, reader.id)
        return sender_texts, reader_texts

    def _assert_notification_matches(self, sender_texts, w, reader, variants):
        expected = resolve_variant(w, reader.id)
        self.assertIn(expected, variants)
        self.assertTrue(
            any(expected in t for t in sender_texts),
            f"sender notification missing reader variant {expected!r}: {sender_texts!r}",
        )
        for other in variants:
            if other != expected:
                self.assertTrue(
                    all(other not in t for t in sender_texts),
                    f"sender notification leaked another variant {other!r}: {sender_texts!r}",
                )

    def _assert_reader_saw_only_its_variant(self, reader_texts, w, reader, variants):
        expected = resolve_variant(w, reader.id)
        self.assertTrue(
            any(expected in t for t in reader_texts),
            f"reader did not see its variant {expected!r}: {reader_texts!r}",
        )
        for other in variants:
            if other != expected:
                self.assertTrue(
                    all(other not in t for t in reader_texts),
                    f"reader received another variant {other!r}: {reader_texts!r}",
                )


class TestCompleteReadFlowEveryone(_FlowCase):

    def test_each_reader_notification_matches_its_variant(self):
        variants = ["نسخة جماعية أولى", "نسخة جماعية ثانية", "نسخة جماعية ثالثة"]
        wid = _make_variant(variants[0], variants, "everyone")
        w = _w(wid)
        for uid in (READER_A, READER_B):
            sender_texts, reader_texts = self._read(wid, _Reader(uid))
            self._assert_notification_matches(sender_texts, w, _Reader(uid), variants)
            self._assert_reader_saw_only_its_variant(reader_texts, w, _Reader(uid), variants)
            self.bot.send_message.reset_mock()


class TestCompleteReadFlowFirstThree(_FlowCase):

    def test_each_reader_notification_matches_its_variant(self):
        variants = ["ثلاثية: أول", "ثلاثية: ثاني", "ثلاثية: ثالث"]
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        w = _w(wid)
        for uid in (READER_A, READER_B, READER_C):
            sender_texts, reader_texts = self._read(wid, _Reader(uid))
            self._assert_notification_matches(sender_texts, w, _Reader(uid), variants)
            self._assert_reader_saw_only_its_variant(reader_texts, w, _Reader(uid), variants)
            self.bot.send_message.reset_mock()


class TestCompleteReadFlowFirstFive(_FlowCase):

    def test_each_reader_notification_matches_its_variant(self):
        variants = ["خماسية: واحد", "خماسية: اثنين", "خماسية: ثلاثة", "خماسية: أربعة", "خماسية: خمسة"]
        wid = _make_variant(variants[0], variants, "first_five", max_readers=5)
        w = _w(wid)
        for uid in (READER_A, READER_B, READER_C, READER_D, READER_E):
            sender_texts, reader_texts = self._read(wid, _Reader(uid))
            self._assert_notification_matches(sender_texts, w, _Reader(uid), variants)
            self._assert_reader_saw_only_its_variant(reader_texts, w, _Reader(uid), variants)
            self.bot.send_message.reset_mock()


class TestCompleteReadFlowFirstOne(_FlowCase):

    def test_notification_shows_reader_variant_not_first(self):
        variants = ["أول شخص: ألف", "أول شخص: باء", "أول شخص: جيم"]
        wid = _make_variant(variants[0], variants, "first_one", max_readers=1)
        w = _w(wid)
        uid = _find_reader_away_from_first(w, variants)
        self.assertIsNotNone(uid)
        self.assertNotEqual(resolve_variant(w, uid), variants[0])
        sender_texts, reader_texts = self._read(wid, _Reader(uid))
        self._assert_notification_matches(sender_texts, w, _Reader(uid), variants)
        self._assert_reader_saw_only_its_variant(reader_texts, w, _Reader(uid), variants)


class TestCompleteReadFlowCustom(_FlowCase):

    def test_reader_name_notification_unchanged_and_no_leak(self):
        variants = ["مخصصة: سر أ", "مخصصة: سر ب"]
        wid = _make_variant(variants[0], variants, "custom", target_users=[READER_A])
        w = _w(wid)
        sender_texts, reader_texts = self._read(wid, _Reader(READER_A))
        # reader still sees its own deterministic variant
        self._assert_reader_saw_only_its_variant(reader_texts, w, _Reader(READER_A), variants)
        # sender notifications never leak a variant that is not the reader's
        self._assert_notification_matches(sender_texts, w, _Reader(READER_A), variants)


if __name__ == "__main__":
    unittest.main(verbosity=2)
