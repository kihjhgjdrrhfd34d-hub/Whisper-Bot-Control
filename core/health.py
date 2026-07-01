"""
core/health.py — Health check and monitoring hooks.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any

logger = logging.getLogger(__name__)

_start_time = time.monotonic()
_start_wall  = datetime.now(timezone.utc)


def uptime_seconds() -> float:
    return time.monotonic() - _start_time


def uptime_str() -> str:
    secs = int(uptime_seconds())
    h, rem = divmod(secs, 3600)
    m, s   = divmod(rem, 60)
    return f"{h}h {m}m {s}s"


def health_check() -> Dict[str, Any]:
    """Return a dict suitable for /health JSON endpoint."""
    status = "ok"
    issues = []

    # DB connectivity
    try:
        from database import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
        status = "degraded"
        issues.append("database")

    # Scheduler alive
    try:
        from scheduler import _scheduler_thread
        sched_alive = bool(_scheduler_thread and _scheduler_thread.is_alive())
    except Exception:
        sched_alive = False

    if not sched_alive:
        issues.append("scheduler_thread_dead")

    return {
        "status":     status,
        "uptime":     uptime_str(),
        "started_at": _start_wall.isoformat(),
        "database":   db_status,
        "scheduler":  "alive" if sched_alive else "dead",
        "issues":     issues,
    }


def metrics() -> Dict[str, Any]:
    """Extended runtime metrics for monitoring dashboards."""
    from core.rate_limiter import rate_limiter
    try:
        from database import get_stats
        stats = dict(get_stats())
    except Exception:
        stats = {}

    return {
        "uptime_seconds":  uptime_seconds(),
        "temp_bans_active": len(rate_limiter.active_temp_bans()),
        "top_spammers":    rate_limiter.top_spammers(5),
        **stats,
    }
