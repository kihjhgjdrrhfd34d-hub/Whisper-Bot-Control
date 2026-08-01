import json
import logging
from typing import Optional

from database.postgres import get_conn

logger = logging.getLogger(__name__)


def init_whisper_conditions_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_conditions (
                id              SERIAL PRIMARY KEY,
                whisper_id      TEXT NOT NULL,
                condition_name  TEXT NOT NULL,
                config          TEXT NOT NULL DEFAULT '{}',
                created_at      TEXT DEFAULT (NOW()),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE TABLE IF NOT EXISTS condition_attempts (
                id              SERIAL PRIMARY KEY,
                whisper_id      TEXT NOT NULL,
                user_id         BIGINT NOT NULL,
                condition_name  TEXT NOT NULL,
                passed          INTEGER NOT NULL DEFAULT 0,
                attempt_data    TEXT,
                created_at      TEXT DEFAULT (NOW()),
                FOREIGN KEY (whisper_id) REFERENCES whispers(whisper_id)
            );

            CREATE INDEX IF NOT EXISTS idx_wc_whisper
                ON whisper_conditions(whisper_id);
            CREATE INDEX IF NOT EXISTS idx_ca_whisper
                ON condition_attempts(whisper_id);
            CREATE INDEX IF NOT EXISTS idx_ca_user
                ON condition_attempts(user_id);
        """)
        conn.commit()


def add_whisper_condition(whisper_id: str, condition_name: str, config: Optional[dict] = None) -> int:
    config_json = json.dumps(config or {})
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO whisper_conditions (whisper_id, condition_name, config)"
            " VALUES (%s, %s, %s) RETURNING id",
            (whisper_id, condition_name, config_json),
        )
        conn.commit()
        return cur.fetchone()["id"]


def get_whisper_conditions(whisper_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whisper_conditions WHERE whisper_id=%s ORDER BY id ASC",
            (whisper_id,),
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["config"] = json.loads(d["config"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def delete_whisper_conditions(whisper_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_conditions WHERE whisper_id=%s", (whisper_id,))
        conn.commit()


def record_condition_attempt(
    whisper_id: str, user_id: int, condition_name: str,
    passed: bool, attempt_data: Optional[dict] = None,
) -> int:
    data_json = json.dumps(attempt_data) if attempt_data else None
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO condition_attempts (whisper_id, user_id, condition_name, passed, attempt_data)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING id",
            (whisper_id, user_id, condition_name, int(passed), data_json),
        )
        conn.commit()
        return cur.fetchone()["id"]


def get_condition_attempts(whisper_id: str, user_id: Optional[int] = None) -> list:
    with get_conn() as conn:
        if user_id is not None:
            rows = conn.execute(
                "SELECT * FROM condition_attempts WHERE whisper_id=%s AND user_id=%s ORDER BY id DESC",
                (whisper_id, user_id),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM condition_attempts WHERE whisper_id=%s ORDER BY id DESC",
                (whisper_id,),
            ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["passed"] = bool(d["passed"])
        try:
            if d["attempt_data"]:
                d["attempt_data"] = json.loads(d["attempt_data"])
        except (json.JSONDecodeError, TypeError):
            pass
        result.append(d)
    return result


def delete_condition_attempts(whisper_id: str) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM condition_attempts WHERE whisper_id=%s", (whisper_id,))
        conn.commit()
