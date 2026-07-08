"""
tests/test_enterprise_handlers.py — Tests for enterprise/handlers_enterprise.py

Covers handler-level enterprise features:
  - XP and ranking system
  - Achievement system
  - Referral/invite system
  - Activity logging
  - Report system
  - Favorites and archive
  - Whisper search
  - Backup system
  - Leaderboard
"""
import os
import sys
import unittest
from unittest.mock import MagicMock, patch
import tempfile
import atexit
import json

_tmpdb = tempfile.mktemp(suffix="_enterprise_handler_test.db")
os.environ["DATABASE_PATH"] = _tmpdb
os.environ["BOT_TOKEN"]     = "0:test_placeholder"
os.environ["ADMIN_IDS"]     = "999"
atexit.register(lambda: os.path.exists(_tmpdb) and os.unlink(_tmpdb))

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from enterprise.db_enterprise import (
    init_enterprise_db, get_xp, award_xp, get_user_achievements,
    check_and_grant_achievements, generate_referral_code, get_user_by_referral_code,
    register_invite, count_invites, log_activity, get_activity_log,
    create_report, get_reports, review_report, count_reports,
    save_favorite, remove_favorite, get_favorites,
    archive_whisper, get_archive, search_whispers,
    xp_leaderboard, create_backup, list_backups,
)


def _boot():
    db.init_db()
    init_enterprise_db()


class TestEnterpriseXP(unittest.TestCase):
    """Test XP system used by /rank command handler."""

    def setUp(self):
        _boot()

    def test_xp_starts_at_zero(self):
        db.upsert_user(60401, "xpuser", "XP User", None)
        xp_data = get_xp(60401)
        self.assertEqual(xp_data["xp"], 0)
        self.assertEqual(xp_data["level"], 1)

    def test_award_xp_increases(self):
        db.upsert_user(60402, "xpuser2", "XP User2", None)
        award_xp(60402, 10, reason="test")
        xp_data = get_xp(60402)
        self.assertEqual(xp_data["xp"], 10)

    def test_award_xp_multiple_times(self):
        db.upsert_user(60403, "xpuser3", "XP User3", None)
        award_xp(60403, 10, reason="test")
        award_xp(60403, 20, reason="test")
        xp_data = get_xp(60403)
        self.assertEqual(xp_data["xp"], 30)

    def test_xp_level_up(self):
        db.upsert_user(60403, "xpuser3", "XP User3", None)
        award_xp(60403, 9999, reason="level up")
        xp_data = get_xp(60403)
        self.assertGreater(xp_data["level"], 1)

    def test_xp_nonexistent_user(self):
        xp_data = get_xp(99999)
        self.assertIsNotNone(xp_data)

    def test_leaderboard_returns_sorted(self):
        db.upsert_user(60410, "top1", "Top1", None)
        db.upsert_user(60411, "top2", "Top2", None)
        award_xp(60410, 100, reason="test")
        award_xp(60411, 50, reason="test")
        board = xp_leaderboard(limit=5)
        self.assertGreaterEqual(len(board), 2)
        self.assertGreaterEqual(board[0]["xp"], board[1]["xp"])


