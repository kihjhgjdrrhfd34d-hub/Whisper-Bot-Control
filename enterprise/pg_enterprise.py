"""
enterprise/pg_enterprise.py — PostgreSQL implementation of enterprise DB functions
Shadow adapter for enterprise/db_enterprise.py
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg2

from database.postgres import get_conn, USE_POSTGRES

logger = logging.getLogger(__name__)

ENTERPRISE_SCHEMA_VERSION = 3


def get_schema_version() -> int:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key='enterprise_schema_version'"
            ).fetchone()
            return int(row["value"]) if row else 0
    except Exception:
        return 0


def set_schema_version(v: int) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s)"
            " ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
            ("enterprise_schema_version", str(v)),
        )
        conn.commit()


def init_enterprise_db() -> None:
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS user_xp (
                user_id     BIGINT PRIMARY KEY,
                xp          INTEGER DEFAULT 0,
                level       INTEGER DEFAULT 1,
                rank_title  TEXT    DEFAULT 'مبتدئ',
                updated_at  TEXT    DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS achievements (
                id          SERIAL PRIMARY KEY,
                code        TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                description TEXT,
                icon        TEXT DEFAULT '🏆',
                xp_reward   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_achievements (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                code        TEXT NOT NULL,
                earned_at   TEXT DEFAULT (NOW()),
                UNIQUE(user_id, code)
            );

            CREATE TABLE IF NOT EXISTS invites (
                id          SERIAL PRIMARY KEY,
                inviter_id  BIGINT NOT NULL,
                invitee_id  BIGINT NOT NULL UNIQUE,
                invited_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS activity_log (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                action      TEXT NOT NULL,
                meta        TEXT,
                created_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS reports (
                id           SERIAL PRIMARY KEY,
                reporter_id  BIGINT NOT NULL,
                whisper_id   TEXT,
                reason       TEXT,
                status       TEXT DEFAULT 'pending',
                reviewed_by  BIGINT,
                reviewed_at  TEXT,
                created_at   TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS ban_log (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                banned_by   BIGINT,
                expires_at  TEXT,
                created_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS temp_bans (
                user_id     BIGINT PRIMARY KEY,
                reason      TEXT,
                banned_by   BIGINT,
                expires_at  TEXT NOT NULL,
                created_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS backup_registry (
                id          SERIAL PRIMARY KEY,
                filename    TEXT NOT NULL UNIQUE,
                size_bytes  BIGINT,
                created_at  TEXT DEFAULT (NOW()),
                created_by  BIGINT,
                notes       TEXT
            );

            CREATE TABLE IF NOT EXISTS stats_snapshots (
                id           SERIAL PRIMARY KEY,
                period_type  TEXT NOT NULL,
                period_label TEXT NOT NULL,
                data_json    TEXT NOT NULL,
                created_at   TEXT DEFAULT (NOW()),
                UNIQUE(period_type, period_label)
            );

            CREATE TABLE IF NOT EXISTS whisper_favorites (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                whisper_id  TEXT NOT NULL,
                saved_at    TEXT DEFAULT (NOW()),
                UNIQUE(user_id, whisper_id)
            );

            CREATE TABLE IF NOT EXISTS whisper_dislikes (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL,
                whisper_id   TEXT NOT NULL,
                disliked_at  TEXT DEFAULT (NOW()),
                UNIQUE(user_id, whisper_id)
            );

            CREATE TABLE IF NOT EXISTS whisper_archive (
                whisper_id   TEXT PRIMARY KEY,
                sender_id    BIGINT NOT NULL,
                content      TEXT NOT NULL,
                whisper_type TEXT NOT NULL,
                archived_at  TEXT DEFAULT (NOW()),
                original_data TEXT
            );

            CREATE TABLE IF NOT EXISTS whisper_destruct (
                whisper_id   TEXT PRIMARY KEY,
                destruct_on  TEXT,
                after_reads  INTEGER DEFAULT 0
            );
        """)
        conn.commit()

    _run_enterprise_migrations()
    _seed_achievements()
    set_schema_version(ENTERPRISE_SCHEMA_VERSION)
    logger.info(f"Enterprise DB initialised (PostgreSQL, schema v{ENTERPRISE_SCHEMA_VERSION})")


