"""
tests/test_variant_read_order.py — Variant assignment by actual read order.

For the limited types (first_one / first_three / first_five) the variant a
reader sees is chosen by their *acceptance order* in whisper_readers
(get_reader_ordinal), not by a user_id hash:

  * first_one   → the single reader gets variants[0]
  * first_three → readers get variants[0], variants[1], variants[2] in order
  * first_five  → readers get variants[0..4] in order

Also covers:
  * no user_id-hash collisions (different users never share a variant for a
    limited type that has enough variants)
  * sender notifications contain exactly the reader's own variant
  * everyone / custom keep their historical deterministic (crc32) behavior
  * concurrent reads always yield distinct ordinals (race safety)
  * fewer variants than readers: no crash and no random variant pick
  * the variants list is never leaked into reader/sender messages
"""
import json
import os
import sys
import threading
import tempfile
import atexit
import unittest
import zlib
from unittest.mock import MagicMock

_tmpdb = tempfile.mktemp(suffix="_variant_read_order.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from services.whisper_service import (
    resolve_variant,
    record_read_and_check,
    build_read_receipt_message,
    build_first_three_read_notification,
    build_first_one_notification,
)

SENDER = 80001
R1 = 80002
R2 = 80003
R3 = 80004
R4 = 80005
R5 = 80006


class _Reader:
    def __init__(self, uid):
        self.id = uid
        self.username = f"user{uid}"
        self.first_name = f"User{uid}"
        self.last_name = None


def _boot():
    db.init_db()
    for uid in (SENDER, R1, R2, R3, R4, R5):
        db.upsert_user(uid, f"user{uid}", f"User{uid}", None)


