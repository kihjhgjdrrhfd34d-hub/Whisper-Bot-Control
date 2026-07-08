"""
enterprise/db_enterprise.py
───────────────────────────
Enterprise-grade database additions.
100% backward-compatible: only adds new tables/columns, never touches existing ones.

All functions here are NEW — they do not replace or modify anything in database.py.
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Reuse the existing connection helper ──────────────────────────────────────
from database import get_conn, DATABASE_PATH    # noqa: E402  (import after path set)


# ═════════════════════════════════════════════════════════════════════════════
# MIGRATION VERSION TRACKING
# ═════════════════════════════════════════════════════════════════════════════

ENTERPRISE_SCHEMA_VERSION = 3   # bump this when adding new enterprise tables


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
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?,?)",
            ("enterprise_schema_version", str(v)),
        )
        conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
# SCHEMA CREATION
# ═════════════════════════════════════════════════════════════════════════════

def init_enterprise_db() -> None:
    """Create all enterprise tables (idempotent).  Called from main.py after init_db()."""
    with get_conn() as conn:
        conn.executescript("""
            -- ── XP & Levels ───────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS user_xp (
                user_id     INTEGER PRIMARY KEY,
                xp          INTEGER DEFAULT 0,
                level       INTEGER DEFAULT 1,
                rank_title  TEXT    DEFAULT 'مبتدئ',
                updated_at  TEXT    DEFAULT (datetime('now'))
            );

            -- ── Achievements ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS achievements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                description TEXT,
                icon        TEXT DEFAULT '🏆',
                xp_reward   INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS user_achievements (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                code        TEXT NOT NULL,
                earned_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, code)
            );

            -- ── Referral / Invite System ──────────────────────────────────
            CREATE TABLE IF NOT EXISTS invites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                inviter_id  INTEGER NOT NULL,
                invitee_id  INTEGER NOT NULL UNIQUE,
                invited_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Activity Log ──────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS activity_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                action      TEXT NOT NULL,
                meta        TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Reports ───────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                reporter_id  INTEGER NOT NULL,
                whisper_id   TEXT,
                reason       TEXT,
                status       TEXT DEFAULT 'pending',
                reviewed_by  INTEGER,
                reviewed_at  TEXT,
                created_at   TEXT DEFAULT (datetime('now'))
            );

            -- ── Ban Log ───────────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS ban_log (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                banned_by   INTEGER,
                expires_at  TEXT,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Temporary Bans ────────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS temp_bans (
                user_id     INTEGER PRIMARY KEY,
                reason      TEXT,
                banned_by   INTEGER,
                expires_at  TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            -- ── Backup Registry ───────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS backup_registry (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                filename    TEXT NOT NULL UNIQUE,
                size_bytes  INTEGER,
                created_at  TEXT DEFAULT (datetime('now')),
                created_by  INTEGER,
                notes       TEXT
            );

            -- ── Statistics Snapshots ──────────────────────────────────────
            CREATE TABLE IF NOT EXISTS stats_snapshots (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                period_type  TEXT NOT NULL,
                period_label TEXT NOT NULL,
                data_json    TEXT NOT NULL,
                created_at   TEXT DEFAULT (datetime('now')),
                UNIQUE(period_type, period_label)
            );

            -- ── Whisper Favorites ─────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS whisper_favorites (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                whisper_id  TEXT NOT NULL,
                saved_at    TEXT DEFAULT (datetime('now')),
                UNIQUE(user_id, whisper_id)
            );

            -- ── Whisper Archive ───────────────────────────────────────────
            CREATE TABLE IF NOT EXISTS whisper_archive (
                whisper_id   TEXT PRIMARY KEY,
                sender_id    INTEGER NOT NULL,
                content      TEXT NOT NULL,
                whisper_type TEXT NOT NULL,
                archived_at  TEXT DEFAULT (datetime('now')),
                original_data TEXT
            );

            -- ── Whisper Replies ───────────────────────────────────────────
            -- Full schema is owned by database.replies (init_replies_db).
            -- Enterprise uses the same table via database.replies functions.
            -- The old thin schema (parent_id, reply_id) is migrated in
            -- _run_enterprise_migrations() if it exists.

            -- ── Self-Destruct Whispers ────────────────────────────────────
            CREATE TABLE IF NOT EXISTS whisper_destruct (
                whisper_id   TEXT PRIMARY KEY,
                destruct_on  TEXT,
                after_reads  INTEGER DEFAULT 0
            );

            -- ── Anonymous Whispers ────────────────────────────────────────
            -- (handled via whispers.is_anonymous column — migrated below)

            -- ── Migration version ─────────────────────────────────────────
            -- stored in settings table as enterprise_schema_version
        """)
        conn.commit()

    _run_enterprise_migrations()
    _seed_achievements()
    set_schema_version(ENTERPRISE_SCHEMA_VERSION)
    logger.info(f"Enterprise DB initialised (schema v{ENTERPRISE_SCHEMA_VERSION})")


def _run_enterprise_migrations() -> None:
    """Idempotent column additions to existing tables."""
    with get_conn() as conn:
        # Add is_anonymous to whispers table
        wcols = [r[1] for r in conn.execute("PRAGMA table_info(whispers)").fetchall()]
        if "is_anonymous" not in wcols:
            conn.execute(
                "ALTER TABLE whispers ADD COLUMN is_anonymous INTEGER DEFAULT 0"
            )
        if "is_self_destruct" not in wcols:
            conn.execute(
                "ALTER TABLE whispers ADD COLUMN is_self_destruct INTEGER DEFAULT 0"
            )
        if "parent_whisper_id" not in wcols:
            conn.execute(
                "ALTER TABLE whispers ADD COLUMN parent_whisper_id TEXT"
            )
        if "is_archived" not in wcols:
            conn.execute(
                "ALTER TABLE whispers ADD COLUMN is_archived INTEGER DEFAULT 0"
            )
        # Add referral_code to users
        ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()]
        if "referral_code" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN referral_code TEXT")
        if "referred_by" not in ucols:
            conn.execute("ALTER TABLE users ADD COLUMN referred_by INTEGER")

        # Migration: upgrade old thin whisper_replies to the new full schema.
        # Old schema (from enterprise v1) had columns: id, parent_id, reply_id, sender_id
        # New schema (canonical, in database/replies.py): reply_id PK, whisper_id, sender_id,
        #   content, media_type, file_id, created_at
        wr_cols_raw = conn.execute("PRAGMA table_info(whisper_replies)").fetchall()
        if wr_cols_raw:
            wr_col_names = {r[1] for r in wr_cols_raw}
            if "parent_id" in wr_col_names and "whisper_id" not in wr_col_names:
                # Old thin schema detected — drop and let init_replies_db() recreate
                conn.execute("DROP TABLE IF EXISTS whisper_replies")
                conn.commit()
                logger.info("Migration: old thin whisper_replies table dropped; "
                            "new schema will be created by init_replies_db().")

        # Migration: recreate whisper_destruct with nullable destruct_on.
        # Old schema had NOT NULL which breaks read-count-only self-destruct.
        dcols_raw = conn.execute("PRAGMA table_info(whisper_destruct)").fetchall()
        if dcols_raw:
            destruct_on_col = next((r for r in dcols_raw if r[1] == "destruct_on"), None)
            # r[3] is the notnull flag (1 = NOT NULL constraint)
            if destruct_on_col and destruct_on_col[3] == 1:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS whisper_destruct_new (
                        whisper_id   TEXT PRIMARY KEY,
                        destruct_on  TEXT,
                        after_reads  INTEGER DEFAULT 0
                    );
                    INSERT OR IGNORE INTO whisper_destruct_new
                        SELECT whisper_id, destruct_on, after_reads
                        FROM whisper_destruct;
                    DROP TABLE whisper_destruct;
                    ALTER TABLE whisper_destruct_new RENAME TO whisper_destruct;
                """)
                logger.info("Migration: whisper_destruct.destruct_on made nullable.")

        conn.commit()


def _seed_achievements() -> None:
    """Insert built-in achievements (INSERT OR IGNORE — never overwrites)."""
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
                "INSERT OR IGNORE INTO achievements (code,title,description,icon,xp_reward)"
                " VALUES (?,?,?,?,?)",
                (code, title, desc, icon, xp),
            )
        conn.commit()


