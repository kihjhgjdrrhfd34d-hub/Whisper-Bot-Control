"""
tests/test_integration.py
Integration tests: cross-module flows that test the system end-to-end
without a real Telegram connection.
"""
import os
import sys
import unittest

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"  # valid enough for tests
os.environ["ADMIN_IDS"]     = "999"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _boot():
    import database as db
    from enterprise.db_enterprise import init_enterprise_db
    db.init_db()
    init_enterprise_db()


class TestUserJourneyXP(unittest.TestCase):
    """User joins → sends whispers → earns XP → levels up → gets achievements."""

    def setUp(self):
        _boot()

    def test_xp_from_first_whisper_achievement(self):
        import database as db
        from enterprise.db_enterprise import (
            check_and_grant_achievements, get_xp,
        )
        # Use a unique user ID never seen by other tests in this file
        db.upsert_user(30001, "journey_user", "Journey", None)
        # Send whisper → check achievements
        db.create_whisper(30001, "first ever", "everyone")
        granted = check_and_grant_achievements(30001)
        self.assertIn("first_whisper", granted)
        # Achievement grants XP
        xp_data = get_xp(30001)
        self.assertGreater(xp_data["xp"], 0)

    def test_whisper_10_achievement(self):
        import database as db
        from enterprise.db_enterprise import check_and_grant_achievements
        # Use a different unique user ID to avoid collision with test above
        db.upsert_user(30002, "journey_user2", "Journey2", None)
        for i in range(10):
            db.create_whisper(30002, f"whisper {i}", "everyone")
        granted = check_and_grant_achievements(30002)
        self.assertIn("whisper_10", granted)


class TestInviteFlow(unittest.TestCase):
    """Inviter generates code → invitee uses it → inviter gets XP."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30010, "inviter2", "Inviter2", None)
        db.upsert_user(30011, "invitee2", "Invitee2", None)

    def test_full_invite_flow(self):
        from enterprise.db_enterprise import (
            generate_referral_code, get_user_by_referral_code,
            register_invite, count_invites, get_xp,
        )
        code = generate_referral_code(30010)
        inviter_id = get_user_by_referral_code(code)
        self.assertEqual(inviter_id, 30010)
        register_invite(30010, 30011)
        self.assertEqual(count_invites(30010), 1)
        xp = get_xp(30010)
        self.assertGreaterEqual(xp["xp"], 20)  # 20 XP per invite


class TestReportFlow(unittest.TestCase):
    """User reports whisper → admin reviews it → status changes."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30020, "reporter2", "Reporter2", None)
        db.upsert_user(30021, "sender2", "Sender2", None)

    def test_report_and_resolve(self):
        import database as db
        from enterprise.db_enterprise import (
            create_report, get_reports, review_report, count_reports,
        )
        wid = db.create_whisper(30021, "offensive content", "everyone")
        rid = create_report(30020, wid, "محتوى مسيء")
        pending = count_reports("pending")
        self.assertGreaterEqual(pending, 1)
        review_report(rid, 999, "resolved")
        resolved_reports = get_reports(status="resolved")
        ids = [r["id"] for r in resolved_reports]
        self.assertIn(rid, ids)


class TestBanFlowWithLog(unittest.TestCase):
    """Admin bans user → ban logged → admin unbans → unban logged."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30030, "banusr", "BanUser", None)

    def test_ban_unban_audit_trail(self):
        import database as db
        from enterprise.db_enterprise import (
            ban_user_with_reason, unban_user_with_reason, get_ban_log,
        )
        ban_user_with_reason(30030, "test", banned_by=999)
        self.assertTrue(db.is_banned(30030))
        log = get_ban_log(30030)
        self.assertTrue(any(e["action"] == "ban" for e in log))

        unban_user_with_reason(30030, "forgiven", unbanned_by=999)
        self.assertFalse(db.is_banned(30030))
        log = get_ban_log(30030)
        self.assertTrue(any(e["action"] == "unban" for e in log))


class TestSelfDestructIntegration(unittest.TestCase):
    """Whisper marked self-destruct → read once → check_self_destruct returns True."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30040, "sduser", "SDUser", None)
        db.upsert_user(30041, "sdrdr", "SDReader", None)

    def test_self_destruct_after_read(self):
        import database as db
        from enterprise.db_enterprise import set_self_destruct, check_self_destruct
        wid = db.create_whisper(30040, "boom after read", "everyone")
        set_self_destruct(wid, after_reads=1)
        # Simulate read
        db.add_reader(wid, 30041)
        count = db.reader_count(wid)
        should_destroy = check_self_destruct(wid, count)
        self.assertTrue(should_destroy)
        # Actually delete it
        db.delete_whisper(wid)
        self.assertIsNone(db.get_whisper(wid))


