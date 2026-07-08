"""
core/rate_limiter.py — In-memory rate limiting, flood protection, spam detection.

All limits are configurable.  Bans imposed here are temporary (stored in memory);
the enterprise/protection.py layer writes permanent bans to the database.
"""
from __future__ import annotations

import time
import logging
from collections import defaultdict, deque
from threading import Lock
from typing import Dict, Deque, Optional, Tuple

logger = logging.getLogger(__name__)


# ── Configurable defaults ─────────────────────────────────────────────────────

# (max_calls, window_seconds)
DEFAULT_LIMITS = {
    "message":       (10, 10),   # 10 msgs per 10 s
    "inline_query":  (20, 10),   # 20 inline queries per 10 s
    "callback":      (15, 10),   # 15 button taps per 10 s
    "whisper_create":(5,  60),   # 5 whisper creations per minute
}

TEMP_BAN_SECONDS = 300           # 5-minute temp ban on flood violation
SPAM_SCORE_THRESHOLD = 50        # score ≥ this → flag as spammer


class RateLimiter:
    """Sliding-window rate limiter with per-user temporary bans."""

    def __init__(self) -> None:
        self._lock = Lock()
        # user_id → action → deque of timestamps
        self._calls: Dict[int, Dict[str, Deque[float]]] = defaultdict(
            lambda: defaultdict(deque)
        )
        # user_id → ban_expires_at
        self._temp_bans: Dict[int, float] = {}
        # user_id → spam score
        self._spam_scores: Dict[int, int] = defaultdict(int)

    # ── Public API ────────────────────────────────────────────────────────────

    def check(self, user_id: int, action: str = "message") -> Tuple[bool, str]:
        """
        Returns (allowed, reason).
        allowed=True  → request may proceed.
        allowed=False → request should be silently dropped or warned.
        """
        with self._lock:
            # 1. Temp ban check
            expires = self._temp_bans.get(user_id, 0.0)
            if expires > time.monotonic():
                remaining = int(expires - time.monotonic())
                return False, f"temp_banned:{remaining}"

            # 2. Sliding-window check
            max_calls, window = DEFAULT_LIMITS.get(action, (30, 10))
            now = time.monotonic()
            q = self._calls[user_id][action]

            # Drop timestamps outside the window
            while q and q[0] < now - window:
                q.popleft()

            if len(q) >= max_calls:
                # Issue temp ban
                self._temp_bans[user_id] = now + TEMP_BAN_SECONDS
                self._spam_scores[user_id] += 10
                logger.warning(
                    f"RateLimiter: flood detected uid={user_id} action={action} "
                    f"→ temp-banned {TEMP_BAN_SECONDS}s"
                )
                return False, "flood"

            q.append(now)
            return True, "ok"

    def add_spam_score(self, user_id: int, points: int = 5) -> int:
        with self._lock:
            self._spam_scores[user_id] += points
            return self._spam_scores[user_id]

    def get_spam_score(self, user_id: int) -> int:
        return self._spam_scores.get(user_id, 0)

    def is_spammer(self, user_id: int) -> bool:
        return self._spam_scores.get(user_id, 0) >= SPAM_SCORE_THRESHOLD

    def clear_user(self, user_id: int) -> None:
        """Reset all rate-limit data for a user (e.g. after admin unban)."""
        with self._lock:
            self._calls.pop(user_id, None)
            self._temp_bans.pop(user_id, None)
            self._spam_scores[user_id] = 0

    def temp_ban_remaining(self, user_id: int) -> int:
        expires = self._temp_bans.get(user_id, 0.0)
        remaining = expires - time.monotonic()
        return max(0, int(remaining))

    def active_temp_bans(self) -> Dict[int, int]:
        now = time.monotonic()
        return {
            uid: int(exp - now)
            for uid, exp in self._temp_bans.items()
            if exp > now
        }

    def top_spammers(self, n: int = 10) -> list:
        return sorted(
            self._spam_scores.items(), key=lambda x: x[1], reverse=True
        )[:n]


# Singleton
rate_limiter = RateLimiter()
