"""
services/whisper_service.py — Business logic for whisper operations.

This layer sits between handlers and the database, encapsulating
pure-data logic with no dependency on TeleBot or Telegram APIs.

Handlers call these functions to perform business operations,
then use TeleBot methods (send_message, answer_callback_query, etc.)
for the Telegram interaction layer.
"""

from __future__ import annotations

from telebot.util import escape
from database import (
    get_whisper, can_read_whisper, record_whisper_read, reader_count,
    get_readers, get_curious_ones, upsert_user, get_setting, is_banned,
    add_curious,
)


# ── Callback data parsing ────────────────────────────────────────────

def parse_whisper_id(call_data: str) -> str:
    """Extract whisper ID from callback data like 'read:<id>'."""
    return call_data.split(":", 1)[1]


# ── Whisper state queries ────────────────────────────────────────────

def is_destructive_whisper(w: dict) -> bool:
    """Check if a whisper is marked as destructive (self-destruct on read)."""
    return bool(dict(w).get("is_destructive", 0))


def is_own_whisper(user_id: int, w: dict) -> bool:
    """Check if the given user is the sender of the whisper."""
    return user_id == w["sender_id"]


def get_whisper_locked_state(w: dict) -> bool:
    """Check if a whisper is currently locked."""
    return bool(w.get("is_locked"))


# ── User registration ────────────────────────────────────────────────

def ensure_user(user_id: int, username: str | None,
                first_name: str | None, last_name: str | None) -> None:
    """Upsert user record — silently ignore errors."""
    try:
        upsert_user(user_id, username, first_name, last_name)
    except Exception:
        pass


# ── Read recording ───────────────────────────────────────────────────

def record_read_and_check(whisper_id: str, user_id: int, display_name: str = "") -> tuple:
    """Record a read and determine if it's the reader's first and the first-ever.

    Returns:
        (is_new_read: bool, is_first_ever: bool)
            - is_new_read: True if this user hasn't read it before
            - is_first_ever: True if this is the very first read across all users
    """
    is_new_read = record_whisper_read(whisper_id, user_id)
    is_first_ever = (reader_count(whisper_id) == 1) if is_new_read else False
    return is_new_read, is_first_ever


# ── Display name helpers ─────────────────────────────────────────────

def get_reader_display_name(reader: dict) -> str:
    """Get a display label for a reader (prefers first_name, falls back to @username)."""
    return reader.get("first_name") or (f"@{reader['username']}" if reader.get("username") else "مستخدم مجهول")


def get_opener_name(whisper_id: str) -> str:
    """Get display name of the first reader of a whisper."""
    readers = get_readers(whisper_id)
    if readers:
        return get_reader_display_name(readers[0])
    return "شخص آخر"


def get_user_display(user) -> str:
    """Get a display string for a Telegram user (prefers first_name, falls back to @username)."""
    return user.first_name or (f"@{user.username}" if user.username else "شخص")


# ── Message builders (pure data → string) ────────────────────────────

def build_first_one_notification(user, w: dict) -> str:
    """Build the detailed HTML notification for a first_one read."""
    username_display = f"@{escape(user.username)}" if user.username else "لا يوجد"
    name_display = escape(user.first_name) if user.first_name else "مستخدم مجهول"
    content_escaped = escape(w["content"])
    return (
        "━━━━━━━━━━━━━━━━━━━━\n"
        "👁️ تمت مشاهدة هذه الهمسة\n\n"
        "👤 معرف المستخدم:\n"
        f"{username_display}\n\n"
        "🪪 الاسم:\n"
        f"{name_display}\n\n"
        "🆔 الآيدي:\n"
        f"{user.id}\n\n"
        "💬 الهمسة:\n"
        f"{content_escaped}\n\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )


def build_read_receipt_message(user) -> str:
    """Build a simple read receipt message."""
    display = get_user_display(user)
    return f"👁 قرأ {display} همستك!"


def build_destructive_receipt_message(user) -> str:
    """Build a read receipt for destructive whispers."""
    display = get_user_display(user)
    return f"👁 قرأ {display} همستك التدميرية!"


def build_public_whisper_notification(user, w: dict) -> str:
    """Build a DM notification when a public (everyone) whisper is first read."""
    from datetime import datetime, timezone
    display = get_user_display(user)
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return (
        "👁️ تم فتح همستك العامة\n\n"
        "قرأها:\n"
        f"• {display}\n"
        f"المعرف: {user.id}\n"
        f"الوقت: {timestamp}"
    )


def build_curious_report_lines(curious: list, readers: list) -> list:
    """Build Markdown-formatted lines for the curious-ones report."""
    lines = [
        f"👀 *الأشخاص الذين حاولوا فتح الهمسة*\n"
        f"({len(curious)} شخص)\n"
    ]
    for i, row in enumerate(curious, 1):
        name = row["first_name"] or "مجهول"
        uname = f"@{row['username']}" if row["username"] else "—"
        uid = row["user_id"]
        tried_at = str(row["tried_at"])[:16] if row["tried_at"] else "—"
        lines.append(
            f"{i}. *{name}*\n"
            f"   🔗 اليوزر: {uname}\n"
            f"   🆔 الآيدي: `{uid}`\n"
            f"   🕐 الوقت: `{tried_at}`\n"
        )
    lines.append(f"👁 قرأها فعلاً: {len(readers)} شخص")
    return lines


# ── Notification type determination ──────────────────────────────────

def determine_notification_type(w: dict, is_first_ever: bool) -> str | None:
    """Determine what notification to send to the sender after a read.

    Returns:
        'detailed' — first_one with detailed HTML message
        'simple'   — other types when read_receipt_enabled
        None       — no notification needed
    """
    if get_setting("read_receipt_enabled") != "1":
        return None
    if w["whisper_type"] == "first_one" and is_first_ever:
        return "detailed"
    return "simple"
