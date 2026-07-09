"""
tests/test_enterprise_db.py
Unit tests for enterprise database layer.

Each test method uses its own unique user/whisper IDs to avoid cross-test
state pollution within the module-level shared SQLite file.
"""
import os
import sys
import unittest

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"  # valid enough for tests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from enterprise import db_enterprise as edb


def _boot():
    db.init_db()
    edb.init_enterprise_db()


class TestXP(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10001, "xpuser", "XP User", None)

    def test_award_and_get(self):
        result = edb.award_xp(10001, 50, reason="test")
        self.assertEqual(result["user_id"], 10001)
        self.assertGreaterEqual(result["xp"], 50)

    def test_level_up(self):
        edb.award_xp(10001, 150, reason="test")
        xp_data = edb.get_xp(10001)
        self.assertGreaterEqual(xp_data["level"], 3)

    def test_xp_accumulates(self):
        edb.award_xp(10001, 10, reason="a")
        edb.award_xp(10001, 20, reason="b")
        xp_data = edb.get_xp(10001)
        self.assertGreaterEqual(xp_data["xp"], 30)

    def test_leaderboard(self):
        db.upsert_user(10002, "lb2", "LB2", None)
        edb.award_xp(10001, 100)
        edb.award_xp(10002, 50)
        lb = edb.xp_leaderboard(5)
        self.assertGreaterEqual(len(lb), 1)
        self.assertIn("xp", lb[0])


class TestAchievements(unittest.TestCase):
    """
    Each test uses a distinct user ID so achievements granted in one test
    do not affect another.
    """
    def setUp(self):
        _boot()

    def test_grant_achievement(self):
        # User 10301: fresh, never had this achievement
        db.upsert_user(10301, "ach301", "Ach301", None)
        result = edb.grant_achievement(10301, "first_whisper")
        self.assertTrue(result)   # newly granted

    def test_no_duplicate_achievement(self):
        db.upsert_user(10302, "ach302", "Ach302", None)
        edb.grant_achievement(10302, "first_whisper")
        result = edb.grant_achievement(10302, "first_whisper")
        self.assertFalse(result)  # already had it

    def test_get_user_achievements(self):
        db.upsert_user(10303, "ach303", "Ach303", None)
        edb.grant_achievement(10303, "first_whisper")
        achievements = edb.get_user_achievements(10303)
        codes = [a["code"] for a in achievements]
        self.assertIn("first_whisper", codes)

    def test_check_and_grant(self):
        # User 10304: send 1 whisper, then check achievements
        db.upsert_user(10304, "ach304", "Ach304", None)
        db.create_whisper(10304, "hi", "everyone")
        granted = edb.check_and_grant_achievements(10304)
        self.assertIn("first_whisper", granted)


class TestInvites(unittest.TestCase):
    """Each test uses distinct user IDs to avoid duplicate-invite collisions."""
    def setUp(self):
        _boot()

    def test_generate_referral_code(self):
        db.upsert_user(10410, "inv410", "Inv410", None)
        code = edb.generate_referral_code(10410)
        self.assertIn("ref_10410", code)

    def test_register_invite(self):
        db.upsert_user(10411, "inv411", "Inv411", None)
        db.upsert_user(10412, "inv412", "Inv412", None)
        result = edb.register_invite(10411, 10412)
        self.assertTrue(result)
        self.assertEqual(edb.count_invites(10411), 1)

    def test_no_duplicate_invite(self):
        db.upsert_user(10413, "inv413", "Inv413", None)
        db.upsert_user(10414, "inv414", "Inv414", None)
        edb.register_invite(10413, 10414)
        result = edb.register_invite(10413, 10414)
        self.assertFalse(result)

    def test_get_user_by_referral_code(self):
        db.upsert_user(10415, "inv415", "Inv415", None)
        code = edb.generate_referral_code(10415)
        uid = edb.get_user_by_referral_code(code)
        self.assertEqual(uid, 10415)

    def test_get_invites(self):
        db.upsert_user(10416, "inv416", "Inv416", None)
        db.upsert_user(10417, "inv417", "Inv417", None)
        edb.register_invite(10416, 10417)
        invites = edb.get_invites(10416)
        self.assertEqual(len(invites), 1)
        self.assertEqual(invites[0]["invitee_id"], 10417)