class TestEnterpriseAchievements(unittest.TestCase):
    """Test achievement system used by /achievements command handler."""

    def setUp(self):
        _boot()

    def test_first_whisper_achievement(self):
        db.upsert_user(60420, "achuser", "Ach User", None)
        db.create_whisper(60420, "first whisper", "everyone")
        granted = check_and_grant_achievements(60420)
        self.assertIn("first_whisper", granted)

    def test_whisper_10_achievement(self):
        db.upsert_user(60421, "achuser2", "Ach User2", None)
        for i in range(10):
            db.create_whisper(60421, f"whisper {i}", "everyone")
        granted = check_and_grant_achievements(60421)
        self.assertIn("whisper_10", granted)

    def test_achievements_stored(self):
        db.upsert_user(60422, "achuser3", "Ach User3", None)
        db.create_whisper(60422, "test", "everyone")
        check_and_grant_achievements(60422)
        achievements = get_user_achievements(60422)
        achievement_codes = [a["code"] for a in achievements]
        self.assertIn("first_whisper", achievement_codes)

    def test_achievement_not_duplicated(self):
        db.upsert_user(60423, "achuser4", "Ach User4", None)
        db.create_whisper(60423, "test", "everyone")
        check_and_grant_achievements(60423)
        check_and_grant_achievements(60423)
        achievements = get_user_achievements(60423)
        first_count = sum(1 for a in achievements if a["code"] == "first_whisper")
        self.assertEqual(first_count, 1)

    def test_no_achievements_for_new_user(self):
        db.upsert_user(60424, "achuser5", "Ach User5", None)
        achievements = get_user_achievements(60424)
        self.assertEqual(len(achievements), 0)


class TestEnterpriseReferrals(unittest.TestCase):
    """Test referral/invite system used by /invite command handler."""

    def setUp(self):
        _boot()

    def test_generate_referral_code(self):
        db.upsert_user(60430, "inviter", "Inviter", None)
        code = generate_referral_code(60430)
        self.assertIsNotNone(code)
        self.assertTrue(len(code) > 0)

    def test_referral_code_lookup(self):
        db.upsert_user(60430, "inviter", "Inviter", None)
        code = generate_referral_code(60430)
        user_id = get_user_by_referral_code(code)
        self.assertEqual(user_id, 60430)

    def test_referral_code_nonexistent(self):
        user_id = get_user_by_referral_code("nonexistent_code_xyz")
        self.assertIsNone(user_id)

    def test_register_invite(self):
        db.upsert_user(60431, "inviter2", "Inviter2", None)
        db.upsert_user(60432, "invitee", "Invitee", None)
        register_invite(60431, 60432)
        self.assertEqual(count_invites(60431), 1)

    def test_register_multiple_invites(self):
        db.upsert_user(60433, "inviter3", "Inviter3", None)
        db.upsert_user(60434, "invitee1", "Invitee1", None)
        db.upsert_user(60435, "invitee2", "Invitee2", None)
        register_invite(60433, 60434)
        register_invite(60433, 60435)
        self.assertEqual(count_invites(60433), 2)

    def test_invite_grants_xp(self):
        db.upsert_user(60436, "inviter4", "Inviter4", None)
        db.upsert_user(60437, "invitee3", "Invitee3", None)
        register_invite(60436, 60437)
        xp_data = get_xp(60436)
        self.assertGreater(xp_data["xp"], 0)


class TestEnterpriseActivityLog(unittest.TestCase):
    """Test activity logging used by /activity command handler."""

    def setUp(self):
        _boot()

    def test_log_activity(self):
        db.upsert_user(60440, "actuser", "Act User", None)
        log_activity(60440, "login")
        log = get_activity_log(60440)
        actions = [e["action"] for e in log]
        self.assertIn("login", actions)

    def test_log_multiple_activities(self):
        db.upsert_user(60441, "actuser2", "Act User2", None)
        log_activity(60441, "whisper_sent")
        log_activity(60441, "whisper_read")
        log = get_activity_log(60441)
        self.assertEqual(len(log), 2)

    def test_log_with_metadata(self):
        db.upsert_user(60442, "actuser3", "Act User3", None)
        log_activity(60442, "test_action", {"key": "value"})
        log = get_activity_log(60442)
        self.assertEqual(len(log), 1)

    def test_activity_log_empty_for_new_user(self):
        db.upsert_user(60443, "actuser4", "Act User4", None)
        log = get_activity_log(60443)
        self.assertEqual(len(log), 0)


