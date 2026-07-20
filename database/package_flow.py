import json
import logging
from database import get_conn

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
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS whisper_covers (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                icon        TEXT DEFAULT '📜',
                is_default  INTEGER DEFAULT 0,
                min_xp      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS message_characters (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                code        TEXT NOT NULL UNIQUE,
                name        TEXT NOT NULL,
                icon        TEXT DEFAULT '👤',
                greeting    TEXT DEFAULT '',
                is_default  INTEGER DEFAULT 0,
                min_xp      INTEGER DEFAULT 0,
                created_at  TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS whisper_packages (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id           INTEGER NOT NULL,
                cover_code        TEXT DEFAULT 'cover_classic',
                character_code    TEXT DEFAULT 'char_whisperer',
                content           TEXT NOT NULL DEFAULT '',
                target_chat_id    INTEGER,
                target_chat_title TEXT DEFAULT '',
                whisper_type      TEXT,
                target_users      TEXT DEFAULT '[]',
                max_readers       INTEGER DEFAULT 0,
                step              INTEGER DEFAULT 1,
                created_at        TEXT DEFAULT (datetime('now')),
                updated_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_wp_user
                ON whisper_packages(user_id);
        """)
        conn.commit()

    _seed_covers()
    _seed_characters()
    _run_migrations()
    logger.info("Package flow DB initialised")


def _run_migrations():
    with get_conn() as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        if "whisper_covers" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(whisper_covers)").fetchall()]
            if "min_xp" not in cols:
                conn.execute("ALTER TABLE whisper_covers ADD COLUMN min_xp INTEGER DEFAULT 0")
        if "message_characters" in tables:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(message_characters)").fetchall()]
            if "min_xp" not in cols:
                conn.execute("ALTER TABLE message_characters ADD COLUMN min_xp INTEGER DEFAULT 0")
        conn.commit()


def _seed_covers():
    with get_conn() as conn:
        for code, name, icon, is_default, min_xp in SEED_COVERS:
            conn.execute(
                "INSERT OR IGNORE INTO whisper_covers (code, name, icon, is_default, min_xp)"
                " VALUES (?, ?, ?, ?, ?)",
                (code, name, icon, is_default, min_xp),
            )
        conn.commit()


def _seed_characters():
    with get_conn() as conn:
        for code, name, icon, greeting, is_default, min_xp in SEED_CHARACTERS:
            conn.execute(
                "INSERT OR IGNORE INTO message_characters (code, name, icon, greeting, is_default, min_xp)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (code, name, icon, greeting, is_default, min_xp),
            )
        conn.commit()


def get_available_covers(user_xp=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM whisper_covers WHERE min_xp <= ? ORDER BY min_xp ASC, id ASC",
            (user_xp,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_covers():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM whisper_covers ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_cover(code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM whisper_covers WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def get_available_characters(user_xp=0):
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM message_characters WHERE min_xp <= ? ORDER BY min_xp ASC, id ASC",
            (user_xp,),
        ).fetchall()
        return [dict(r) for r in rows]


def get_all_characters():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM message_characters ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]


def get_character(code):
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM message_characters WHERE code=?", (code,)).fetchone()
        return dict(row) if row else None


def create_package(user_id):
    delete_package(user_id)
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO whisper_packages (user_id) VALUES (?)",
            (user_id,),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM whisper_packages WHERE user_id=? ORDER BY id DESC LIMIT 1", (user_id,)).fetchone()
        return dict(row) if row else None


def get_package(user_id):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM whisper_packages WHERE user_id=? ORDER BY id DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        return dict(row) if row else None


def update_package_step(user_id, step):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET step=?, updated_at=datetime('now') WHERE user_id=?",
            (step, user_id),
        )
        conn.commit()


def update_package_cover(user_id, cover_code):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET cover_code=?, step=2, updated_at=datetime('now') WHERE user_id=?",
            (cover_code, user_id),
        )
        conn.commit()


def update_package_character(user_id, character_code):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET character_code=?, step=3, updated_at=datetime('now') WHERE user_id=?",
            (character_code, user_id),
        )
        conn.commit()


def update_package_content(user_id, content):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET content=?, step=4, updated_at=datetime('now') WHERE user_id=?",
            (content, user_id),
        )
        conn.commit()


def update_package_target(user_id, chat_id, chat_title):
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET target_chat_id=?, target_chat_title=?, step=5, updated_at=datetime('now') WHERE user_id=?",
            (chat_id, chat_title, user_id),
        )
        conn.commit()


def update_package_type(user_id, whisper_type, target_users=None, max_readers=0):
    targets = json.dumps(target_users or [])
    with get_conn() as conn:
        conn.execute(
            "UPDATE whisper_packages SET whisper_type=?, target_users=?, max_readers=?, step=6, updated_at=datetime('now') WHERE user_id=?",
            (whisper_type, targets, max_readers, user_id),
        )
        conn.commit()


def delete_package(user_id):
    with get_conn() as conn:
        conn.execute("DELETE FROM whisper_packages WHERE user_id=?", (user_id,))
        conn.commit()


from database.postgres import USE_POSTGRES
if USE_POSTGRES:
    from database.pg_package_flow import *