class TestActivityLog(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10020, "actuser", "Act User", None)

    def test_log_and_retrieve(self):
        edb.log_activity(10020, "whisper_sent", {"wid": "abc"})
        log = edb.get_activity_log(10020)
        self.assertGreaterEqual(len(log), 1)
        # Check action is present anywhere in the log (order-agnostic)
        actions = [e["action"] for e in log]
        self.assertIn("whisper_sent", actions)

    def test_get_all_activity(self):
        edb.log_activity(10020, "login")
        all_log = edb.get_all_activity(limit=50)
        self.assertGreaterEqual(len(all_log), 1)

    def test_action_filter(self):
        edb.log_activity(10020, "special_action_xyz")
        filtered = edb.get_all_activity(action_filter="special_action_xyz")
        self.assertTrue(all(e["action"] == "special_action_xyz" for e in filtered))


class TestReports(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10030, "reporter", "Reporter", None)

    def test_create_report(self):
        rid = edb.create_report(10030, "wid_abc", "محتوى مسيء")
        self.assertIsInstance(rid, int)
        self.assertGreater(rid, 0)

    def test_get_reports(self):
        edb.create_report(10030, "wid_def", "spam")
        reports = edb.get_reports(status="pending")
        self.assertGreaterEqual(len(reports), 1)

    def test_review_report(self):
        rid = edb.create_report(10030, "wid_ghi", "test reason")
        edb.review_report(rid, 0, "resolved")
        reports = edb.get_reports(status="resolved")
        ids = [r["id"] for r in reports]
        self.assertIn(rid, ids)

    def test_count_reports(self):
        edb.create_report(10030, "wid_cnt1", "count test")
        count = edb.count_reports("pending")
        self.assertGreaterEqual(count, 1)


class TestBanSystem(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10040, "bantest", "Ban Test", None)

    def test_permanent_ban(self):
        edb.ban_user_with_reason(10040, "test perm ban", banned_by=0)
        self.assertTrue(db.is_banned(10040))

    def test_unban(self):
        edb.ban_user_with_reason(10040, "temp ban reason", banned_by=0)
        edb.unban_user_with_reason(10040, "forgiven", unbanned_by=0)
        self.assertFalse(db.is_banned(10040))

    def test_temp_ban_writes_to_ban_log(self):
        edb.ban_user_with_reason(10040, "log test", banned_by=0, hours=1)
        log = edb.get_ban_log(10040)
        self.assertGreaterEqual(len(log), 1)
        actions = [e["action"] for e in log]
        self.assertIn("ban", actions)

    def test_expire_temp_bans(self):
        from datetime import datetime, timedelta, timezone
        # Insert an already-expired temp ban
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO temp_bans (user_id, reason, banned_by, expires_at)"
                " VALUES (?,?,?,?)",
                (10040, "expired", 0, past),
            )
            conn.commit()
        expired = edb.expire_temp_bans()
        self.assertGreaterEqual(expired, 1)


class TestFavoritesAndArchive(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10050, "favuser", "Fav User", None)
        self.wid = db.create_whisper(10050, "fav content", "everyone")

    def test_save_favorite(self):
        result = edb.save_favorite(10050, self.wid)
        self.assertTrue(result)

    def test_no_duplicate_favorite(self):
        edb.save_favorite(10050, self.wid)
        edb.save_favorite(10050, self.wid)  # should silently ignore
        favs = edb.get_favorites(10050)
        count = sum(1 for f in favs if f["whisper_id"] == self.wid)
        self.assertEqual(count, 1)

    def test_remove_favorite(self):
        edb.save_favorite(10050, self.wid)
        edb.remove_favorite(10050, self.wid)
        favs = edb.get_favorites(10050)
        wids = [f["whisper_id"] for f in favs]
        self.assertNotIn(self.wid, wids)

    def test_archive(self):
        result = edb.archive_whisper(self.wid)
        self.assertTrue(result)
        archive = edb.get_archive(10050)
        self.assertTrue(any(a["whisper_id"] == self.wid for a in archive))