# ═════════════════════════════════════════════════════════════════════════════
# XP & LEVELS
# ═════════════════════════════════════════════════════════════════════════════

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
    """Award XP to a user. Returns dict with new totals and level-up info."""
    from core.events import event_bus
    with get_conn() as conn:
        row = conn.execute(
            "SELECT xp, level FROM user_xp WHERE user_id=?", (user_id,)
        ).fetchone()
        old_xp    = row["xp"]    if row else 0
        old_level = row["level"] if row else 1
        new_xp    = old_xp + points
        new_level, new_rank = _calc_level(new_xp)
        conn.execute(
            """INSERT INTO user_xp (user_id, xp, level, rank_title, updated_at)
               VALUES (?,?,?,?, datetime('now'))
               ON CONFLICT(user_id) DO UPDATE SET
                 xp=excluded.xp, level=excluded.level,
                 rank_title=excluded.rank_title, updated_at=excluded.updated_at""",
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
            "SELECT * FROM user_xp WHERE user_id=?", (user_id,)
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
            "ORDER BY ux.xp DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# ACHIEVEMENTS
# ═════════════════════════════════════════════════════════════════════════════

def grant_achievement(user_id: int, code: str) -> bool:
    """Grant an achievement. Returns True if newly granted, False if already had it."""
    from core.events import event_bus
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO user_achievements (user_id, code) VALUES (?,?)",
                (user_id, code),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return False   # already earned

    ach = get_achievement(code)
    if ach and ach.get("xp_reward", 0) > 0:
        award_xp(user_id, ach["xp_reward"], reason=f"achievement:{code}")
    event_bus.fire(event_bus.ON_ACHIEVEMENT, user_id=user_id, code=code)
    return True


def get_achievement(code: str) -> Optional[Dict]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM achievements WHERE code=?", (code,)
        ).fetchone()
        return dict(row) if row else None