def _make_variant(first, variants, wtype, max_readers=0, target_users=None):
    return db.create_whisper(
        SENDER, first, wtype,
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


def _read(wid, uid):
    """Simulate the real flow: record the read, then resolve the variant."""
    record_read_and_check(wid, uid)
    return resolve_variant(_w(wid), uid)


def _variants_of(w):
    raw = w.get("conditions_data")
    if isinstance(raw, str):
        raw = json.loads(raw)
    return [v for v in (raw or {}).get("variants") or [] if isinstance(v, str) and v.strip()]


# ── get_reader_ordinal ─────────────────────────────────────────────────────

class TestGetReaderOrdinal(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_ordinals_reflect_acceptance_order(self):
        wid = _make_variant("v0", ["v0", "v1", "v2"], "first_three", max_readers=3)
        self.assertIsNone(db.get_reader_ordinal(wid, R1))
        for uid, expected in [(R1, 0), (R2, 1), (R3, 2)]:
            self.assertTrue(db.record_whisper_read(wid, uid))
            self.assertEqual(db.get_reader_ordinal(wid, uid), expected)
        self.assertIsNone(db.get_reader_ordinal(wid, R5))

    def test_ordinal_ignores_read_at_ties(self):
        wid = _make_variant("v0", ["v0", "v1"], "first_three", max_readers=3)
        self.assertTrue(db.record_whisper_read(wid, R1))
        self.assertTrue(db.record_whisper_read(wid, R2))
        # Force both rows into the same read_at second — id must still
        # disambiguate the order.
        with db.get_conn() as conn:
            conn.execute(
                "UPDATE whisper_readers SET read_at='2024-01-01 00:00:00' WHERE whisper_id=?",
                (wid,),
            )
        self.assertEqual(db.get_reader_ordinal(wid, R1), 0)
        self.assertEqual(db.get_reader_ordinal(wid, R2), 1)


# ── first_one ──────────────────────────────────────────────────────────────

class TestFirstOne(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["نسخة أول شخص أ", "نسخة أول شخص ب", "نسخة أول شخص ج"]
        self.wid = _make_variant(self.variants[0], self.variants, "first_one", max_readers=1)

    def test_first_reader_gets_first_variant(self):
        self.assertEqual(_read(self.wid, R1), self.variants[0])

    def test_only_one_slot(self):
        self.assertTrue(db.record_whisper_read(self.wid, R1))
        self.assertFalse(db.record_whisper_read(self.wid, R2))


# ── first_three ────────────────────────────────────────────────────────────

class TestFirstThree(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["ثلاثية: أول", "ثلاثية: ثاني", "ثلاثية: ثالث"]
        self.wid = _make_variant(self.variants[0], self.variants, "first_three", max_readers=3)

    def test_three_readers_get_ordered_variants(self):
        for uid, expected in [
            (R1, self.variants[0]),
            (R2, self.variants[1]),
            (R3, self.variants[2]),
        ]:
            self.assertEqual(_read(self.wid, uid), expected)

    def test_no_duplicate_variants_for_different_user_ids(self):
        seen = {_read(self.wid, uid) for uid in (R1, R2, R3)}
        self.assertEqual(seen, set(self.variants))

    def test_locked_after_three(self):
        for uid in (R1, R2, R3):
            db.record_whisper_read(self.wid, uid)
        self.assertTrue(_w(self.wid)["is_locked"])
        self.assertFalse(db.record_whisper_read(self.wid, R4))


# ── first_five ─────────────────────────────────────────────────────────────

class TestFirstFive(unittest.TestCase):

    def setUp(self):
        _boot()
        self.variants = ["خماسية: واحد", "خماسية: اثنين", "خماسية: ثلاثة", "خماسية: أربعة", "خماسية: خمسة"]
        self.wid = _make_variant(self.variants[0], self.variants, "first_five", max_readers=5)

    def test_five_readers_get_ordered_variants(self):
        for uid, expected in zip((R1, R2, R3, R4, R5), self.variants):
            self.assertEqual(_read(self.wid, uid), expected)

    def test_all_five_variants_used(self):
        seen = {_read(self.wid, uid) for uid in (R1, R2, R3, R4, R5)}
        self.assertEqual(seen, set(self.variants))


# ── sender notification matches reader variant ─────────────────────────────

class TestSenderNotificationMatches(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_first_three_notification_matches_second_reader(self):
        variants = ["نسخة ١", "نسخة ٢", "نسخة ٣"]
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        db.record_whisper_read(wid, R1)
        db.record_whisper_read(wid, R2)  # second reader → variants[1]
        w = _w(wid)
        notify = build_first_three_read_notification(_Reader(R2), w)
        self.assertIn(variants[1], notify)
        for other in variants:
            if other != variants[1]:
                self.assertNotIn(other, notify)

    def test_first_one_notification_matches_reader(self):
        variants = ["أول: ألف", "أول: باء", "أول: جيم"]
        wid = _make_variant(variants[0], variants, "first_one", max_readers=1)
        db.record_whisper_read(wid, R1)
        w = _w(wid)
        notify = build_first_one_notification(_Reader(R1), w)
        self.assertIn(variants[0], notify)
        for other in variants[1:]:
            self.assertNotIn(other, notify)

    def test_read_receipt_matches_reader(self):
        variants = ["أ", "ب", "ج"]
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        db.record_whisper_read(wid, R1)
        db.record_whisper_read(wid, R2)
        db.record_whisper_read(wid, R3)  # third reader → variants[2]
        w = _w(wid)
        receipt = build_read_receipt_message(_Reader(R3), w)
        self.assertIn(variants[2], receipt)
        for other in variants[:2]:
            self.assertNotIn(other, receipt)


# ── everyone / custom unchanged ────────────────────────────────────────────

class TestEveryoneCustomUnchanged(unittest.TestCase):

    def setUp(self):
        _boot()

    def _expected_hash(self, wid, uid, variants):
        return variants[zlib.crc32(f"{wid}:{uid}".encode("utf-8")) % len(variants)]

    def test_everyone_keeps_deterministic_crc32(self):
        variants = ["جميع: أ", "جميع: ب", "جميع: ج"]
        wid = _make_variant(variants[0], variants, "everyone")
        db.record_whisper_read(wid, R1)
        db.record_whisper_read(wid, R2)
        w = _w(wid)
        self.assertEqual(resolve_variant(w, R1), self._expected_hash(wid, R1, variants))
        self.assertEqual(resolve_variant(w, R2), self._expected_hash(wid, R2, variants))

    def test_custom_keeps_deterministic_crc32(self):
        variants = ["مخصص: أ", "مخصص: ب", "مخصص: ج"]
        wid = _make_variant(variants[0], variants, "custom", target_users=[R1])
        db.record_whisper_read(wid, R1)
        w = _w(wid)
        self.assertEqual(resolve_variant(w, R1), self._expected_hash(wid, R1, variants))


# ── fewer variants than readers ────────────────────────────────────────────

class TestFewerVariantsThanReaders(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_no_crash_and_no_random_pick(self):
        variants = ["أ", "ب"]  # only 2 variants but 3 reader slots
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        db.record_whisper_read(wid, R1)
        db.record_whisper_read(wid, R2)
        db.record_whisper_read(wid, R3)
        w = _w(wid)
        self.assertEqual(resolve_variant(w, R1), variants[0])
        self.assertEqual(resolve_variant(w, R2), variants[1])
        # The 3rd reader has no matching variant: fall back to stored content
        # (the first variant) instead of picking a random/repeated one.
        self.assertEqual(resolve_variant(w, R3), w["content"])


# ── race: concurrent reads ─────────────────────────────────────────────────

class TestConcurrentReadsRace(unittest.TestCase):

    def setUp(self):
        _boot()

    def test_concurrent_reads_get_distinct_ordinals(self):
        variants = ["ر", "و", "ن"]
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        results = []
        barrier = threading.Barrier(3)

        def read(uid):
            barrier.wait()
            results.append((uid, db.record_whisper_read(wid, uid)))

        threads = [threading.Thread(target=read, args=(u,)) for u in (R1, R2, R3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertTrue(all(ok for _, ok in results))
        self.assertEqual(db.reader_count(wid), 3)
        ordinals = sorted(db.get_reader_ordinal(wid, uid) for uid in (R1, R2, R3))
        self.assertEqual(ordinals, [0, 1, 2])
        assigned = sorted(resolve_variant(_w(wid), uid) for uid in (R1, R2, R3))
        self.assertEqual(assigned, sorted(variants))


# ── no leak of the variants list ───────────────────────────────────────────

class TestNoVariantListLeak(unittest.TestCase):
    """Integration through _complete_read_flow: reader/sender messages never
    contain any variant other than the reader's own."""

    def setUp(self):
        _boot()
        self.bot = MagicMock()
        self.bot.send_message = MagicMock()

    def _msgs_to(self, chat_id):
        out = []
        for args, kwargs in self.bot.send_message.call_args_list:
            target = args[0] if args else kwargs.get("chat_id")
            text = args[1] if len(args) > 1 else kwargs.get("text")
            if target == chat_id:
                out.append(text)
        return out

    def test_first_three_never_leaks_another_variant(self):
        from handlers.whisper import _complete_read_flow
        variants = ["سر ألف", "سر باء", "سر جيم"]
        wid = _make_variant(variants[0], variants, "first_three", max_readers=3)
        for uid in (R1, R2, R3):
            self.bot.send_message.reset_mock()
            _complete_read_flow(
                self.bot, call=None, user=_Reader(uid),
                whisper_id=wid, w=_w(wid), is_destructive=False,
            )
            expected = resolve_variant(_w(wid), uid)
            sender_texts = self._msgs_to(SENDER)
            reader_texts = self._msgs_to(uid)
            for t in sender_texts + reader_texts:
                for other in variants:
                    if other != expected:
                        self.assertNotIn(other, t)
            self.assertTrue(any(expected in t for t in reader_texts),
                            "reader did not see its own variant")
            self.assertTrue(any(expected in t for t in sender_texts),
                            "sender notification missing reader variant")


if __name__ == "__main__":
    unittest.main(verbosity=2)
