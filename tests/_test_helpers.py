"""
tests/_test_helpers.py
Shared helpers for test isolation.
"""
import os
import sys
import tempfile

# Ensure project root is on the path
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Set a placeholder token so config.py doesn't crash on import
os.environ.setdefault("BOT_TOKEN", "0:test_token_placeholder")
os.environ.setdefault("ADMIN_IDS", "999")


def make_isolated_db() -> str:
    """Create a fresh temp DB file and point DATABASE_PATH at it."""
    fd, path = tempfile.mkstemp(suffix=".db", prefix="whisper_test_")
    os.close(fd)
    os.environ["DATABASE_PATH"] = path
    # Force database module to reload its path
    import importlib
    import config as cfg
    cfg.DATABASE_PATH = path
    import database as db
    importlib.reload(db)
    return path


def boot_db(enterprise: bool = False) -> None:
    """Initialise DB (+ enterprise tables if requested)."""
    import database as db
    db.init_db()
    if enterprise:
        from enterprise import db_enterprise as edb
        edb.init_enterprise_db()


def cleanup_db(path: str) -> None:
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