def get_user_achievements(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT a.code, a.title, a.description, a.icon, a.xp_reward, ua.earned_at "
            "FROM user_achievements ua "
            "JOIN achievements a ON a.code=ua.code "
            "WHERE ua.user_id=? ORDER BY ua.earned_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def check_and_grant_achievements(user_id: int) -> List[str]:
    """Check thresholds and auto-grant unlocked achievements. Returns list of newly granted codes."""
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

    # Invite achievements
    invite_count = count_invites(user_id)
    for code, threshold in [("first_invite", 1), ("invites_5", 5)]:
        if invite_count >= threshold:
            if grant_achievement(user_id, code):
                granted.append(code)

    # Saved whispers
    saved = count_saved_whispers(user_id)
    if saved >= 5:
        if grant_achievement(user_id, "saved_5"):
            granted.append("saved_5")

    return granted


# ═════════════════════════════════════════════════════════════════════════════
# INVITES / REFERRALS
# ═════════════════════════════════════════════════════════════════════════════

def generate_referral_code(user_id: int) -> str:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT referral_code FROM users WHERE user_id=?", (user_id,)
        ).fetchone()
        if row and row["referral_code"]:
            return row["referral_code"]
        code = f"ref_{user_id}_{str(uuid.uuid4())[:6]}"
        conn.execute(
            "UPDATE users SET referral_code=? WHERE user_id=?", (code, user_id)
        )
        conn.commit()
        return code


