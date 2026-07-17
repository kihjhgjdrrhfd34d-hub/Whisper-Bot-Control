"""
handlers/_formatting.py — Shared formatting helpers for handlers.

Consolidated from duplicate copies found in handlers/replies.py and
handlers/dashboard.py to reduce code duplication.
"""
from __future__ import annotations

from database import get_user


def _fmt_username(username: str) -> str:
    return f"@{username.replace('_', '\\_')}"


def _get_sender_display(user_id: int) -> str:
    u = get_user(user_id)
    if not u:
        return f"المُستخدم {user_id}"
    name = u["first_name"] or f"المُستخدم {user_id}"
    if u["username"]:
        return f"{name} ({_fmt_username(u['username'])})"
    return name
