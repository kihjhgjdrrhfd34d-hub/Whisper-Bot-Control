"""
enterprise/protection.py — Anti-spam, flood protection, suspicious account detection.
Integrates core/rate_limiter.py with the database ban system.
"""
from __future__ import annotations

import logging
from typing import Optional, Tuple

from core.rate_limiter import rate_limiter, SPAM_SCORE_THRESHOLD
from core.events import event_bus

logger = logging.getLogger(__name__)


def check_user(user_id: int, action: str = "message") -> Tuple[bool, str]:
    """
    Unified entry point.  Call before processing any user request.
    Returns (allowed, reason).
    """
    # 1. Database-level permanent ban check (fast path via existing function)
    from database import is_banned
    if is_banned(user_id):
        return False, "permanently_banned"

    # 2. Rate-limit / flood check
    allowed, reason = rate_limiter.check(user_id, action)
    if not allowed:
        # Auto-escalate repeat spammers to DB temp-ban
        score = rate_limiter.get_spam_score(user_id)
        if score >= SPAM_SCORE_THRESHOLD:
            _escalate_to_db_temp_ban(user_id, score)
            event_bus.fire(event_bus.ON_SPAM_DETECTED, user_id=user_id, score=score)
        return False, reason

    return True, "ok"


def _escalate_to_db_temp_ban(user_id: int, score: int) -> None:
    """Write a 24-hour temp ban to the DB for chronic spammers."""
    try:
        from enterprise.db_enterprise import ban_user_with_reason
        ban_user_with_reason(
            user_id=user_id,
            reason=f"Auto spam detection (score={score})",
            banned_by=0,   # 0 = system
            hours=24,
        )
        logger.warning(f"Protection: auto-temp-banned user {user_id} (spam score={score})")
    except Exception as exc:
        logger.error(f"Protection: failed to escalate ban for {user_id}: {exc}")


def on_user_action(user_id: int, action: str) -> None:
    """Record an action in the rate limiter. Call for lightweight tracking."""
    rate_limiter.check(user_id, action)


def clear_user_restrictions(user_id: int) -> None:
    """Remove all in-memory rate-limit data (called after admin unban)."""
    rate_limiter.clear_user(user_id)


def get_protection_status(user_id: int) -> dict:
    return {
        "spam_score":        rate_limiter.get_spam_score(user_id),
        "is_spammer":        rate_limiter.is_spammer(user_id),
        "temp_ban_remaining": rate_limiter.temp_ban_remaining(user_id),
    }
