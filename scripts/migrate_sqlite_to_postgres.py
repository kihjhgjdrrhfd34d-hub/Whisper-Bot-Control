"""
migrate_sqlite_to_postgres.py — Full data migration from SQLite to PostgreSQL.

Usage:
    DATABASE_URL=postgresql://user:pass@host:5432/whisperbot \
        python scripts/migrate_sqlite_to_postgres.py

Requires:
    - whispers.db (existing SQLite database)
    - DATABASE_URL env var pointing to a PostgreSQL database
"""

import os
import sys
import sqlite3

import psycopg2
import psycopg2.extras

SQLITE_DB = os.getenv("DATABASE_PATH", "whispers.db")
POSTGRES_URL = os.getenv("DATABASE_URL")

if not POSTGRES_URL:
    print("DATABASE_URL environment variable is required")
    sys.exit(1)


def sqlite_conn():
    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row
    return conn


def postgres_conn():
    conn = psycopg2.connect(POSTGRES_URL)
    conn.autocommit = False
    return conn


def get_sqlite_tables(conn):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    return [r[0] for r in rows]


def table_exists_pg(cur, table_name):
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=%s",
        (table_name,),
    )
    return cur.fetchone() is not None


def pg_column_defs(table_name):
    schema_statements = {
        "users": """
            CREATE TABLE IF NOT EXISTS users (
                user_id     BIGINT PRIMARY KEY,
                username    TEXT,
                first_name  TEXT,
                last_name   TEXT,
                created_at  TEXT,
                is_banned   INTEGER DEFAULT 0,
                started     INTEGER DEFAULT 0
            )""",
        "whispers": """
            CREATE TABLE IF NOT EXISTS whispers (
                whisper_id      TEXT PRIMARY KEY,
                sender_id       BIGINT NOT NULL,
                content         TEXT NOT NULL,
                whisper_type    TEXT NOT NULL,
                target_users    TEXT DEFAULT '[]',
                max_readers     INTEGER DEFAULT 0,
                is_locked       INTEGER DEFAULT 0,
                created_at      TEXT,
                auto_delete_at  TEXT,
                is_destructive  INTEGER DEFAULT 0,
                message_type    TEXT,
                file_id         TEXT,
                caption         TEXT,
                location_lat    DOUBLE PRECISION,
                location_lon    DOUBLE PRECISION,
                is_closed       INTEGER DEFAULT 0,
                is_pinned       INTEGER DEFAULT 0,
                media_type      TEXT,
                group_chat_id   BIGINT,
                group_message_id INTEGER,
                group_inline_message_id TEXT,
                is_anonymous    INTEGER DEFAULT 0,
                is_self_destruct INTEGER DEFAULT 0,
                parent_whisper_id TEXT,
                is_archived     INTEGER DEFAULT 0
            )""",
        "whisper_readers": """
            CREATE TABLE IF NOT EXISTS whisper_readers (
                id          SERIAL PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                user_id     BIGINT NOT NULL,
                read_at     TEXT,
                UNIQUE(whisper_id, user_id)
            )""",
        "curious_ones": """
            CREATE TABLE IF NOT EXISTS curious_ones (
                id          SERIAL PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                user_id     BIGINT NOT NULL,
                tried_at    TEXT,
                UNIQUE(whisper_id, user_id)
            )""",
        "settings": """
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )""",
        "mandatory_channels": """
            CREATE TABLE IF NOT EXISTS mandatory_channels (
                id           SERIAL PRIMARY KEY,
                channel_id   TEXT NOT NULL UNIQUE,
                channel_name TEXT
            )""",
        "broadcasts": """
            CREATE TABLE IF NOT EXISTS broadcasts (
                id         SERIAL PRIMARY KEY,
                content    TEXT,
                media_type TEXT,
                file_id    TEXT,
                sent_at    TEXT,
                sent_by    BIGINT
            )""",
        "group_settings": """
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id                 BIGINT PRIMARY KEY,
                public_whispers_enabled INTEGER DEFAULT 1,
                anonymous_enabled       INTEGER DEFAULT 1,
                read_notifications      INTEGER DEFAULT 1,
                auto_delete_minutes     INTEGER DEFAULT 0,
                spam_limit_enabled      INTEGER DEFAULT 1,
                spam_limit_count        INTEGER DEFAULT 5,
                spam_limit_window_seconds INTEGER DEFAULT 60
            )""",
        "whisper_timestamps": """
            CREATE TABLE IF NOT EXISTS whisper_timestamps (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                chat_id     BIGINT NOT NULL,
                created_at  TEXT
            )""",
        "pending_media_whispers": """
            CREATE TABLE IF NOT EXISTS pending_media_whispers (
                id           SERIAL PRIMARY KEY,
                user_id      BIGINT NOT NULL,
                message_type TEXT NOT NULL,
                file_id      TEXT NOT NULL,
                caption      TEXT,
                content      TEXT,
                created_at   TEXT
            )""",
        "whisper_replies": """
            CREATE TABLE IF NOT EXISTS whisper_replies (
                reply_id    TEXT PRIMARY KEY,
                whisper_id  TEXT NOT NULL,
                sender_id   BIGINT NOT NULL,
                parent_reply_id TEXT,
                content     TEXT NOT NULL DEFAULT '',
                media_type  TEXT,
                file_id     TEXT,
                created_at  TEXT
            )""",
        "reply_reads": """
            CREATE TABLE IF NOT EXISTS reply_reads (
                id        SERIAL PRIMARY KEY,
                reply_id  TEXT NOT NULL,
                user_id   BIGINT NOT NULL,
                read_at   TEXT,
                UNIQUE(reply_id, user_id)
            )""",
        "personal_whispers": """
            CREATE TABLE IF NOT EXISTS personal_whispers (
                id              SERIAL PRIMARY KEY,
                whisper_id      TEXT NOT NULL UNIQUE,
                sender_id       BIGINT NOT NULL,
                recipient_id    BIGINT NOT NULL,
                content         TEXT NOT NULL,
                is_read         INTEGER DEFAULT 0,
                read_at         TEXT,
                created_at      TEXT
            )""",
        "user_xp": """
            CREATE TABLE IF NOT EXISTS user_xp (
                user_id     BIGINT PRIMARY KEY,
                xp          INTEGER DEFAULT 0,
                level       INTEGER DEFAULT 1,
                rank_title  TEXT DEFAULT 'مبتدئ',
                updated_at  TEXT
            )""",
        "achievements": """
            CREATE TABLE IF NOT EXISTS achievements (
                id          SERIAL PRIMARY KEY,
                code        TEXT NOT NULL UNIQUE,
                title       TEXT NOT NULL,
                description TEXT,
                icon        TEXT DEFAULT '🏆',
                xp_reward   INTEGER DEFAULT 0
            )""",
        "user_achievements": """
            CREATE TABLE IF NOT EXISTS user_achievements (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                code        TEXT NOT NULL,
                earned_at   TEXT,
                UNIQUE(user_id, code)
            )""",
        "invites": """
            CREATE TABLE IF NOT EXISTS invites (
                id          SERIAL PRIMARY KEY,
                inviter_id  BIGINT NOT NULL,
                invitee_id  BIGINT NOT NULL UNIQUE,
                invited_at  TEXT
            )""",
        "activity_log": """
            CREATE TABLE IF NOT EXISTS activity_log (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                action      TEXT NOT NULL,
                meta        TEXT,
                created_at  TEXT
            )""",
        "reports": """
            CREATE TABLE IF NOT EXISTS reports (
                id           SERIAL PRIMARY KEY,
                reporter_id  BIGINT NOT NULL,
                whisper_id   TEXT,
                reason       TEXT,
                status       TEXT DEFAULT 'pending',
                reviewed_by  BIGINT,
                reviewed_at  TEXT,
                created_at   TEXT
            )""",
        "ban_log": """
            CREATE TABLE IF NOT EXISTS ban_log (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                action      TEXT NOT NULL,
                reason      TEXT,
                banned_by   BIGINT,
                expires_at  TEXT,
                created_at  TEXT
            )""",
        "temp_bans": """
            CREATE TABLE IF NOT EXISTS temp_bans (
                user_id     BIGINT PRIMARY KEY,
                reason      TEXT,
                banned_by   BIGINT,
                expires_at  TEXT NOT NULL,
                created_at  TEXT
            )""",
        "backup_registry": """
            CREATE TABLE IF NOT EXISTS backup_registry (
                id          SERIAL PRIMARY KEY,
                filename    TEXT NOT NULL UNIQUE,
                size_bytes  BIGINT,
                created_at  TEXT,
                created_by  BIGINT,
                notes       TEXT
            )""",
        "stats_snapshots": """
            CREATE TABLE IF NOT EXISTS stats_snapshots (
                id           SERIAL PRIMARY KEY,
                period_type  TEXT NOT NULL,
                period_label TEXT NOT NULL,
                data_json    TEXT NOT NULL,
                created_at   TEXT,
                UNIQUE(period_type, period_label)
            )""",
        "whisper_favorites": """
            CREATE TABLE IF NOT EXISTS whisper_favorites (
                id          SERIAL PRIMARY KEY,
                user_id     BIGINT NOT NULL,
                whisper_id  TEXT NOT NULL,
                saved_at    TEXT,
                UNIQUE(user_id, whisper_id)
            )""",
        "whisper_archive": """
            CREATE TABLE IF NOT EXISTS whisper_archive (
                whisper_id   TEXT PRIMARY KEY,
                sender_id    BIGINT NOT NULL,
                content      TEXT NOT NULL,
                whisper_type TEXT NOT NULL,
                archived_at  TEXT,
                original_data TEXT
            )""",
        "whisper_destruct": """
            CREATE TABLE IF NOT EXISTS whisper_destruct (
                whisper_id   TEXT PRIMARY KEY,
                destruct_on  TEXT,
                after_reads  INTEGER DEFAULT 0
            )""",
    }
    return schema_statements.get(table_name)


