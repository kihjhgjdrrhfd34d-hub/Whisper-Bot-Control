import json
import os
import sys
import unittest

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from database.whisper_conditions import (
    init_whisper_conditions_db,
    add_whisper_condition,
    get_whisper_conditions,
    delete_whisper_conditions,
    record_condition_attempt,
    get_condition_attempts,
    delete_condition_attempts,
)
from conditions import (
    BaseCondition,
    ConditionResult,
    ConditionRegistry,
    discover_conditions,
    registry,
)
from conditions.password import PasswordCondition
from conditions.question import QuestionCondition
from conditions.time_window import TimeWindowCondition
from conditions.channel_member import ChannelMemberCondition


class TestCreateWhisperWithoutConditions(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_whisper_conditions_db()
        db.upsert_user(9001, "sender", "Sender", None)

    def test_create_without_conditions_data(self):
        wid = db.create_whisper(9001, "no conditions", "everyone")
        self.assertIsNotNone(wid)
        w = dict(db.get_whisper(wid))
        self.assertIsNotNone(w)
        self.assertEqual(w["content"], "no conditions")
        self.assertIsNone(w.get("conditions_data"))

    def test_create_with_default_args_matches_old_behavior(self):
        old_wid = db.create_whisper(9001, "old style", "everyone")
        w = db.get_whisper(old_wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["whisper_type"], "everyone")
        self.assertEqual(w["sender_id"], 9001)


class TestCreateWhisperWithConditionsData(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_whisper_conditions_db()
        db.upsert_user(9002, "sender2", "Sender2", None)

    def test_create_with_empty_conditions_data(self):
        wid = db.create_whisper(9002, "empty conditions", "everyone", conditions_data={})
        self.assertIsNotNone(wid)
        w = dict(db.get_whisper(wid))
        self.assertEqual(json.loads(w["conditions_data"]), {})

    def test_create_with_conditions_data(self):
        cond = {"password": {"hash": "abc123"}, "time_window": {"window": "09:00-17:00"}}
        wid = db.create_whisper(9002, "with conditions", "everyone", conditions_data=cond)
        self.assertIsNotNone(wid)
        w = dict(db.get_whisper(wid))
        loaded = json.loads(w["conditions_data"]) if w["conditions_data"] else {}
        self.assertEqual(loaded, cond)

    def test_create_with_conditions_data_json_string(self):
        cond_str = json.dumps({"question": {"q": "answer?"}})
        wid = db.create_whisper(9002, "json string", "everyone", conditions_data=cond_str)
        self.assertIsNotNone(wid)
        w = dict(db.get_whisper(wid))
        loaded = json.loads(w["conditions_data"]) if w["conditions_data"] else {}
        self.assertEqual(loaded, {"question": {"q": "answer?"}})


class TestReadConditionsData(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_whisper_conditions_db()
        db.upsert_user(9003, "sender3", "Sender3", None)

    def test_read_conditions_data_back(self):
        cond = {"password": {"hash": "secret"}}
        wid = db.create_whisper(9003, "read test", "everyone", conditions_data=cond)
        w = dict(db.get_whisper(wid))
        self.assertIsNotNone(w)
        loaded = json.loads(w["conditions_data"])
        self.assertEqual(loaded, cond)

    def test_whisper_without_conditions_returns_none(self):
        wid = db.create_whisper(9003, "no cond", "everyone")
        w = dict(db.get_whisper(wid))
        self.assertIsNone(w.get("conditions_data"))
        self.assertIsNone(w["conditions_data"])

    def test_get_whisper_returns_all_expected_fields(self):
        wid = db.create_whisper(
            9003, "full fields", "custom",
            target_users=[9003],
            max_readers=5,
            conditions_data={"channel_member": {"channel_id": "@test"}},
        )
        w = dict(db.get_whisper(wid))
        self.assertEqual(w["content"], "full fields")
        self.assertEqual(w["whisper_type"], "custom")
        self.assertIsNotNone(w["conditions_data"])


class TestWhisperConditionsCRUD(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_whisper_conditions_db()
        db.upsert_user(9004, "sender4", "Sender4", None)
        self.wid = db.create_whisper(9004, "crud test", "everyone")

    def test_add_whisper_condition(self):
        cid = add_whisper_condition(self.wid, "password", {"hash": "abc"})
        self.assertIsInstance(cid, int)
        self.assertGreater(cid, 0)

    def test_get_whisper_conditions(self):
        add_whisper_condition(self.wid, "password", {"hash": "abc"})
        conditions = get_whisper_conditions(self.wid)
        self.assertEqual(len(conditions), 1)
        self.assertEqual(conditions[0]["condition_name"], "password")
        self.assertEqual(conditions[0]["config"], {"hash": "abc"})

    def test_get_whisper_conditions_empty(self):
        conditions = get_whisper_conditions(self.wid)
        self.assertEqual(conditions, [])

    def test_multiple_conditions(self):
        add_whisper_condition(self.wid, "password", {"hash": "abc"})
        add_whisper_condition(self.wid, "time_window", {"window": "09-17"})
        conditions = get_whisper_conditions(self.wid)
        self.assertEqual(len(conditions), 2)
        names = [c["condition_name"] for c in conditions]
        self.assertIn("password", names)
        self.assertIn("time_window", names)

    def test_delete_whisper_conditions(self):
        add_whisper_condition(self.wid, "password", {})
        delete_whisper_conditions(self.wid)
        self.assertEqual(get_whisper_conditions(self.wid), [])


class TestConditionAttemptsCRUD(unittest.TestCase):
    def setUp(self):
        db.init_db()
        init_whisper_conditions_db()
        db.upsert_user(9005, "sender5", "Sender5", None)
        db.upsert_user(9006, "reader5", "Reader5", None)
        self.wid = db.create_whisper(9005, "attempt test", "everyone")

    def test_record_attempt_passed(self):
        aid = record_condition_attempt(self.wid, 9006, "password", passed=True)
        self.assertIsInstance(aid, int)
        self.assertGreater(aid, 0)

    def test_record_attempt_failed(self):
        aid = record_condition_attempt(self.wid, 9006, "password", passed=False)
        self.assertIsInstance(aid, int)

    def test_get_attempts_by_whisper(self):
        record_condition_attempt(self.wid, 9006, "password", passed=True)
        attempts = get_condition_attempts(self.wid)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["passed"])

    def test_get_attempts_by_whisper_and_user(self):
        record_condition_attempt(self.wid, 9006, "password", passed=False)
        record_condition_attempt(self.wid, 9006, "password", passed=True)
        attempts = get_condition_attempts(self.wid, user_id=9006)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(attempts[0]["passed"])

    def test_get_attempts_other_user_not_included(self):
        db.upsert_user(9007, "other", "Other", None)
        record_condition_attempt(self.wid, 9006, "password", passed=True)
        attempts = get_condition_attempts(self.wid, user_id=9007)
        self.assertEqual(attempts, [])

    def test_delete_condition_attempts(self):
        record_condition_attempt(self.wid, 9006, "password", passed=True)
        delete_condition_attempts(self.wid)
        self.assertEqual(get_condition_attempts(self.wid), [])


class TestConditionBaseClasses(unittest.TestCase):
    def test_condition_result_dataclass(self):
        r = ConditionResult(passed=True, reason="ok")
        self.assertTrue(r.passed)
        self.assertEqual(r.reason, "ok")
        self.assertIsNone(r.data)

    def test_condition_result_with_data(self):
        r = ConditionResult(passed=False, reason="wrong", data={"attempts": 3})
        self.assertFalse(r.passed)
        self.assertEqual(r.data["attempts"], 3)

    def test_base_condition_is_abstract(self):
        with self.assertRaises(TypeError):
            BaseCondition()

    def test_password_condition_has_name(self):
        c = PasswordCondition()
        self.assertEqual(c.name, "password")
        self.assertTrue(c.description)

    def test_question_condition_has_name(self):
        c = QuestionCondition()
        self.assertEqual(c.name, "question")

    def test_time_window_condition_has_name(self):
        c = TimeWindowCondition()
        self.assertEqual(c.name, "time_window")

    def test_channel_member_condition_has_name(self):
        c = ChannelMemberCondition()
        self.assertEqual(c.name, "channel_member")


class TestConditionRegistry(unittest.TestCase):
    def setUp(self):
        self.r = ConditionRegistry()

    def test_register_and_get(self):
        c = PasswordCondition()
        self.r.register(c)
        self.assertIs(self.r.get("password"), c)

    def test_get_unknown(self):
        self.assertIsNone(self.r.get("nonexistent"))

    def test_all_returns_copy(self):
        c = PasswordCondition()
        self.r.register(c)
        all_conds = self.r.all()
        self.assertEqual(len(all_conds), 1)
        all_conds.clear()
        self.assertIsNotNone(self.r.get("password"))

    def test_unregister(self):
        c = PasswordCondition()
        self.r.register(c)
        self.r.unregister("password")
        self.assertIsNone(self.r.get("password"))

    def test_register_overwrites(self):
        c1 = PasswordCondition()
        c2 = PasswordCondition()
        self.r.register(c1)
        self.r.register(c2)
        self.assertIs(self.r.get("password"), c2)

    def test_global_registry_exists(self):
        self.assertIsNotNone(registry)


class TestConditionDiscovery(unittest.TestCase):
    def test_discover_finds_all_conditions(self):
        temp_registry = ConditionRegistry()
        original_conditions = dict(registry._conditions)
        registry._conditions.clear()
        try:
            discover_conditions()
            names = set(registry.all().keys())
            expected = {"password", "question", "time_window", "channel_member"}
            for name in expected:
                self.assertIn(name, names, f"Condition {name} not discovered")
        finally:
            registry._conditions.clear()
            registry._conditions.update(original_conditions)


if __name__ == "__main__":
    unittest.main(verbosity=2)
