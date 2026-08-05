"""
tests/conftest.py
Global test isolation:
  * Points DATABASE_PATH at a fresh temp SQLite file (never whispers.db).
  * Loads a hermetic token/admin env so config.py does not read production
    secrets from .env during tests.
  * Wipes all tables between test modules so modules cannot contaminate one
    another through the shared database file.

The env vars are set at import time.  pytest imports this file before any test
module, so the first `config` / `database` import inside the suite already picks
up the temp path.  database.get_conn() keeps its exact API.
"""
import os
import sys
import tempfile

import pytest

# ── Hermetic environment (runs before any test module import) ────────────────
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ["BOT_TOKEN"] = "0:test_token_placeholder"
os.environ["ADMIN_IDS"] = "999"

_fd, _DB_PATH = tempfile.mkstemp(prefix="whisper_pytest_", suffix=".db")
os.close(_fd)
os.environ["DATABASE_PATH"] = _DB_PATH


@pytest.fixture(scope="session", autouse=True)
def _session_cleanup():
    # Rebind DATABASE_PATH on every already-imported module (database,
    # db_enterprise, envelope, config_validator, ...) so the whole session uses
    # the same isolated temp DB regardless of import order.  get_conn() resolves
    # the name at call time, so rebinding is enough — the public API is unchanged.
    import sys as _sys

    for _mod in list(_sys.modules.values()):
        if getattr(_mod, "DATABASE_PATH", None) is not None:
            _mod.DATABASE_PATH = _DB_PATH
    yield
    try:
        os.unlink(_DB_PATH)
    except OSError:
        pass


def _wipe_all_tables() -> None:
    """Delete every row from every existing table (FK checks off)."""
    import database as db

    with db.get_conn() as conn:
        conn.execute("PRAGMA foreign_keys=OFF")
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        for t in tables:
            conn.execute('DELETE FROM "%s"' % t)
        try:
            conn.execute("DELETE FROM sqlite_sequence")
        except Exception:
            pass
        conn.execute("PRAGMA foreign_keys=ON")
        conn.commit()


@pytest.fixture(scope="module", autouse=True)
def _isolated_db_module():
    yield
    _wipe_all_tables()