def copy_table(sq, pg, table_name):
    sq_cursor = sq.execute(f"SELECT * FROM {table_name}")
    columns = [desc[0] for desc in sq_cursor.description]
    rows = sq_cursor.fetchall()

    if not rows:
        print(f"     {table_name}: 0 rows (empty)")
        return 0

    col_names = ",".join(columns)
    placeholders = ",".join("%s" for _ in columns)
    pg_cursor = pg.cursor()
    batch_size = 500
    total = 0

    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        values_batch = [tuple(row) for row in batch]

        try:
            psycopg2.extras.execute_values(
                pg_cursor,
                f"INSERT INTO {table_name} ({col_names}) VALUES %s",
                values_batch,
                template=f"({placeholders})",
            )
        except Exception as exc:
            print(f"     {table_name}: batch error at row {i}: {exc}")
            pg.rollback()
            raise

        total += len(batch)
        print(f"     {table_name}: {total}/{len(rows)} rows", end="\r")

    pg.commit()
    print(f"     {table_name}: {total} rows copied")
    return total


def main():
    if not os.path.exists(SQLITE_DB):
        print(f"SQLite database not found: {SQLITE_DB}")
        sys.exit(1)

    print(f"SQLite: {SQLITE_DB}")
    print(f"PostgreSQL: {POSTGRES_URL.split('@')[-1] if '@' in POSTGRES_URL else POSTGRES_URL}")

    sq = sqlite_conn()
    pg = postgres_conn()

    try:
        sq_tables = get_sqlite_tables(sq)
        print(f"\nFound {len(sq_tables)} tables in SQLite:")
        for t in sq_tables:
            count = sq.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"   - {t}: {count} rows")
        print()

        ordered_tables = [
            "users",
            "settings",
            "mandatory_channels",
            "broadcasts",
            "group_settings",
            "achievements",
            "whispers",
            "whisper_readers",
            "curious_ones",
            "whisper_timestamps",
            "pending_media_whispers",
            "whisper_replies",
            "reply_reads",
            "personal_whispers",
            "user_xp",
            "user_achievements",
            "invites",
            "activity_log",
            "reports",
            "ban_log",
            "temp_bans",
            "backup_registry",
            "stats_snapshots",
            "whisper_favorites",
            "whisper_archive",
            "whisper_destruct",
        ]

        print("Creating PostgreSQL tables...")
        for table in ordered_tables:
            stmt = pg_column_defs(table)
            if stmt:
                try:
                    cur = pg.cursor()
                    cur.execute(stmt)
                    pg.commit()
                    print(f"   {table}")
                except Exception as exc:
                    pg.rollback()
                    print(f"   {table}: {exc}")

        print("Truncating PostgreSQL tables (CASCADE)...")
        tables_to_truncate = [t for t in ordered_tables if t in sq_tables]
        if tables_to_truncate:
            cur = pg.cursor()
            truncate_sql = "TRUNCATE " + ", ".join(tables_to_truncate) + " RESTART IDENTITY CASCADE"
            cur.execute(truncate_sql)
            pg.commit()
            print(f"   Truncated {len(tables_to_truncate)} tables")

        print("\nCopying data to PostgreSQL...")
        total_rows = 0
        results = {}
        for table in ordered_tables:
            if table in sq_tables:
                rows_copied = copy_table(sq, pg, table)
                results[table] = rows_copied
                total_rows += rows_copied

        print(f"\n{'='*50}")
        print(f"Migration complete! {total_rows} total rows copied.")
        print(f"{'='*50}")

        print("\nVerification report: SQLite → PostgreSQL")
        print(f"{'='*50}")
        print(f"{'Table':<22} {'SQLite':>8} {'PG':>8} {'Status':>8}")
        print(f"{'─'*50}")
        all_ok = True
        for table in ordered_tables:
            if table in sq_tables:
                sq_count = sq.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                try:
                    cur = pg.cursor()
                    cur.execute(f"SELECT COUNT(*) FROM {table}")
                    pg_count = cur.fetchone()[0]
                    match = sq_count == pg_count
                    if not match:
                        all_ok = False
                    status = "OK" if match else "MISMATCH"
                    print(f"{table:<22} {sq_count:>8} {pg_count:>8} {status:>8}")
                except Exception as exc:
                    print(f"{table:<22} {'ERR':>8} {'ERR':>8}")
                    all_ok = False
        print(f"{'─'*50}")

        if all_ok:
            print("Result: All tables match — migration completed successfully ✓")
        else:
            print("Result: Mismatches found — migration completed with differences ✗")

    finally:
        sq.close()
        pg.close()


if __name__ == "__main__":
    main()