def register_invite(inviter_id: int, invitee_id: int) -> bool:
    """Record that invitee joined via inviter. Returns False if already recorded."""
    with get_conn() as conn:
        try:
            conn.execute(
                "INSERT INTO invites (inviter_id, invitee_id) VALUES (?,?)",
                (inviter_id, invitee_id),
            )
            conn.execute(
                "UPDATE users SET referred_by=? WHERE user_id=?",
                (inviter_id, invitee_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return False
    award_xp(inviter_id, 20, reason="invite")
    grant_achievement(inviter_id, "first_invite")
    return True


def count_invites(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM invites WHERE inviter_id=?", (user_id,)
        ).fetchone()[0]


def get_invites(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT i.invitee_id, i.invited_at, u.username, u.first_name "
            "FROM invites i LEFT JOIN users u ON u.user_id=i.invitee_id "
            "WHERE i.inviter_id=? ORDER BY i.invited_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_user_by_referral_code(code: str) -> Optional[int]:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT user_id FROM users WHERE referral_code=?", (code,)
        ).fetchone()
        return row["user_id"] if row else None


# ═════════════════════════════════════════════════════════════════════════════
# ACTIVITY LOG
# ═════════════════════════════════════════════════════════════════════════════

def log_activity(user_id: int, action: str, meta: Optional[Dict] = None) -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO activity_log (user_id, action, meta) VALUES (?,?,?)",
            (user_id, action, json.dumps(meta) if meta else None),
        )
        conn.commit()


def get_activity_log(user_id: int, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM activity_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_activity(limit: int = 50, action_filter: Optional[str] = None) -> List[Dict]:
    with get_conn() as conn:
        if action_filter:
            rows = conn.execute(
                "SELECT al.*, u.username, u.first_name FROM activity_log al "
                "LEFT JOIN users u ON u.user_id=al.user_id "
                "WHERE al.action=? ORDER BY al.created_at DESC LIMIT ?",
                (action_filter, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT al.*, u.username, u.first_name FROM activity_log al "
                "LEFT JOIN users u ON u.user_id=al.user_id "
                "ORDER BY al.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# REPORTS
# ═════════════════════════════════════════════════════════════════════════════

def create_report(reporter_id: int, whisper_id: str, reason: str) -> int:
    """Create a report. Returns the report id."""
    from core.events import event_bus
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO reports (reporter_id, whisper_id, reason) VALUES (?,?,?)",
            (reporter_id, whisper_id, reason),
        )
        conn.commit()
        report_id = cur.lastrowid
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
                "WHERE r.status=? ORDER BY r.created_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT r.*, u.username reporter_name FROM reports r "
                "LEFT JOIN users u ON u.user_id=r.reporter_id "
                "ORDER BY r.created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]


def review_report(report_id: int, reviewed_by: int, new_status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE reports SET status=?, reviewed_by=?, reviewed_at=datetime('now') WHERE id=?",
            (new_status, reviewed_by, report_id),
        )
        conn.commit()


def count_reports(status: Optional[str] = "pending") -> int:
    with get_conn() as conn:
        if status:
            return conn.execute(
                "SELECT COUNT(*) FROM reports WHERE status=?", (status,)
            ).fetchone()[0]
        return conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# ADVANCED BAN SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

def ban_user_with_reason(user_id: int, reason: str, banned_by: int,
                         hours: Optional[int] = None) -> None:
    """Ban (permanent or temp) with reason. Logs to ban_log."""
    from database import ban_user
    from core.events import event_bus
    from core.logging_config import audit_log

    expires_at = None
    if hours:
        expires_at = (datetime.utcnow() + timedelta(hours=hours)).isoformat()
        # Write to temp_bans table
        with get_conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO temp_bans (user_id,reason,banned_by,expires_at)"
                " VALUES (?,?,?,?)",
                (user_id, reason, banned_by, expires_at),
            )
            conn.commit()
    else:
        ban_user(user_id)  # permanent via existing function

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO ban_log (user_id,action,reason,banned_by,expires_at)"
            " VALUES (?,?,?,?,?)",
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
        conn.execute("DELETE FROM temp_bans WHERE user_id=?", (user_id,))
        conn.execute(
            "INSERT INTO ban_log (user_id,action,reason,banned_by) VALUES (?,?,?,?)",
            (user_id, "unban", reason, unbanned_by),
        )
        conn.commit()
    audit_log("UNBAN", actor_id=unbanned_by, target=user_id, reason=reason)
    event_bus.fire(event_bus.ON_USER_UNBANNED, user_id=user_id)


def get_ban_log(user_id: Optional[int] = None, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        if user_id:
            rows = conn.execute(
                "SELECT * FROM ban_log WHERE user_id=? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM ban_log ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def expire_temp_bans() -> int:
    """Remove expired temp bans from temp_bans table. Returns count."""
    from database import unban_user
    now = datetime.utcnow().isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT user_id FROM temp_bans WHERE expires_at <= ?", (now,)
        ).fetchall()
        count = len(rows)
        for r in rows:
            unban_user(r["user_id"])
            conn.execute("DELETE FROM temp_bans WHERE user_id=?", (r["user_id"],))
        if count:
            conn.commit()
    return count


# ═════════════════════════════════════════════════════════════════════════════
# WHISPER FAVORITES & ARCHIVE
# ═════════════════════════════════════════════════════════════════════════════

def save_favorite(user_id: int, whisper_id: str) -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO whisper_favorites (user_id, whisper_id) VALUES (?,?)",
                (user_id, whisper_id),
            )
            conn.commit()
        return True
    except Exception:
        return False


def remove_favorite(user_id: int, whisper_id: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "DELETE FROM whisper_favorites WHERE user_id=? AND whisper_id=?",
            (user_id, whisper_id),
        )
        conn.commit()


def get_favorites(user_id: int) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT wf.whisper_id, wf.saved_at, w.content, w.whisper_type "
            "FROM whisper_favorites wf "
            "LEFT JOIN whispers w ON w.whisper_id=wf.whisper_id "
            "WHERE wf.user_id=? ORDER BY wf.saved_at DESC",
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]


def count_saved_whispers(user_id: int) -> int:
    with get_conn() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM whisper_favorites WHERE user_id=?", (user_id,)
        ).fetchone()[0]


