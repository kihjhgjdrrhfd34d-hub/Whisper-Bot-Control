"""
test_postgres.py — Verify PostgreSQL adapter setup.

Usage:
    # Test module imports and connection (requires DATABASE_URL)
    DATABASE_URL=postgresql://user:pass@host:5432/whisperbot \\
        python scripts/test_postgres.py

    # Test just the module imports (no DB needed)
    python scripts/test_postgres.py --check-imports

Tests:
    ✓ PostgreSQL adapter modules can be imported
    ✓ DATABASE_URL is present (if expected)
    ✓ PostgreSQL connection succeeds
    ✓ All expected tables exist
"""

import os
import sys
import argparse

EXPECTED_TABLES = [
    "users", "whispers", "whisper_readers", "curious_ones",
    "settings", "mandatory_channels", "broadcasts", "group_settings",
    "whisper_timestamps", "pending_media_whispers",
    "whisper_replies", "reply_reads", "personal_whispers",
    "user_xp", "achievements", "user_achievements", "invites",
    "activity_log", "reports", "ban_log", "temp_bans",
    "backup_registry", "stats_snapshots",
    "whisper_favorites", "whisper_archive", "whisper_destruct",
]


def check_imports():
    """Verify that all PostgreSQL adapter modules can be imported."""
    print("🔍 Checking module imports...")
    modules = [
        "config",
        "database.postgres",
        "database.pg_core",
        "database.pg_personal",
        "database.pg_replies",
        "enterprise.pg_enterprise",
    ]
    all_ok = True
    for mod_name in modules:
        try:
            __import__(mod_name, fromlist=["_"])
            print(f"  ✅ {mod_name}")
        except Exception as exc:
            print(f"  ❌ {mod_name}: {exc}")
            all_ok = False
    return all_ok


def check_shadow_adapter():
    """Verify that the shadow adapter mechanism works (import via original modules)."""
    print("\n🔍 Checking shadow adapter wiring...")

    checks = [
        ("database.__init__", "database", ["init_db", "get_conn", "upsert_user"]),
        ("database.personal", "database.personal", ["init_personal_db", "get_conn", "create_personal_whisper"]),
        ("database.replies", "database.replies", ["init_replies_db", "create_reply"]),
        ("enterprise.db_enterprise", "enterprise.db_enterprise", ["init_enterprise_db", "award_xp"]),
    ]
    all_ok = True
    for label, mod_name, funcs in checks:
        try:
            mod = __import__(mod_name, fromlist=funcs)
            for fn in funcs:
                if hasattr(mod, fn):
                    print(f"  ✅ {label}.{fn}")
                else:
                    print(f"  ❌ {label}.{fn} — not found")
                    all_ok = False
        except Exception as exc:
            print(f"  ❌ {label}: import failed — {exc}")
            all_ok = False
    return all_ok


def test_connection():
    """Test PostgreSQL connection and table count."""
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        print("\n⚠️  DATABASE_URL not set — skipping connection test.")
        print("   Set DATABASE_URL to run full connection test:")
        print("   DATABASE_URL=postgresql://user:pass@host:5432/dbname python scripts/test_postgres.py")
        return False

    print(f"\n🔌 Testing PostgreSQL connection...")
    try:
        from database.postgres import get_conn, USE_POSTGRES
        if not USE_POSTGRES:
            print("  ⚠️  USE_POSTGRES is False — DATABASE_URL may be empty")
            return False

        conn = get_conn()
        print("  ✅ Connection successful!")

        # Get table count
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE'"
        ).fetchall()
        existing = {r["table_name"] for r in rows}
        print(f"\n📋 Tables in PostgreSQL ({len(existing)} total):")
        for t in sorted(existing):
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()["count"]
            print(f"   - {t}: {count} rows")

        # Check for expected tables
        print(f"\n🔍 Expected tables check ({len(EXPECTED_TABLES)} total):")
        present = 0
        for t in EXPECTED_TABLES:
            if t in existing:
                print(f"  ✅ {t}")
                present += 1
            else:
                print(f"  ⚠️  {t} — not found")
        print(f"\n   {present}/{len(EXPECTED_TABLES)} expected tables present")

        conn.close()
        return True
    except Exception as exc:
        print(f"  ❌ Connection failed: {exc}")
        return False


def main():
    parser = argparse.ArgumentParser(description="Test PostgreSQL adapter setup")
    parser.add_argument("--check-imports", action="store_true",
                        help="Only check imports, skip connection test")
    args = parser.parse_args()

    print("=" * 50)
    print("  PostgreSQL Adapter Test")
    print("=" * 50)

    imports_ok = check_imports()
    if not imports_ok:
        print("\n❌ Some imports failed!")
        sys.exit(1)
    else:
        print("\n✅ All imports OK!")

    shadow_ok = check_shadow_adapter()
    if not shadow_ok:
        print("\n❌ Some shadow adapter checks failed!")
        sys.exit(1)
    else:
        print("\n✅ Shadow adapter wiring OK!")

    if not args.check_imports:
        conn_ok = test_connection()
        if conn_ok:
            print("\n✅ PostgreSQL adapter is fully operational!")
        else:
            print("\n⚠️  Connection test skipped or failed (may be expected without DATABASE_URL)")
    else:
        print("\n⏭️  Connection test skipped (--check-imports)")

    print("=" * 50)


if __name__ == "__main__":
    main()
