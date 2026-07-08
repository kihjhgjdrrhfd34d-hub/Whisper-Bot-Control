"""
core/logging_config.py — Structured, rotating log setup with separate audit log.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


LOGS_DIR = Path(__file__).parent.parent / "logs"
LOGS_DIR.mkdir(exist_ok=True)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with rotating file handler + console handler."""
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    fmt = "%(asctime)s [%(levelname)-8s] %(name)s: %(message)s"
    datefmt = "%Y-%m-%d %H:%M:%S"

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # ── Console handler ───────────────────────────────────────────────────────
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(console)

    # ── Rotating file handler (10 MB × 5 files) ───────────────────────────────
    fh = logging.handlers.RotatingFileHandler(
        LOGS_DIR / "whisper_bot.log",
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    root.addHandler(fh)

    # ── Separate audit log (never rotated automatically, append only) ─────────
    audit_fh = logging.FileHandler(
        LOGS_DIR / "audit.log", encoding="utf-8"
    )
    audit_fh.setFormatter(logging.Formatter(fmt, datefmt=datefmt))
    audit_logger = logging.getLogger("audit")
    audit_logger.addHandler(audit_fh)
    audit_logger.propagate = False


# ── Convenience audit-log helper ─────────────────────────────────────────────

_audit = logging.getLogger("audit")


def audit_log(action: str, actor_id: int | None = None, **details) -> None:
    """Write a structured audit-log entry."""
    parts = [f"ACTION={action}"]
    if actor_id is not None:
        parts.append(f"actor={actor_id}")
    for k, v in details.items():
        parts.append(f"{k}={v!r}")
    _audit.info(" | ".join(parts))
