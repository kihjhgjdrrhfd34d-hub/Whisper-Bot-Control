"""
tests/test_variant_whispers.py — Stage 1: deterministic variant engine.

Covers services.whisper_service.resolve_variant:
  * determinism (same whisper_id + user_id ⇒ same variant, stable across calls)
  * different readers can see different variants
  * fallback to original content when no valid variants exist
  * ignores non-text / empty / missing variants
  * coexistence with conditions (password / question / multiple_choice)
  * coexistence with first_five / custom whisper types
  * sender/admin-facing data (content) stays unchanged
"""
import json
import os
import sys
import tempfile
import atexit
import unittest

_tmpdb = tempfile.mktemp(suffix="_variant_whispers_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"] = "0:test_placeholder"
os.environ["ADMIN_IDS"] = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database import create_whisper, get_whisper, upsert_user
from conditions import registry
from services.whisper_service import resolve_variant


SENDER = 60100
READER_A = 60101
READER_B = 60102


def _whisper_dict(**overrides):
    """Build a plain dict shaped like a row returned by get_whisper()."""
    base = {
        "whisper_id": "wid_001",
        "sender_id": SENDER,
        "content": "النص الأصلي",
        "whisper_type": "everyone",
        "conditions_data": None,
    }
    base.update(overrides)
    return base


def _variants(entries):
    return _whisper_dict(conditions_data={"variants": entries})


class TestResolveVariantDeterminism(unittest.TestCase):
    """Determinism guarantees of the variant engine."""

    def test_same_id_and_user_always_same_variant(self):
        w = _variants(["نسخة أولى", "نسخة ثانية", "نسخة ثالثة"])
        first = resolve_variant(w, READER_A)
        for _ in range(50):
            self.assertEqual(resolve_variant(w, READER_A), first)

    def test_stable_across_fresh_dict_instances(self):
        w1 = _variants(["أ", "ب", "ج"])
        w2 = _variants(["أ", "ب", "ج"])
        self.assertEqual(resolve_variant(w1, READER_A), resolve_variant(w2, READER_A))

    def test_result_is_a_single_variant_from_the_list(self):
        variants = ["نسخة أولى", "نسخة ثانية", "نسخة ثالثة"]
        w = _variants(variants)
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, variants)

    def test_different_readers_can_get_different_variants(self):
        w = _variants(["أ", "ب", "ج"])
        seen = {resolve_variant(w, uid) for uid in range(70001, 70051)}
        self.assertGreaterEqual(len(seen), 2)


class TestResolveVariantFallback(unittest.TestCase):
    """Fallback to original content when variants are absent/invalid."""

    def test_no_conditions_data_returns_content(self):
        w = _whisper_dict()
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_conditions_data_without_variants_key(self):
        w = _whisper_dict(conditions_data={"password": {"hash": "x"}})
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_empty_variants_list(self):
        w = _variants([])
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_variants_contains_only_empty_strings(self):
        w = _variants(["", "   ", None])
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_variants_contains_only_non_text_values(self):
        w = _variants([123, None, {"text": "x"}, ["a"], True])
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_mixed_invalid_entries_are_filtered_and_valid_used(self):
        variants = ["  ", None, "نسخة صالحة", 42]
        w = _variants(variants)
        picked = resolve_variant(w, READER_A)
        self.assertEqual(picked, "نسخة صالحة")

    def test_missing_whisper_id_returns_content(self):
        w = {
            "content": "بدون معرف",
            "conditions_data": {"variants": ["أ", "ب"]},
        }
        self.assertEqual(resolve_variant(w, READER_A), "بدون معرف")

    def test_id_key_used_when_whisper_id_missing(self):
        w = {
            "id": "wid_other",
            "content": "محتوى",
            "conditions_data": {"variants": ["أ", "ب", "ج"]},
        }
        first = resolve_variant(w, READER_A)
        for _ in range(20):
            self.assertEqual(resolve_variant(w, READER_A), first)
        self.assertIn(first, ["أ", "ب", "ج"])

    def test_malformed_json_conditions_data_returns_content(self):
        w = _whisper_dict(conditions_data="{not valid json")
        self.assertEqual(resolve_variant(w, READER_A), "النص الأصلي")

    def test_json_string_conditions_data(self):
        w = _whisper_dict(
            conditions_data=json.dumps({"variants": ["أ", "ب", "ج"]})
        )
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])

    def test_empty_content_stays_empty_on_fallback(self):
        w = _whisper_dict(content="")
        self.assertEqual(resolve_variant(w, READER_A), "")

    def test_none_whisper_does_not_raise(self):
        self.assertEqual(resolve_variant(None, READER_A), "")


