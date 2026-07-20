import json
import logging
from contextlib import contextmanager
from database.postgres import get_conn as _pg_get_conn

logger = logging.getLogger(__name__)

SEED_COVERS = [
    ("cover_classic", "أساسي",     "🤫", 1, 0),
    ("cover_elegant", "أنيق",       "🌟", 0, 0),
    ("cover_mystic",  "غامض",      "🌙", 0, 0),
    ("cover_shadow",  "ظل",        "🌑", 0, 150),
    ("cover_royal",   "ملكي",      "👑", 0, 50),
]

SEED_CHARACTERS = [
    ("char_whisperer", "المُهمس",   "🤫", "🤫 همسة في أذنك..",       1, 0),
    ("char_shadow",    "الظل",      "🌑", "🕯️ من الظل إلى النور..",   0, 50),
    ("char_mystery",   "الغموض",    "🔮", "🔮 سرٌ من الأسرار..",      0, 100),
    ("char_wise",      "الحكيم",    "📖", "📖 يقول الحكيم..",         0, 200),
]


def init_package_flow_db():
    with _pg_get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_covers (
                id          SERIAL PRIMARY KEY,
                code        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                icon        TEXT DEFAULT '📜',
                is_default  INTEGER DEFAULT 0,
                min_xp      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS message_characters (
                id          SERIAL PRIMARY KEY,
                code        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                icon        TEXT DEFAULT '👤',
                greeting    TEXT DEFAULT '',
                is_default  INTEGER DEFAULT 0,
                min_xp      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (NOW())
            );

            CREATE TABLE IF NOT EXISTS whisper_packages (
                id                SERIAL PRIMARY KEY,
                user_id           BIGINT NOT NULL,
                cover_code        TEXT DEFAULT 'cover_classic',
                character_code    TEXT DEFAULT 'char_whisperer',
                content           TEXT NOT NULL DEFAULT '',
                target_chat_id    BIGINT,
                target_chat_title TEXT DEFAULT '',
                whisper_type      TEXT,
                target_users      TEXT DEFAULT '[]',
                max_readers       INTEGER DEFAULT 0,
                step              INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (NOW()),
                updated_at        TEXT DEFAULT (NOW())
            );

            CREATE INDEX IF NOT EXISTS idx_wp_user
                ON whisper_packages(user_id);
        """)
        conn.commit()

    _seed_covers()
    _seed_characters()
    _run_migrations()
    logger.info("Package flow DB initialised (PG)")


def _run_migrations():
    with _pg_get_conn() as conn:
        tables = {
            r["table_name"] for r in conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE'"
            ).fetchall()
        }
        if "whisper_covers" in tables:
            cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='whisper_covers' AND table_schema='public'"
            ).fetchall()}
            if "min_xp" not in cols:
                conn.execute("ALTER TABLE whisper_covers ADD COLUMN min_xp INTEGER DEFAULT 0")
        if "message_characters" in tables:
            cols = {r["column_name"] for r in conn.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='message_characters' AND table_schema='public'"
            ).fetchall()}
            if "min_xp" not in cols:
                conn.execute("ALTER TABLE message_characters ADD COLUMN min_xp INTEGER DEFAULT 0")
        conn.commit()


def _seed_covers():
    with _pg_get_conn() as conn:
        for code, name, icon, is_default, min_xp in SEED_COVERS:
            conn.execute(
                "INSERT INTO whisper_covers (code, name, icon, is_default, min_xp)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (code) DO NOTHING",
                (code, name, icon, is_default, min_xp),
            )
        conn.commit()


def _seed_characters():
    with _pg_get_conn() as conn:
        for code, name, icon, greeting, is_default, min_xp in SEED_CHARACTERS:
            conn.execute(
                "INSERT INTO message_characters (code, name, icon, greeting, is_default, min_xp)"
                " VALUES (%s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (code) DO NOTHING",
                (code, name, icon, greeting, is_default, min_xp),
            )
        conn.commit()


def get_available_covers(user_xp=0):
    with _pg_get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whisper_covers WHERE min_xp <= %s ORDER BY min_xp ASC, id ASC",
            (user_xp,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_covers():
    with _pg_get_conn() as conn:
        rows = conn.execute("SELECT * FROM whisper_covers ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_cover(code):
    with _pg_get_conn() as conn:
        row = conn.execute("SELECT * FROM whisper_covers WHERE code=%s", (code,)).fetchone()
        return dict(row) if row else None


def get_available_characters(user_xp=0):
    with _pg_get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM message_characters WHERE min_xp <= %s ORDER BY min_xp ASC, id ASC",
            (user_xp,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_characters():
    with _pg_get_conn() as conn:
        rows = conn.execute("SELECT * FROM message_characters ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_character(code):
    with _pg_get_conn() as conn:
        row = conn.execute("SELECT * FROM message_characters WHERE code=%s", (code,)).fetchone()
        return dict(row) if row else None


def create_package(user_id):
    delete_package(user_id)
    with _pg_get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO whisper_packages (user_id) VALUES (%s) RETURNING *",
            (user_id,),
        )
        conn.commit()
        row = cur.fetchone()
        return dict(row) if row else None


def get_package(user_id):
    with _pg_get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_packages WHERE user_id=%s ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_package_step(user_id, step):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET step=%s, updated_at=NOW() WHERE user_id=%s",
            (step, user_id),
        )
        conn.commit()


def update_package_cover(user_id, cover_code):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET cover_code=%s, step=2, updated_at=NOW() WHERE user_id=%s",
            (cover_code, user_id),
        )
        conn.commit()


def update_package_character(user_id, character_code):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET character_code=%s, step=3, updated_at=NOW() WHERE user_id=%s",
            (character_code, user_id),
        )
        conn.commit()


def update_package_content(user_id, content):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET content=%s, step=4, updated_at=NOW() WHERE user_id=%s",
            (content, user_id),
        )
        conn.commit()


def update_package_target(user_id, chat_id, chat_title):
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET target_chat_id=%s, target_chat_title=%s, step=5, updated_at=NOW() WHERE user_id=%s",
            (chat_id, chat_title, user_id),
        )
        conn.commit()


def update_package_type(user_id, whisper_type, target_users=None, max_readers=0):
    targets = json.dumps(target_users or [])
    with _pg_get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET whisper_type=%s, target_users=%s, max_readers=%s, step=6, updated_at=NOW() WHERE user_id=%s",
            (whisper_type, targets, max_readers, user_id),
        )
        conn.commit()


def delete_package(user_id):
    with _pg_get_conn() as conn:
        conn.execute("DELETE FROM whisper_packages WHERE user_id=%s", (user_id,))
        conn.commit()
