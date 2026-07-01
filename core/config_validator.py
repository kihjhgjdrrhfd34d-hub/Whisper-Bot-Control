"""
core/config_validator.py — Validate configuration on startup.
Raises ConfigError with a clear message if anything critical is missing.
"""
from __future__ import annotations

import logging
import os
from typing import List

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """Raised when the configuration is invalid."""


def validate_config() -> List[str]:
    """
    Validate the runtime configuration.
    Returns a list of warning strings (non-fatal issues).
    Raises ConfigError on fatal issues.
    """
    warnings: List[str] = []

    # ── BOT_TOKEN ─────────────────────────────────────────────────────────────
    token = os.getenv("BOT_TOKEN", "")
    if not token or token.strip() == "":
        raise ConfigError(
            "BOT_TOKEN is not set.\n"
            "  • Local / Termux: create a .env file with BOT_TOKEN=your_token\n"
            "  • Render / cloud: set the BOT_TOKEN environment variable."
        )
    if ":" not in token:
        raise ConfigError("BOT_TOKEN format looks invalid (expected 'id:hash').")

    # ── ADMIN_IDS ─────────────────────────────────────────────────────────────
    raw_admins = os.getenv("ADMIN_IDS", "0")
    admin_ids = [x.strip() for x in raw_admins.split(",") if x.strip().isdigit()]
    if not admin_ids or admin_ids == ["0"]:
        warnings.append(
            "ADMIN_IDS is not set or is '0'.  No admins will have access to the admin panel."
        )

    # ── DATABASE_PATH ─────────────────────────────────────────────────────────
    db_path = os.getenv("DATABASE_PATH", "whispers.db")
    db_dir = os.path.dirname(os.path.abspath(db_path))
    if not os.path.isdir(db_dir):
        warnings.append(f"DATABASE_PATH directory does not exist: {db_dir}")

    # ── KEEP_ALIVE_PORT ───────────────────────────────────────────────────────
    port_str = os.getenv("KEEP_ALIVE_PORT", "8080")
    try:
        port = int(port_str)
        if not (1 <= port <= 65535):
            warnings.append(f"KEEP_ALIVE_PORT={port} is out of range [1-65535].")
    except ValueError:
        warnings.append(f"KEEP_ALIVE_PORT='{port_str}' is not a valid integer.")

    for w in warnings:
        logger.warning(f"Config warning: {w}")

    return warnings


def validate_and_log() -> None:
    """Call at startup.  Logs warnings, raises ConfigError on fatal issues."""
    try:
        warnings = validate_config()
        if warnings:
            logger.warning(f"Config validated with {len(warnings)} warning(s).")
        else:
            logger.info("Config validated OK.")
    except ConfigError as exc:
        logger.critical(f"FATAL CONFIG ERROR: {exc}")
        raise
