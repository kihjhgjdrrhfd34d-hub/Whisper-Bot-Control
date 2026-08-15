"""
handlers/_formatting.py — Shared formatting helpers for handlers.

Consolidated from duplicate copies found in handlers/replies.py and
handlers/dashboard.py to reduce code duplication.
"""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from database import get_user

# Sana'a / Yemen display timezone.  Internal storage stays UTC; this is used
# only to render stored UTC timestamps to the user in Asia/Aden (UTC+03:00).
_ADEN = ZoneInfo("Asia/Aden")


def _as_aden(datetime_value) -> datetime:
    """Convert a UTC datetime (aware or naive-UTC) to Asia/Aden."""
    if datetime_value.tzinfo is None:
        datetime_value = datetime_value.replace(tzinfo=timezone.utc)
    return datetime_value.astimezone(_ADEN)


def format_display_time(value, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Format a stored UTC timestamp string for display in Asia/Aden.

    Accepts SQLite "YYYY-MM-DD HH:MM:SS" strings and PostgreSQL
    "YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00" isoformat strings.  Never changes
    how the timestamp is stored — conversion is display-only.
    """
    if not value:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    normalized = text.replace("T", " ")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        try:
            dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return text
    return _as_aden(dt).strftime(fmt)



def _fmt_username(username: str) -> str:
    return f"@{username.replace('_', '\\_')}"


def build_share_link(bot_username: str, whisper_id: str) -> str:
    import urllib.parse
    whisper_link = f"https://t.me/{bot_username}?start={whisper_id}"
    return f"https://t.me/share/url?url={urllib.parse.quote(whisper_link, safe='')}"


def _get_sender_display(user_id: int) -> str:
    u = get_user(user_id)
    if not u:
        return f"المُستخدم {user_id}"
    name = u["first_name"] or f"المُستخدم {user_id}"
    if u["username"]:
        return f"{name} ({_fmt_username(u['username'])})"
    return name