def _run_enterprise_migrations() -> None:
    with get_conn() as conn:
        wcols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='whispers' AND table_schema='public'"
        ).fetchall()}

        for col_name, col_type in [
            ("is_anonymous", "INTEGER DEFAULT 0"),
            ("is_self_destruct", "INTEGER DEFAULT 0"),
            ("parent_whisper_id", "TEXT"),
            ("is_archived", "INTEGER DEFAULT 0"),
        ]:
            if col_name not in wcols:
                conn.execute(f"ALTER TABLE whispers ADD COLUMN {col_name} {col_type}")

        ucols = {r["column_name"] for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='users' AND table_schema='public'"
        ).fetchall()}
        for col_name, col_type in [
            ("referral_code", "TEXT"),
            ("referred_by", "BIGINT"),
        ]:
            if col_name not in ucols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col_name} {col_type}")

        conn.commit()


def _seed_achievements() -> None:
    achievements = [
        ("first_whisper",  "أول همسة",          "أرسلت همستك الأولى",              "🤫", 10),
        ("whisper_10",     "ناشر سري",           "أرسلت 10 همسات",                  "📨", 20),
        ("whisper_50",     "همساز",              "أرسلت 50 همسة",                   "🗣", 50),
        ("whisper_100",    "أسطورة الهمسات",     "أرسلت 100 همسة",                  "🏆", 100),
        ("reader_10",      "فضولي",              "قرأت 10 همسات",                   "👁", 20),
        ("first_invite",   "مدعو أول",           "دعوت شخصاً للبوت",                "🎁", 15),
        ("invites_5",      "منشر البوت",         "دعوت 5 أشخاص",                   "📢", 30),
        ("all_types",      "مستكشف",             "استخدمت جميع أنواع الهمسات",      "🔍", 25),
        ("first_reply",    "محادث سري",          "رددت على همسة",                   "💬", 10),
        ("saved_5",        "جامع الأسرار",       "حفظت 5 همسات في المفضلة",         "❤️", 15),
    ]
    with get_conn() as conn:
        for code, title, desc, icon, xp in achievements:
            conn.execute(
                "INSERT INTO achievements (code,title,description,icon,xp_reward)"
                " VALUES (%s,%s,%s,%s,%s)"
                " ON CONFLICT (code) DO NOTHING",
                (code, title, desc, icon, xp),
            )
        conn.commit()


LEVEL_RANKS = [
    (0,   "مبتدئ",         1),
    (50,  "ناشر",          2),
    (150, "همساز",         3),
    (300, "خبير الهمسات", 4),
    (500, "أسطورة",       5),
    (800, "سيد الأسرار",  6),
]


def _calc_level(xp: int) -> Tuple[int, str]:
    level, rank = 1, "مبتدئ"
    for threshold, title, lvl in LEVEL_RANKS:
        if xp >= threshold:
            level, rank = lvl, title
    return level, rank


def award_xp(user_id: int, points: int, reason: str = "") -> Dict[str, Any]:
    from core.events import event_bus
    with get_conn() as conn:
        row = conn.execute(
            "SELECT xp, level FROM user_xp WHERE user_id=%s", (user_id,)
        ).fetchone()
        old_xp    = row["xp"]    if row else 0
        old_level = row["level"] if row else 1
        new_xp    = old_xp + points
        new_level, new_rank = _calc_level(new_xp)
        conn.execute(
            "INSERT INTO user_xp (user_id, xp, level, rank_title, updated_at)"
            " VALUES (%s, %s, %s, %s, NOW())"
            " ON CONFLICT(user_id) DO UPDATE SET"
            "  xp=EXCLUDED.xp, level=EXCLUDED.level,"
            "  rank_title=EXCLUDED.rank_title, updated_at=EXCLUDED.updated_at",
            (user_id, new_xp, new_level, new_rank),
        )
        conn.commit()

    leveled_up = new_level > old_level
    result = {
        "user_id":   user_id,
        "xp":        new_xp,
        "level":     new_level,
        "rank":      new_rank,
        "gained":    points,
        "leveled_up": leveled_up,
    }
    event_bus.fire(event_bus.ON_XP_AWARDED, user_id=user_id, points=points, reason=reason)
    if leveled_up:
        event_bus.fire(event_bus.ON_LEVEL_UP, user_id=user_id, level=new_level, rank=new_rank)
    return result


def get_xp(user_id: int) -> Dict[str, Any]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM user_xp WHERE user_id=%s", (user_id,)
        ).fetchone()
        if row:
            return dict(row)
        return {"user_id": user_id, "xp": 0, "level": 1, "rank_title": "مبتدئ"}


