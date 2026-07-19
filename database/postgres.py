"""
database/postgres.py — PostgreSQL connection & utilities for the adapter layer.

Provides a psycopg2 connection wrapper and a helper that returns the
DATABASE_URL from config, or None if PostgreSQL is not configured.
"""

import os
import re
import logging
from contextlib import contextmanager
from typing import Optional

import psycopg2
import psycopg2.extras
from config import DATABASE_URL

logger = logging.getLogger(__name__)

# ── Detect if PostgreSQL mode should be active ──────────────────────────
USE_POSTGRES = bool(DATABASE_URL.strip())


def _parse_url() -> str:
    return DATABASE_URL.strip()


def pg_conn_str() -> Optional[str]:
    if USE_POSTGRES:
        return _parse_url()
    return None


# ── Connection wrapper ──────────────────────────────────────────────────

class PgConnection:
    """
    Thin wrapper around a psycopg2 connection that adds sqlite3-like
    convenience methods: .execute(), .executescript(), rowcount, lastrowid.
    """

    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    @staticmethod
    def connect(dsn: str) -> "PgConnection":
        conn = psycopg2.connect(dsn)
        conn.autocommit = False
        return PgConnection(conn)

    def _get_cursor(self):
        return self._conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, sql: str, params=None):
        cur = self._get_cursor()
        if params is not None:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        self._last_cursor = cur
        return cur

    def executescript(self, script: str):
        """Run multiple SQL statements separated by semicolons."""
        statements = self._split_sql_script(script)
        for stmt in statements:
            stripped = stmt.strip()
            if stripped:
                self.execute(stripped)

    def commit(self):
        self._conn.commit()

    def close(self):
        self._conn.close()

    def rollback(self):
        self._conn.rollback()

    @property
    def lastrowid(self):
        if self._last_cursor is not None:
            return self._last_cursor.fetchone()[0] if self._last_cursor.description else None
        return None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            try:
                self._conn.rollback()
            except Exception:
                pass
        else:
            try:
                self._conn.commit()
            except Exception:
                pass
        self.close()

    # ── helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _split_sql_script(script: str):
        """Split a SQL script into individual statements, respecting $$ and strings."""
        statements = []
        current = []
        in_string = False
        string_char = None
        in_dollar_tag = False
        dollar_tag = None
        i = 0
        while i < len(script):
            ch = script[i]
            if in_dollar_tag:
                current.append(ch)
                if script[i:].startswith(dollar_tag):
                    current.append(dollar_tag)
                    i += len(dollar_tag)
                    in_dollar_tag = False
                    continue
            elif in_string:
                current.append(ch)
                if ch == string_char and (i == 0 or script[i - 1] != '\\'):
                    in_string = False
            elif ch == "'":
                current.append(ch)
                in_string = True
                string_char = "'"
            elif ch == '$':
                # check for dollar-quoting
                match = re.match(r'\$([^$]*)\$', script[i:])
                if match:
                    current.append(match.group(0))
                    dollar_tag = match.group(0)
                    i += len(dollar_tag)
                    in_dollar_tag = True
                    continue
                else:
                    current.append(ch)
            elif ch == ';' and not in_string:
                current.append(ch)
                stmt = ''.join(current).strip()
                if stmt:
                    statements.append(stmt)
                current = []
            else:
                current.append(ch)
            i += 1
        remaining = ''.join(current).strip()
        if remaining:
            statements.append(remaining)
        return statements


def get_conn():
    """Return a PgConnection. Raises RuntimeError if DATABASE_URL is not set."""
    if not USE_POSTGRES:
        raise RuntimeError("PostgreSQL not configured: DATABASE_URL is empty")
    dsn = _parse_url()
    try:
        return PgConnection.connect(dsn)
    except Exception as exc:
        logger.error(f"PostgreSQL connection failed: {exc}")
        raise
