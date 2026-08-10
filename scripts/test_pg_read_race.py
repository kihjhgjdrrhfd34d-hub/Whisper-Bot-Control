"""
scripts/test_pg_read_race.py — Real concurrent-read race tests against PostgreSQL.

Validates that record_whisper_read() can never exceed max_readers for the
limited types (first_one / first_three / first_five) even under true
concurrency, using independent pooled PostgreSQL connections.

Run (requires DATABASE_URL):
    DATABASE_URL=postgresql://user:pass@host:5432/whisperbot \\
        python scripts/test_pg_read_race.py

Without DATABASE_URL the script prints a skip notice and exits 0 so CI stays
green.  Safe to run against a live DB: it creates short-lived whispers and
deletes them when done.

Tests:
    ✓ exactly 3 of 4 concurrent reads accepted (limit 3)
    ✓ stress loop (20 rounds) never overflows the limit
    ✓ FOR UPDATE serialization is deterministic (2 overlapping transactions)
    ✓ accepted readers get ordinals 0,1,2 and variants[0], variants[1], variants[2]
    ✓ same-user repeat read returns False instead of raising IntegrityError
"""
import json
import os
import random
import sys
import threading
import time
import unittest

DB_URL = os.getenv("DATABASE_URL", "").strip()
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def _load_db():
    """Import the shadowed database module only when PostgreSQL is configured.

    Kept lazy so importing this module (e.g. under pytest collection) never
    flips a SQLite session into PostgreSQL mode.
    """
    import database as db  # noqa: E402
    return db


class _PgRaceBase(unittest.TestCase):
    """Shared PG bootstrap. Every test skips cleanly without DATABASE_URL."""

    DB = None
    SENDER = None
    READERS = ()

    @classmethod
    def setUpClass(cls):
        if not DB_URL:
            return
        db = _load_db()
        cls.DB = db
        db.init_db()
        # Random-ish ids so runs never collide with production users/whispers.
        cls.SENDER = random.SystemRandom().randrange(10**10, 2 * 10**10)
        cls.READERS = [cls.SENDER + i for i in (1, 2, 3, 4, 5)]
        for uid in [cls.SENDER] + list(cls.READERS):
            db.upsert_user(uid, f"user{uid}", f"User{uid}", None)

    def setUp(self):
        if self.DB is None:
            self.skipTest("DATABASE_URL not set")
        self._created = []
        self.wid = self._new_whisper("first_three", 3, variants=None)

    def tearDown(self):
        if self.DB is not None:
            for wid in self._created:
                try:
                    self.DB.delete_whisper(wid)
                except Exception:
                    pass

    def _new_whisper(self, wtype, max_readers, variants=None):
        kwargs = {"max_readers": max_readers}
        if variants is not None:
            kwargs["conditions_data"] = json.dumps(
                {"variants": variants}, ensure_ascii=False
            )
        wid = self.DB.create_whisper(self.SENDER, "race", wtype, **kwargs)
        self._created.append(wid)
        return wid

    def _run_concurrent(self, uids):
        """All threads hit record_whisper_read at the same instant.

        Each call opens its own pooled connection, so this is genuine
        cross-connection concurrency.  Returns {uid: bool}.
        """
        barrier = threading.Barrier(len(uids))
        results = {}

        def _read(uid):
            barrier.wait()
            results[uid] = self.DB.record_whisper_read(self.wid, uid)

        threads = [threading.Thread(target=_read, args=(u,)) for u in uids]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        return results

    def _manual_insert(self, uid, limit, lock_row=True):
        """BEGIN; SELECT..FOR UPDATE; conditional INSERT — no commit.

        Returns (conn, rowcount).  Caller decides when to commit/close.
        """
        db = self.DB
        conn = db.get_conn()
        conn.execute("BEGIN")
        try:
            if lock_row:
                conn.execute(
                    "SELECT whisper_type, max_readers FROM whispers "
                    "WHERE whisper_id=%s FOR UPDATE",
                    (self.wid,),
                )
            cur = conn.execute(
                "INSERT INTO whisper_readers (whisper_id, user_id) "
                "SELECT %s, %s "
                "WHERE (SELECT COUNT(*) FROM whisper_readers WHERE whisper_id=%s) < %s",
                (self.wid, uid, self.wid, limit),
            )
            return conn, cur.rowcount
        except Exception:
            conn.rollback()
            conn.close()
            raise