def archive_whisper(whisper_id: str) -> bool:
    """Copy whisper to archive table and mark as archived. Keeps original intact."""
    from database import get_whisper
    w = get_whisper(whisper_id)
    if not w:
        return False
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO whisper_archive "
            "(whisper_id, sender_id, content, whisper_type, original_data)"
            " VALUES (?,?,?,?,?)",
            (whisper_id, w["sender_id"], w["content"], w["whisper_type"],
             json.dumps(dict(w))),
        )
        conn.execute(
            "UPDATE whispers SET is_archived=1 WHERE whisper_id=?", (whisper_id,)
        )
        conn.commit()
    return True


def get_archive(user_id: int, limit: int = 20) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whisper_archive WHERE sender_id=? ORDER BY archived_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# WHISPER SEARCH
# ═════════════════════════════════════════════════════════════════════════════

def search_whispers(user_id: int, query: str, limit: int = 10) -> List[Dict]:
    """Full-text search in whispers sent by user_id."""
    with get_conn() as conn:
        like = f"%{query}%"
        rows = conn.execute(
            "SELECT * FROM whispers WHERE sender_id=? AND content LIKE ? "
            "AND is_archived=0 ORDER BY created_at DESC LIMIT ?",
            (user_id, like, limit),
        ).fetchall()
        return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# WHISPER REPLIES / THREADS  (delegated to database.replies)
# ═════════════════════════════════════════════════════════════════════════════

def create_reply(parent_id: str, sender_id: int, content: str,
                 auto_delete_hours: int = 0) -> str:
    """
    Create a reply attached to parent_id (whisper_id).

    Delegates to database.replies.create_reply which owns the canonical schema.
    The auto_delete_hours parameter is accepted for backward compatibility
    but not applied to replies (replies follow the parent whisper's lifecycle).

    Returns the reply_id string on success, or None on failure.
    """
    from database.replies import create_reply as _create_reply
    return _create_reply(
        whisper_id=parent_id,
        sender_id=sender_id,
        content=content,
    )


def get_thread(whisper_id: str) -> List[Dict]:
    """Return all replies to a whisper, oldest first."""
    from database.replies import get_replies as _get_replies
    rows = _get_replies(whisper_id)
    return [dict(r) for r in rows]


# ═════════════════════════════════════════════════════════════════════════════
# SELF-DESTRUCT WHISPERS
# ═════════════════════════════════════════════════════════════════════════════

def set_self_destruct(whisper_id: str, after_reads: int = 1,
                      after_hours: int = 0) -> None:
    destruct_on = None
    if after_hours > 0:
        destruct_on = (datetime.utcnow() + timedelta(hours=after_hours)).isoformat()
    # When after_hours == 0, destruct_on stays NULL; destruction is read-count only.

    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO whisper_destruct (whisper_id, destruct_on, after_reads)"
            " VALUES (?,?,?)",
            (whisper_id, destruct_on, after_reads),
        )
        conn.execute(
            "UPDATE whispers SET is_self_destruct=1 WHERE whisper_id=?", (whisper_id,)
        )
        conn.commit()