class TestResolveVariantConditions(unittest.TestCase):
    """Variants must coexist with existing conditions without interference."""

    def setUp(self):
        db.init_db()
        from database.whisper_conditions import init_whisper_conditions_db
        init_whisper_conditions_db()
        upsert_user(SENDER, "sender", "Sender", None)
        upsert_user(READER_A, "reader_a", "ReaderA", None)

    def test_variants_plus_password(self):
        cond = {
            "password": {"hash": "secret"},
            "variants": ["أ", "ب", "ج"],
        }
        wid = create_whisper(SENDER, "الأصل", "everyone", conditions_data=cond)
        w = dict(get_whisper(wid))
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])
        results = registry.check_all(w, READER_A)
        names = [r.condition_type for r in results]
        self.assertNotIn("variants", names)
        self.assertEqual(names, ["password"])
        self.assertTrue(results[0].requires_interaction)
        self.assertFalse(results[0].passed)

    def test_variants_plus_question(self):
        cond = {
            "question": {"question": "ما هو الجواب؟", "answer": "42"},
            "variants": ["أ", "ب", "ج"],
        }
        wid = create_whisper(SENDER, "الأصل", "everyone", conditions_data=cond)
        w = dict(get_whisper(wid))
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])
        results = registry.check_all(w, READER_A)
        names = [r.condition_type for r in results]
        self.assertNotIn("variants", names)
        self.assertEqual(names, ["question"])

    def test_variants_plus_multiple_choice(self):
        cond = {
            "multiple_choice": {
                "question": "اختر", "choices": ["أ", "ب"], "correct_index": 1,
            },
            "variants": ["أ", "ب", "ج"],
        }
        wid = create_whisper(SENDER, "الأصل", "everyone", conditions_data=cond)
        w = dict(get_whisper(wid))
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])
        results = registry.check_all(w, READER_A)
        names = [r.condition_type for r in results]
        self.assertNotIn("variants", names)
        self.assertEqual(names, ["multiple_choice"])
        self.assertTrue(results[0].requires_interaction)

    def test_variants_plus_password_passed_still_passes(self):
        from database.whisper_conditions import record_condition_attempt
        cond = {
            "password": {"hash": "secret"},
            "variants": ["أ", "ب", "ج"],
        }
        wid = create_whisper(SENDER, "الأصل", "everyone", conditions_data=cond)
        record_condition_attempt(wid, READER_A, "password", passed=True)
        w = dict(get_whisper(wid))
        results = registry.check_all(w, READER_A)
        self.assertTrue(results[0].passed)
        self.assertIn(resolve_variant(w, READER_A), ["أ", "ب", "ج"])

    def test_variants_with_first_five_type(self):
        cond = {"variants": ["أ", "ب", "ج"]}
        wid = create_whisper(
            SENDER, "الأصل", "first_five", max_readers=5, conditions_data=cond,
        )
        w = dict(get_whisper(wid))
        self.assertEqual(w["whisper_type"], "first_five")
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])
        self.assertEqual(db.effective_max_readers(w), 5)

    def test_variants_with_custom_type(self):
        cond = {"variants": ["أ", "ب", "ج"]}
        wid = create_whisper(
            SENDER, "الأصل", "custom", target_users=[READER_A],
            conditions_data=cond,
        )
        w = dict(get_whisper(wid))
        self.assertEqual(w["whisper_type"], "custom")
        picked = resolve_variant(w, READER_A)
        self.assertIn(picked, ["أ", "ب", "ج"])

    def test_sender_notification_shows_reader_variant(self):
        # The stored content is NOT touched; only the notification text changes.
        cond = {"variants": ["أ", "ب", "ج"]}
        wid = create_whisper(SENDER, "النص الأصلي", "everyone", conditions_data=cond)
        w = dict(get_whisper(wid))
        self.assertEqual(w["content"], "النص الأصلي")

        from services.whisper_service import (
            build_read_receipt_message,
            build_first_three_read_notification,
            resolve_variant,
        )

        class _User:
            id = READER_A
            username = "reader_a"
            first_name = "ReaderA"

        expected = resolve_variant(w, READER_A)
        self.assertIn(expected, ["أ", "ب", "ج"])

        receipt = build_read_receipt_message(_User(), w)
        self.assertIn(expected, receipt)
        self.assertNotIn("النص الأصلي", receipt)

        notify = build_first_three_read_notification(_User(), w)
        self.assertIn(expected, notify)


if __name__ == "__main__":
    unittest.main()