class TestEnterpriseReports(unittest.TestCase):
    """Test report system used by /report and /reports command handlers."""

    def setUp(self):
        _boot()
        db.upsert_user(60450, "reporter", "Reporter", None)
        db.upsert_user(60451, "sender", "Sender", None)
        self.wid = db.create_whisper(60451, "reportable content", "everyone")

    def test_create_report(self):
        rid = create_report(60450, self.wid, "inappropriate content")
        self.assertIsNotNone(rid)

    def test_get_reports_pending(self):
        create_report(60450, self.wid, "spam")
        reports = get_reports(status="pending")
        self.assertGreaterEqual(len(reports), 1)

    def test_review_report(self):
        rid = create_report(60450, self.wid, "test report")
        review_report(rid, 999, "resolved")
        reports = get_reports(status="resolved")
        rids = [r["id"] for r in reports]
        self.assertIn(rid, rids)

    def test_count_reports_pending(self):
        create_report(60450, self.wid, "another report")
        count = count_reports("pending")
        self.assertGreaterEqual(count, 1)

    def test_count_reports_resolved(self):
        rid = create_report(60450, self.wid, "resolve me")
        review_report(rid, 999, "resolved")
        count = count_reports("resolved")
        self.assertGreaterEqual(count, 1)

    def test_review_invalid_report_no_crash(self):
        review_report(99999, 999, "resolved")


class TestEnterpriseFavorites(unittest.TestCase):
    """Test favorites system used by enterprise handlers."""

    def setUp(self):
        _boot()
        db.upsert_user(60460, "favuser", "Fav User", None)
        self.wid = db.create_whisper(60460, "fav content", "everyone")

    def test_save_and_get_favorite(self):
        save_favorite(60460, self.wid)
        favs = get_favorites(60460)
        wids = [f["whisper_id"] for f in favs]
        self.assertIn(self.wid, wids)

    def test_remove_favorite(self):
        save_favorite(60460, self.wid)
        remove_favorite(60460, self.wid)
        favs = get_favorites(60460)
        wids = [f["whisper_id"] for f in favs]
        self.assertNotIn(self.wid, wids)

    def test_favorites_empty_for_new_user(self):
        db.upsert_user(60461, "newfav", "New Fav", None)
        favs = get_favorites(60461)
        self.assertEqual(len(favs), 0)

    def test_remove_nonexistent_favorite_no_crash(self):
        remove_favorite(60460, "nonexistent_wid")


class TestEnterpriseArchive(unittest.TestCase):
    """Test archive system used by enterprise handlers."""

    def setUp(self):
        _boot()
        db.upsert_user(60470, "archuser", "Arch User", None)
        self.wid = db.create_whisper(60470, "archive content", "everyone")

    def test_archive_whisper(self):
        archive_whisper(self.wid)
        archive = get_archive(60470)
        self.assertTrue(any(a["whisper_id"] == self.wid for a in archive))

    def test_archive_nonexistent_no_crash(self):
        archive_whisper("nonexistent_wid")


class TestEnterpriseSearch(unittest.TestCase):
    """Test whisper search used by /search command handler."""

    def setUp(self):
        _boot()
        db.upsert_user(60480, "searchuser", "Search User", None)
        db.create_whisper(60480, "find this whisper", "everyone")
        db.create_whisper(60480, "another message", "everyone")

    def test_search_finds_content(self):
        results = search_whispers(60480, "find")
        self.assertGreaterEqual(len(results), 1)

    def test_search_no_match(self):
        results = search_whispers(60480, "zzz_no_match_zzz")
        self.assertEqual(len(results), 0)

    def test_search_empty_query(self):
        results = search_whispers(60480, "")
        self.assertIsNotNone(results)


class TestEnterpriseBackup(unittest.TestCase):
    """Test backup system used by /backup command handler."""

    def setUp(self):
        _boot()

    def test_create_and_list_backups(self):
        backup_id = create_backup(created_by=999)
        self.assertIsNotNone(backup_id)
        backups = list_backups()
        backup_filenames = [b["filename"] for b in backups]
        self.assertIn(backup_id, backup_filenames)

    def test_list_backups_when_empty(self):
        backups = list_backups()
        self.assertIsNotNone(backups)


if __name__ == "__main__":
    unittest.main(verbosity=2)