class TestConcurrentReads(_PgRaceBase):

    def test_exactly_three_of_four_concurrent_reads_accepted(self):
        uids = list(self.READERS[:4])
        results = self._run_concurrent(uids)
        accepted = [u for u, ok in results.items() if ok]
        self.assertEqual(len(accepted), 3,
                         "exactly 3 readers must be accepted when limit=3")
        self.assertEqual(self.DB.reader_count(self.wid), 3)
        self.assertLessEqual(self.DB.reader_count(self.wid), 3)

    def test_stress_no_overflow(self):
        for _ in range(20):
            self.wid = self._new_whisper("first_three", 3, variants=None)
            results = self._run_concurrent(list(self.READERS))
            self.assertLessEqual(self.DB.reader_count(self.wid), 3,
                                 f"limit exceeded on round {_}")
            self.assertEqual(sum(1 for ok in results.values() if ok), 3,
                             f"acceptance count wrong on round {_}")
            self.DB.delete_whisper(self.wid)

    def test_ordinals_and_variants_of_accepted_readers(self):
        self.wid = self._new_whisper(
            "first_three", 3, variants=["v0", "v1", "v2"],
        )
        results = self._run_concurrent(list(self.READERS[:4]))
        accepted = [u for u, ok in results.items() if ok]
        self.assertEqual(len(accepted), 3)

        ordinals = sorted(self.DB.get_reader_ordinal(self.wid, u) for u in accepted)
        self.assertEqual(ordinals, [0, 1, 2],
                         "accepted readers must get ordinals 0,1,2")

        from services.whisper_service import resolve_variant
        w = dict(self.DB.get_whisper(self.wid))
        assigned = sorted(resolve_variant(w, u) for u in accepted)
        self.assertEqual(assigned, ["v0", "v1", "v2"],
                         "variants must be assigned exactly [0],[1],[2]")


class TestForUpdateSerialization(_PgRaceBase):
    """Deterministic proof of the FOR UPDATE serialization.

    Two overlapping transactions both try to take the last slot.  Without
    FOR UPDATE the second sees the stale count and over-fills; with it the
    second blocks on the row lock, then sees count==3 and inserts nothing.
    """

    def setUp(self):
        super().setUp()
        # Seed 2 committed readers so the count is exactly at the boundary.
        self.assertTrue(self.DB.record_whisper_read(self.wid, self.READERS[0]))
        self.assertTrue(self.DB.record_whisper_read(self.wid, self.READERS[1]))

    def test_second_overlapping_transaction_is_rejected(self):
        db = self.DB

        # A: holds the whisper row lock and inserts (count 2<3 -> rowcount 1).
        conn_a, inserted_a = self._manual_insert(self.READERS[2], 3, lock_row=True)
        self.assertEqual(inserted_a, 1)

        outcome = {}

        def _b():
            conn_b, rowcount = self._manual_insert(self.READERS[3], 3, lock_row=True)
            outcome["rowcount"] = rowcount
            conn_b.commit()
            conn_b.close()

        t = threading.Thread(target=_b)
        t.start()
        time.sleep(0.25)  # let B reach (and block on) the row lock
        conn_a.commit()
        conn_a.close()
        t.join()

        self.assertEqual(outcome.get("rowcount"), 0,
                         "second concurrent insert must be rejected (count==3)")
        self.assertEqual(db.reader_count(self.wid), 3)
        self.assertLessEqual(db.reader_count(self.wid), 3)


class TestSameUserDoubleTap(_PgRaceBase):
    """Repeat read by the same user must return False, never raise."""

    def test_double_tap_returns_false(self):
        self.wid = self._new_whisper("first_five", 5, variants=None)
        self.assertTrue(self.DB.record_whisper_read(self.wid, self.READERS[0]))
        self.assertFalse(self.DB.record_whisper_read(self.wid, self.READERS[0]))
        self.assertEqual(self.DB.reader_count(self.wid), 1)


if __name__ == "__main__":
    if not DB_URL:
        print("⚠️  DATABASE_URL not set — skipping PostgreSQL race tests.")
        sys.exit(0)
    unittest.main(verbosity=2)