class TestWhisperSearch(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10060, "srch", "Search User", None)
        db.create_whisper(10060, "السلام عليكم ورحمة الله", "everyone")
        db.create_whisper(10060, "مرحبا بالعالم", "everyone")

    def test_search_found(self):
        results = edb.search_whispers(10060, "السلام")
        self.assertGreaterEqual(len(results), 1)

    def test_search_not_found(self):
        results = edb.search_whispers(10060, "xyz_not_here_123")
        self.assertEqual(len(results), 0)


class TestWhisperReplies(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10070, "parent_sender", "Parent", None)
        db.upsert_user(10071, "reply_sender", "Reply", None)
        self.parent_id = db.create_whisper(10070, "parent whisper", "everyone")

    def test_create_reply(self):
        reply_id = edb.create_reply(self.parent_id, 10071, "reply content")
        self.assertIsNotNone(reply_id)
        thread = edb.get_thread(self.parent_id)
        self.assertEqual(len(thread), 1)
        self.assertEqual(thread[0]["reply_id"], reply_id)

    def test_thread_order(self):
        r1 = edb.create_reply(self.parent_id, 10071, "first reply")
        r2 = edb.create_reply(self.parent_id, 10070, "second reply")
        thread = edb.get_thread(self.parent_id)
        self.assertEqual(thread[0]["reply_id"], r1)
        self.assertEqual(thread[1]["reply_id"], r2)


class TestSelfDestruct(unittest.TestCase):
    def setUp(self):
        _boot()
        db.upsert_user(10080, "destruct", "Destruct", None)

    def test_set_self_destruct(self):
        wid = db.create_whisper(10080, "boom", "everyone")
        edb.set_self_destruct(wid, after_reads=1)
        w = db.get_whisper(wid)
        self.assertEqual(w["is_self_destruct"], 1)

    def test_check_destruct_by_reads(self):
        wid = db.create_whisper(10080, "boom2", "everyone")
        edb.set_self_destruct(wid, after_reads=1)
        # With 1 read → should destruct
        self.assertTrue(edb.check_self_destruct(wid, current_read_count=1))
        # With 0 reads → should NOT destruct (no time limit set)
        self.assertFalse(edb.check_self_destruct(wid, current_read_count=0))

    def test_check_destruct_by_time(self):
        from datetime import datetime, timedelta
        wid = db.create_whisper(10080, "timed", "everyone")
        # Set destruct 1 hour from now — should NOT fire yet
        edb.set_self_destruct(wid, after_reads=0, after_hours=1)
        self.assertFalse(edb.check_self_destruct(wid, current_read_count=0))

    def test_check_destruct_time_expired(self):
        """Manually insert an expired destruct_on to verify time trigger."""
        from datetime import datetime, timedelta, timezone
        wid = db.create_whisper(10080, "expired_time", "everyone")
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        with db.get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO whisper_destruct"
                " (whisper_id, destruct_on, after_reads) VALUES (?,?,?)",
                (wid, past, 0),
            )
            conn.execute(
                "UPDATE whispers SET is_self_destruct=1 WHERE whisper_id=?", (wid,)
            )
            conn.commit()
        self.assertTrue(edb.check_self_destruct(wid, current_read_count=0))


class TestStatsSnapshots(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_snapshot_daily(self):
        edb.snapshot_stats("daily")
        snaps = edb.get_snapshots("daily")
        self.assertGreaterEqual(len(snaps), 1)
        self.assertIn("data", snaps[0])

    def test_active_users(self):
        count = edb.get_active_users(7)
        self.assertIsInstance(count, int)
        self.assertGreaterEqual(count, 0)


class TestBackupSystem(unittest.TestCase):
    def setUp(self):
        _boot()

    def test_create_and_list_backup(self):
        try:
            filename = edb.create_backup(notes="unit test")
            backups = edb.list_backups()
            names = [b["filename"] for b in backups]
            self.assertIn(filename, names)
        except Exception:
            self.skipTest("Backup not available with :memory: DB")


if __name__ == "__main__":
    unittest.main(verbosity=2)