def check_self_destruct(whisper_id: str, current_read_count: int) -> bool:
    """Returns True if the whisper should be destroyed now."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_destruct WHERE whisper_id=?", (whisper_id,)
        ).fetchone()
        if not row:
            return False
        # Read-count trigger: destroy when reads reach the threshold
        if row["after_reads"] > 0 and current_read_count >= row["after_reads"]:
            return True
        # Time trigger: only fires when destruct_on is explicitly set (not NULL)
        if row["destruct_on"]:
            now = datetime.utcnow().isoformat()
            if now >= row["destruct_on"]:
                return True
    return False


# ═════════════════════════════════════════════════════════════════════════════
# STATISTICS SNAPSHOTS
# ═════════════════════════════════════════════════════════════════════════════

def snapshot_stats(period_type: str = "daily") -> None:
    """Take a statistics snapshot for the given period_type (daily/weekly/monthly/yearly)."""
    from database import get_stats
    label_map = {
        "daily":   datetime.utcnow().strftime("%Y-%m-%d"),
        "weekly":  datetime.utcnow().strftime("%Y-W%W"),
        "monthly": datetime.utcnow().strftime("%Y-%m"),
        "yearly":  datetime.utcnow().strftime("%Y"),
    }
    label = label_map.get(period_type, datetime.utcnow().isoformat())
    data  = get_stats()
    # Add enterprise stats
    with get_conn() as conn:
        data["total_reports"] = conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0]
        data["open_reports"]  = conn.execute(
            "SELECT COUNT(*) FROM reports WHERE status='pending'"
        ).fetchone()[0]

    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO stats_snapshots (period_type, period_label, data_json)"
            " VALUES (?,?,?)",
            (period_type, label, json.dumps(data)),
        )
        conn.commit()
    logger.info(f"Stats snapshot created: {period_type}/{label}")


def get_snapshots(period_type: str, limit: int = 30) -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM stats_snapshots WHERE period_type=? "
            "ORDER BY period_label DESC LIMIT ?",
            (period_type, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["data"] = json.loads(d["data_json"])
            result.append(d)
        return result


def get_active_users(days: int = 7) -> int:
    """Users who sent a whisper or read one in the last N days."""
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        senders = conn.execute(
            "SELECT COUNT(DISTINCT sender_id) FROM whispers WHERE created_at >= ?",
            (since,),
        ).fetchone()[0]
        readers = conn.execute(
            "SELECT COUNT(DISTINCT user_id) FROM whisper_readers WHERE read_at >= ?",
            (since,),
        ).fetchone()[0]
        # UNION approach for unique active users
        return conn.execute(
            "SELECT COUNT(*) FROM ("
            "  SELECT sender_id as uid FROM whispers WHERE created_at >= ?"
            "  UNION"
            "  SELECT user_id FROM whisper_readers WHERE read_at >= ?"
            ")",
            (since, since),
        ).fetchone()[0]


# ═════════════════════════════════════════════════════════════════════════════
# BACKUP SYSTEM
# ═════════════════════════════════════════════════════════════════════════════

BACKUPS_DIR = Path(__file__).parent.parent / "backups"
BACKUPS_DIR.mkdir(exist_ok=True)
MAX_BACKUPS = 10


def create_backup(created_by: Optional[int] = None, notes: str = "") -> str:
    """Copy the live SQLite DB to backups/ with a timestamped name. Returns filename."""
    from core.events import event_bus
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"whispers_backup_{ts}.db"
    dest = BACKUPS_DIR / filename
    shutil.copy2(DATABASE_PATH, dest)
    size = dest.stat().st_size

    with get_conn() as conn:
        conn.execute(
            "INSERT INTO backup_registry (filename, size_bytes, created_by, notes)"
            " VALUES (?,?,?,?)",
            (filename, size, created_by, notes),
        )
        conn.commit()

    _cleanup_old_backups()
    event_bus.fire(event_bus.ON_BACKUP_CREATED, filename=filename, size=size)
    logger.info(f"Backup created: {filename} ({size} bytes)")
    return filename


def restore_backup(filename: str) -> bool:
    """Restore from a named backup file. Returns True on success."""
    src = BACKUPS_DIR / filename
    if not src.exists():
        logger.error(f"Restore failed: {filename} not found")
        return False
    # Backup the current DB first
    create_backup(notes="pre-restore auto-backup")
    shutil.copy2(src, DATABASE_PATH)
    logger.info(f"Restored from backup: {filename}")
    return True


def list_backups() -> List[Dict]:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM backup_registry ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]


def _cleanup_old_backups() -> None:
    """Keep only MAX_BACKUPS most recent backups."""
    files = sorted(BACKUPS_DIR.glob("whispers_backup_*.db"), reverse=True)
    for old in files[MAX_BACKUPS:]:
        try:
            old.unlink()
            with get_conn() as conn:
                conn.execute(
                    "DELETE FROM backup_registry WHERE filename=?", (old.name,)
                )
                conn.commit()
            logger.info(f"Removed old backup: {old.name}")
        except Exception as exc:
            logger.warning(f"Could not remove old backup {old.name}: {exc}")


# ═════════════════════════════════════════════════════════════════════════════
# DATABASE ABSTRACTION LAYER (future PostgreSQL support)
# ═════════════════════════════════════════════════════════════════════════════

class DatabaseAdapter:
    """
    Thin abstraction layer.  Currently wraps SQLite.
    Future: swap out `_execute` / `_fetchone` / `_fetchall` for psycopg2/asyncpg.
    """

    def execute(self, sql: str, params: tuple = ()) -> None:
        with get_conn() as conn:
            conn.execute(sql, params)
            conn.commit()

    def fetchone(self, sql: str, params: tuple = ()) -> Optional[Dict]:
        with get_conn() as conn:
            row = conn.execute(sql, params).fetchone()
            return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> List[Dict]:
        with get_conn() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]


db = DatabaseAdapter()
