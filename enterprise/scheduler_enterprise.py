"""
enterprise/scheduler_enterprise.py
────────────────────────────────────
Extends the existing scheduler with enterprise tasks:
  • Daily stats snapshots
  • Scheduled backups
  • Temp-ban expiry
  • Achievement checks

Does NOT modify scheduler.py.  Called from main.py AFTER start_scheduler().
"""
from __future__ import annotations

import logging
import time
from threading import Thread

logger = logging.getLogger(__name__)

_enterprise_thread: Thread | None = None


def _enterprise_tick() -> None:
    """Run once per hour: expire temp bans, take daily snapshot."""
    try:
        from enterprise.db_enterprise import expire_temp_bans, snapshot_stats
        expired = expire_temp_bans()
        if expired:
            logger.info(f"Enterprise scheduler: expired {expired} temp ban(s).")
        snapshot_stats("daily")
    except Exception as exc:
        logger.error(f"Enterprise scheduler tick error: {exc}", exc_info=True)


def _run_enterprise_scheduler(interval_seconds: int) -> None:
    while True:
        time.sleep(interval_seconds)
        _enterprise_tick()


def start_enterprise_scheduler(interval_hours: int = 1) -> None:
    """Start the enterprise background scheduler (once only)."""
    global _enterprise_thread
    if _enterprise_thread and _enterprise_thread.is_alive():
        return
    secs = interval_hours * 3600
    logger.info(f"Enterprise scheduler starting (every {interval_hours}h).")
    _enterprise_thread = Thread(
        target=_run_enterprise_scheduler,
        args=(secs,),
        daemon=True,
        name="EnterpriseScheduler",
    )
    _enterprise_thread.start()