class TestActivityLogIntegration(unittest.TestCase):
    """All user actions are logged to activity_log."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30050, "actlog", "ActLog", None)

    def test_multiple_actions_logged(self):
        from enterprise.db_enterprise import log_activity, get_activity_log
        import database as db
        wid = db.create_whisper(30050, "log this", "everyone")
        log_activity(30050, "whisper_sent", {"wid": wid})
        log_activity(30050, "whisper_read", {"wid": wid})
        log = get_activity_log(30050)
        actions = [e["action"] for e in log]
        self.assertIn("whisper_sent", actions)
        self.assertIn("whisper_read", actions)


class TestRegressionBackwardCompat(unittest.TestCase):
    """Verify all original database functions still work after enterprise migration."""

    def setUp(self):
        _boot()
        import database as db
        db.upsert_user(30060, "compat", "Compat", None)

    def test_all_core_db_functions_exist(self):
        import database as db
        funcs = [
            "get_conn", "init_db", "_run_migrations", "upsert_user",
            "is_new_user", "mark_user_started", "get_user", "is_banned",
            "ban_user", "unban_user", "get_all_users", "search_users",
            "create_whisper", "get_whisper", "update_whisper_content",
            "toggle_whisper_lock", "delete_whisper", "clear_whisper_readers",
            "add_reader", "get_readers", "reader_count", "add_curious",
            "get_curious_ones", "can_read_whisper", "get_setting", "set_setting",
            "get_mandatory_channels", "add_mandatory_channel", "remove_mandatory_channel",
            "get_stats", "get_user_stats", "delete_expired_whispers",
        ]
        for fn in funcs:
            self.assertTrue(hasattr(db, fn), f"Missing function: database.{fn}")

    def test_original_whisper_flow_unchanged(self):
        import database as db
        wid = db.create_whisper(30060, "compat whisper", "everyone")
        w = db.get_whisper(wid)
        self.assertEqual(w["content"], "compat whisper")
        can, reason = db.can_read_whisper(wid, 30060)
        self.assertTrue(can)  # sender can always read
        db.add_reader(wid, 99999)
        self.assertEqual(db.reader_count(wid), 1)
        db.clear_whisper_readers(wid)
        self.assertEqual(db.reader_count(wid), 0)
        db.delete_whisper(wid)
        self.assertIsNone(db.get_whisper(wid))

    def test_settings_api_unchanged(self):
        import database as db
        db.set_setting("test_compat", "value1")
        self.assertEqual(db.get_setting("test_compat"), "value1")
        # Re-set
        db.set_setting("test_compat", "value2")
        self.assertEqual(db.get_setting("test_compat"), "value2")

    def test_enterprise_columns_dont_break_core_reads(self):
        """The new columns (is_anonymous, is_self_destruct, etc.) should not
        break existing get_whisper() calls — they just return None/0 by default."""
        import database as db
        wid = db.create_whisper(30060, "column test", "custom",
                                  target_users=[30060])
        w = db.get_whisper(wid)
        self.assertIsNotNone(w)
        self.assertEqual(w["content"], "column test")
        # Enterprise columns default to 0
        self.assertEqual(w["is_anonymous"], 0)
        self.assertEqual(w["is_self_destruct"], 0)
        self.assertIsNone(w["parent_whisper_id"])
        self.assertEqual(w["is_archived"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