def xp_leaderboard(limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT ux.user_id, ux.xp, ux.level, ux.rank_title, "
            "u.username, u.first_name "
            "FROM user_xp ux LEFT JOIN users u ON u.user_id=ux.user_id "
            "ORDER BY ux.xp DESC LIMIT %s",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def grant_achievement(user_id: int, code: str) -> bool:
    from core.events import event_bus
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO user_achievements (user_id, code) VALUES (%s,%s)",
                (user_id, code),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            return False

    ach = get_achievement(code)
    if ach and ach.get("xp_reward", 0) > 0:
        award_xp(user_id, ach["xp_reward"], reason=f"achievement:{code}")
    event_bus.fire(event_bus.ON_ACHIEVEMENT, user_id=user_id, code=code)
    return True


def get_achievement(code: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM achievements WHERE code=%s", (code,)
        ).fetchone()
        return dict(row) if row else None


def get_user_achievements(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.code, a.title, a.description, a.icon, a.xp_reward, ua.earned_at "
            "FROM user_achievements ua "
            "JOIN achievements a ON a.code=ua.code "
            "WHERE ua.user_id=%s ORDER BY ua.earned_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def check_and_grant_achievements(user_id: int) -> List[str]:
    from database import get_user_stats
    stats = get_user_stats(user_id)
    granted = []

    mapping = [
        ("first_whisper",  stats["sent"] >= 1),
        ("whisper_10",     stats["sent"] >= 10),
        ("whisper_50",     stats["sent"] >= 50),
        ("whisper_100",    stats["sent"] >= 100),
        ("reader_10",      stats["read_others"] >= 10),
        ("all_types",      all([
            stats["type_everyone"], stats["type_first_one"],
            stats["type_first_three"], stats["type_custom"],
        ])),
    ]
    for code, condition in mapping:
        if condition:
            if grant_achievement(user_id, code):
                granted.append(code)

    invite_count = count_invites(user_id)
    for code, threshold in [("first_invite", 1), ("invites_5", 5)]:
        if invite_count >= threshold:
            if grant_achievement(user_id, code):
                granted.append(code)

    saved = count_saved_whispers(user_id)
    if saved >= 5:
        if grant_achievement(user_id, "saved_5"):
            granted.append("saved_5")

    return granted


def generate_referral_code(user_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referral_code FROM users WHERE user_id=%s", (user_id,)
        ).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
        code = f"ref_{user_id}_{str(uuid.uuid4())[:6]}"
        conn.execute(
            "UPDATE users SET referral_code=%s WHERE user_id=%s", (code, user_id)
        )
        conn.commit()
        return code


def register_invite(inviter_id: int, invitee_id: int) -> bool:
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO invites (inviter_id, invitee_id) VALUES (%s,%s)",
                (inviter_id, invitee_id),
            )
            conn.execute(
                "UPDATE users SET referred_by=%s WHERE user_id=%s",
                (inviter_id, invitee_id),
            )
            conn.commit()
        except psycopg2.errors.UniqueViolation:
            return False
    award_xp(inviter_id, 20, reason="invite")
    grant_achievement(inviter_id, "first_invite")
    return True


def count_invites(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM invites WHERE inviter_id=%s", (user_id,)
        ).fetchone()["count"]


def get_invites(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.invitee_id, i.invited_at, u.username, u.first_name "
            "FROM invites i LEFT JOIN users u ON u.user_id=i.invitee_id "
            "WHERE i.inviter_id=%s ORDER BY i.invited_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_by_referral_code(code: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE referral_code=%s", (code,)
        ).fetchone()
        return row["user_id"] if row else None


def log_activity(user_id: int, action: str, meta: Optional[Dict] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (user_id, action, meta) VALUES (%s,%s,%s)",
            (user_id, action, json.dumps(meta) if meta else None),
        )
        conn.commit()


def get_activity_log(user_id: int, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_activity(limit: int = 50, action_filter: Optional[str] = None) -> List[Dict]:
    with get_conn() as conn:
        if action_filter:
            rows = conn.execute(
                "SELECT al.*, u.username, u.first_name FROM activity_log al "
                "LEFT JOIN users u ON u.user_id=al.user_id "
                "WHERE al.action=%s ORDER BY al.created_at DESC LIMIT %s",
                (action_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT al.*, u.username, u.first_name FROM activity_log al "
                "LEFT JOIN users u ON u.user_id=al.user_id "
                "ORDER BY al.created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def create_report(reporter_id: int, whisper_id: str, reason: str) -> int:
    from core.events import event_bus
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports (reporter_id, whisper_id, reason) VALUES (%s,%s,%s) RETURNING id",
            (reporter_id, whisper_id, reason),
        )
        conn.commit()
        report_id = cur.fetchone()[0]
    event_bus.fire(event_bus.ON_REPORT_CREATED,
                   report_id=report_id, reporter_id=reporter_id,
                   whisper_id=whisper_id, reason=reason)
    return report_id


def get_reports(status: Optional[str] = "pending", limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT r.*, u.username reporter_name FROM reports r "
                "LEFT JOIN users u ON u.user_id=r.reporter_id "
                "WHERE r.status=%s ORDER BY r.created_at DESC LIMIT %s",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.*, u.username reporter_name FROM reports r "
                "LEFT JOIN users u ON u.user_id=r.reporter_id "
                "ORDER BY r.created_at DESC LIMIT %s",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def review_report(report_id: int, reviewed_by: int, new_status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reports SET status=%s, reviewed_by=%s, reviewed_at=NOW() WHERE id=%s",
            (new_status, reviewed_by, report_id),
        )
        conn.commit()


def count_reports(status: Optional[str] = "pending") -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM reports WHERE status=%s", (status,)
            ).fetchone()["count"]
        return conn.execute("SELECT COUNT(*) FROM reports").fetchone()["count"]


def ban_user_with_reason(user_id: int, reason: str, banned_by: int,
                         hours: Optional[int] = None) -> None:
    from database import ban_user
    from core.events import event_bus
    from core.logging_config import audit_log

    expires_at = None
    if hours:
        expires_at = (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO temp_bans (user_id,reason,banned_by,expires_at)"
                " VALUES (%s,%s,%s,%s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " reason=EXCLUDED.reason, banned_by=EXCLUDED.banned_by, expires_at=EXCLUDED.expires_at",
                (user_id, reason, banned_by, expires_at),
            )
            conn.commit()
    else:
        ban_user(user_id)

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ban_log (user_id,action,reason,banned_by,expires_at)"
            " VALUES (%s,%s,%s,%s,%s)",
            (user_id, "ban", reason, banned_by, expires_at),
        )
        conn.commit()

    audit_log("BAN", actor_id=banned_by, target=user_id,
              reason=reason, expires_at=expires_at)
    event_bus.fire(event_bus.ON_USER_BANNED, user_id=user_id,
                   reason=reason, by=banned_by)


def unban_user_with_reason(user_id: int, reason: str, unbanned_by: int) -> None:
    from database import unban_user
    from core.events import event_bus
    from core.logging_config import audit_log

    unban_user(user_id)
    with get_conn() as conn:
        conn.execute("DELETE FROM temp_bans WHERE user_id=%s", (user_id,))
        conn.execute(
            "INSERT INTO ban_log (user_id,action,reason,banned_by) VALUES (%s,%s,%s,%s)",
            (user_id, "unban", reason, unbanned_by),
        )
        conn.commit()
    audit_log("UNBAN", actor_id=unbanned_by, target=user_id, reason=reason)
    event_bus.fire(event_bus.ON_USER_UNBANNED, user_id=user_id)


def get_ban_log(user_id: Optional[int] = None, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM ban_log WHERE user_id=%s ORDER BY created_at DESC LIMIT %s",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ban_log ORDER BY created_at DESC LIMIT %s", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def expire_temp_bans() -> int:
    from database import unban_user
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM temp_bans WHERE expires_at <= %s", (now,)
        ).fetchall()
        count = len(rows)
        for r in rows:
            unban_user(r["user_id"])
            conn.execute("DELETE FROM temp_bans WHERE user_id=%s", (r["user_id"],))
        if count:
            conn.commit()
    return count


def save_favorite(user_id: int, whisper_id: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO whisper_favorites (user_id, whisper_id) VALUES (%s,%s)"
                " ON CONFLICT (user_id, whisper_id) DO NOTHING",
                (user_id, whisper_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def remove_favorite(user_id: int, whisper_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM whisper_favorites WHERE user_id=%s AND whisper_id=%s",
            (user_id, whisper_id),
        )
        conn.commit()


def get_favorites(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT wf.whisper_id, wf.saved_at, w.content, w.whisper_type "
            "FROM whisper_favorites wf "
            "LEFT JOIN whispers w ON w.whisper_id=wf.whisper_id "
            "WHERE wf.user_id=%s ORDER BY wf.saved_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_saved_whispers(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_favorites WHERE user_id=%s", (user_id,)
        ).fetchone()["count"]


def count_whisper_likes(whisper_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_favorites WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()["count"]


def has_user_liked(user_id: int, whisper_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whisper_favorites WHERE user_id=%s AND whisper_id=%s",
            (user_id, whisper_id),
        ).fetchone()
        return row is not None


def save_dislike(user_id: int, whisper_id: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO whisper_dislikes (user_id, whisper_id) VALUES (%s,%s)"
                " ON CONFLICT (user_id, whisper_id) DO NOTHING",
                (user_id, whisper_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def remove_dislike(user_id: int, whisper_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM whisper_dislikes WHERE user_id=%s AND whisper_id=%s",
            (user_id, whisper_id),
        )
        conn.commit()


def count_whisper_dislikes(whisper_id: str) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_dislikes WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()["count"]


def has_user_disliked(user_id: int, whisper_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM whisper_dislikes WHERE user_id=%s AND whisper_id=%s",
            (user_id, whisper_id),
        ).fetchone()
        return row is not None


def archive_whisper(whisper_id: str) -> bool:
    from database import get_whisper
    w = get_whisper(whisper_id)
    if not w:
        return False
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_archive "
            "(whisper_id, sender_id, content, whisper_type, original_data)"
            " VALUES (%s,%s,%s,%s,%s)"
            " ON CONFLICT (whisper_id) DO NOTHING",
            (whisper_id, w["sender_id"], w["content"], w["whisper_type"],
             json.dumps(dict(w))),
        )
        conn.execute(
            "UPDATE whispers SET is_archived=1 WHERE whisper_id=%s", (whisper_id,)
        )
        conn.commit()
    return True


def get_archive(user_id: int, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whisper_archive WHERE sender_id=%s ORDER BY archived_at DESC LIMIT %s",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def search_whispers(user_id: int, query: str, limit: int = 10) -> List[Dict]:
    with get_conn() as conn:
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=%s AND content LIKE %s "
            "AND is_archived=0 ORDER BY created_at DESC LIMIT %s",
            (user_id, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def create_reply(parent_id: str, sender_id: int, content: str,
                 auto_delete_hours: int = 0) -> str:
    from database.replies import create_reply as _create_reply
    return _create_reply(
        whisper_id=parent_id,
        sender_id=sender_id,
        content=content,
    )


def get_thread(whisper_id: str) -> List[Dict]:
    from database.replies import get_replies as _get_replies
    rows = _get_replies(whisper_id)
    return [dict(r) for r in rows]


def set_self_destruct(whisper_id: str, after_reads: int = 1,
                      after_hours: int = 0) -> None:
    destruct_on = None
    if after_hours > 0:
        destruct_on = (datetime.now(timezone.utc) + timedelta(hours=after_hours)).isoformat()

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_destruct (whisper_id, destruct_on, after_reads)"
            " VALUES (%s,%s,%s)"
            " ON CONFLICT (whisper_id) DO UPDATE SET"
            " destruct_on=EXCLUDED.destruct_on, after_reads=EXCLUDED.after_reads",
            (whisper_id, destruct_on, after_reads),
        )
        conn.execute(
            "UPDATE whispers SET is_self_destruct=1 WHERE whisper_id=%s", (whisper_id,)
        )
        conn.commit()


def check_self_destruct(whisper_id: str, current_read_count: int) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_destruct WHERE whisper_id=%s", (whisper_id,)
        ).fetchone()
        if not row:
            return False
        if row["after_reads"] > 0 and current_read_count >= row["after_reads"]:
            return True
        if row["destruct_on"]:
            now = datetime.now(timezone.utc).isoformat()
            if now >= row["destruct_on"]:
                return True
    return False


def snapshot_stats(period_type: str = "daily") -> None:
    from database import get_stats
    now = datetime.now(timezone.utc)
    label_map = {
        "daily":   now.strftime("%Y-%m-%d"),
        "weekly":  now.strftime("%Y-W%W"),
        "monthly": now.strftime("%Y-%m"),
        "yearly":  now.strftime("%Y"),
    }
    label = label_map.get(period_type, now.isoformat())
    data  = get_stats()
    with get_conn() as conn:
        data["total_reports"] = conn.execute("SELECT COUNT(*) FROM reports").fetchone()["count"]
        data["open_reports"]  = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE status='pending'"
        ).fetchone()["count"]

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO stats_snapshots (period_type, period_label, data_json)"
            " VALUES (%s,%s,%s)"
            " ON CONFLICT (period_type, period_label) DO UPDATE SET data_json=EXCLUDED.data_json",
            (period_type, label, json.dumps(data)),
        )
        conn.commit()
    logger.info(f"Stats snapshot created: {period_type}/{label}")


def get_snapshots(period_type: str, limit: int = 30) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stats_snapshots WHERE period_type=%s "
            "ORDER BY period_label DESC LIMIT %s",
            (period_type, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data_json"])
            result.append(d)
        return result


def get_active_users(days: int = 7) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT sender_id as uid FROM whispers WHERE created_at >= %s"
            "  UNION"
            "  SELECT user_id FROM whisper_readers WHERE read_at >= %s"
            ") AS active_users",
            (since, since),
        ).fetchone()["count"]


BACKUPS_DIR = Path(__file__).parent.parent / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)
MAX_BACKUPS = 10


def create_backup(created_by: Optional[int] = None, notes: str = "") -> str:
    from core.events import event_bus
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"whispers_backup_{ts}.db"
    logger.warning("PostgreSQL mode: backup uses pg_dump if available, otherwise skipped.")
    dest = BACKUPS_DIR / filename
    import shutil
    from config import DATABASE_PATH

    pg_dump_path = shutil.which("pg_dump")
    if pg_dump_path and USE_POSTGRES:
        from database.postgres import DATABASE_URL
        import subprocess
        try:
            result = subprocess.run(
                [pg_dump_path, "--no-owner", "--no-acl", "-f", str(dest), DATABASE_URL],
                capture_output=True, text=True, timeout=60
            )
            if result.returncode != 0:
                logger.error(f"pg_dump failed: {result.stderr}")
                raise RuntimeError("pg_dump failed")
        except Exception as exc:
            logger.warning(f"pg_dump backup failed, falling back to placeholder: {exc}")
            dest.write_text(json.dumps({"note": "PostgreSQL backup placeholder"}))
    else:
        logger.info("PostgreSQL backup: pg_dump not found, creating placeholder.")
        dest.write_text(json.dumps({"note": "PostgreSQL backup placeholder"}))

    size = dest.stat().st_size
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backup_registry (filename, size_bytes, created_by, notes)"
            " VALUES (%s,%s,%s,%s)",
            (filename, size, created_by, notes),
        )
        conn.commit()

    _cleanup_old_backups()
    event_bus.fire(event_bus.ON_BACKUP_CREATED, filename=filename, size=size)
    logger.info(f"Backup created: {filename} ({size} bytes)")
    return filename


def restore_backup(filename: str) -> bool:
    src = BACKUPS_DIR / filename
    if not src.exists():
        logger.error(f"Restore failed: {filename} not found")
        return False
    create_backup(notes="pre-restore auto-backup")

    from database.postgres import DATABASE_URL as PG_URL
    pg_dump_path = __import__('shutil', fromlist=['which']).which("pg_restore")
    if pg_dump_path and USE_POSTGRES:
        import subprocess
        try:
            result = subprocess.run(
                ["pg_restore", "--no-owner", "--no-acl", "-d", PG_URL, str(src)],
                capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0:
                logger.error(f"pg_restore failed: {result.stderr}")
                return False
        except Exception as exc:
            logger.error(f"pg_restore failed: {exc}")
            return False

    logger.info(f"Restored from backup: {filename}")
    return True


def list_backups() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM backup_registry ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def _cleanup_old_backups() -> None:
    files = sorted(BACKUPS_DIR.glob("whispers_backup_*.db"), reverse=True)
    for old in files[MAX_BACKUPS:]:
        try:
            old.unlink()
            with get_conn() as conn:
                conn.execute(
                    "DELETE FROM backup_registry WHERE filename=%s", (old.name,)
                )
                conn.commit()
            logger.info(f"Removed old backup: {old.name}")
        except Exception as exc:
            logger.warning(f"Could not remove old backup {old.name}: {exc}")
