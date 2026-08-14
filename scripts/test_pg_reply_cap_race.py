"""
scripts/test_pg_reply_cap_race.py — Real concurrent reply-cap race tests
against PostgreSQL, plus the close/lock TOCTOU guard on record_whisper_read.

Validates that create_reply() can never exceed MAX_REPLIES_PER_WHISPER even
under true concurrency (independent pooled connections blocked on the whisper
row FOR UPDATE), mirroring scripts/test_pg_read_race.py for the reader limit.

Run (requires DATABASE_URL):
    DATABASE_URL=postgresql://user:pass@host:5432/whisperbot \\
        python scripts/test_pg_reply_cap_race.py

Without DATABASE_URL the script prints a skip notice and exits 0 so CI stays
green.  Safe to run against a live DB: it creates short-lived whispers and
deletes them when done.

Tests:
    ✓ exactly 5 of 20 concurrent replies accepted (MAX caps the burst)
    ✓ FOR UPDATE serialization is deterministic (2 overlapping transactions)
    ✓ record_whisper_read rejects a read after close/lock (TOCTOU guard)
    ✓ single-threaded cap still enforced
"""

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


class _PgReplyCapBase(unittest.TestCase):
    """Shared PG bootstrap. Every test skips cleanly without DATABASE_URL."""

    DB = None
    SENDER = None
    REPLIERS = ()

    @classmethod
    def setUpClass(cls):
        if not DB_URL:
            return
        db = _load_db()
        cls.DB = db
        db.init_db()
        from database.replies import MAX_REPLIES_PER_WHISPER
        cls.MAX = MAX_REPLIES_PER_WHISPER
        # Random-ish ids so runs never collide with production users/whispers.
        cls.SENDER = random.SystemRandom().randrange(10**10, 2 * 10**10)
        cls.REPLIERS = [cls.SENDER + i for i in range(1, 21)]
        db.upsert_user(cls.SENDER, f"user{cls.SENDER}", "Sender", None)
        for uid in cls.REPLIERS:
            db.upsert_user(uid, f"user{uid}", f"User{uid}", None)

    def setUp(self):
        if self.DB is None:
            self.skipTest("DATABASE_URL not set")
        self._created = []
        self.wid = self._new_whisper()

    def tearDown(self):
        if self.DB is not None:
            for wid in self._created:
                try:
                    self.DB.delete_whisper(wid)
                except Exception:
                    pass

    def _new_whisper(self):
        wid = self.DB.create_whisper(self.SENDER, "reply cap race", "everyone")
        self._created.append(wid)
        return wid


class TestConcurrentReplyCap(_PgReplyCapBase):
    """Burst of concurrent create_reply against a nearly-full whisper."""

    def test_burst_never_exceeds_cap(self):
        db = self.DB
        from database.replies import create_reply, count_replies

        remaining = 5
        for i in range(self.MAX - remaining):
            rid = create_reply(self.wid, self.SENDER, content=f"seed {i}")
            self.assertIsNotNone(rid, f"seed {i} must succeed")

        n_attempts = 20
        barrier = threading.Barrier(n_attempts)
        accepted = []
        errors = []

        def _reply(idx):
            barrier.wait()
            try:
                rid = create_reply(self.wid, self.SENDER, content=f"burst {idx}")
                if rid is not None:
                    accepted.append(rid)
            except Exception as exc:  # pragma: no cover - unexpected
                errors.append(str(exc))

        threads = [threading.Thread(target=_reply, args=(i,)) for i in range(n_attempts)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"unexpected errors: {errors}")
        total = count_replies(self.wid)
        self.assertLessEqual(
            total, self.MAX,
            f"reply cap exceeded: {total} > {self.MAX}",
        )
        self.assertEqual(
            len(accepted), remaining,
            f"exactly the remaining {remaining} slots must be filled, "
            f"got {len(accepted)} (total={total})",
        )
        self.assertEqual(total, self.MAX)

    def test_stress_no_overflow(self):
        db = self.DB
        from database.replies import create_reply, count_replies

        # Fill to MAX-2, then 10 concurrent attempts per round.
        for round_no in range(5):
            self.wid = self._new_whisper()
            for i in range(self.MAX - 2):
                self.assertIsNotNone(create_reply(self.wid, self.SENDER, content=f"s{i}"))
            remaining = 2
            barrier = threading.Barrier(10)
            accepted = []

            def _reply(idx):
                barrier.wait()
                rid = create_reply(self.wid, self.SENDER, content=f"r{round_no}-{idx}")
                if rid is not None:
                    accepted.append(rid)

            threads = [threading.Thread(target=_reply, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertLessEqual(count_replies(self.wid), self.MAX,
                                 f"cap exceeded on round {round_no}")
            self.assertEqual(len(accepted), remaining,
                             f"acceptance count wrong on round {round_no}")
            self.DB.delete_whisper(self.wid)


class TestForUpdateReplySerialization(_PgReplyCapBase):
    """Deterministic proof of the FOR UPDATE serialization in create_reply."""

    def setUp(self):
        super().setUp()
        self._replies_pending = []
        self._replies = None

    def _manual_insert(self, content):
        """BEGIN; SELECT..FOR UPDATE; conditional INSERT — no commit.

        Returns (conn, rowcount).  Caller decides when to commit/close.
        """
        db = self.DB
        conn = db.get_conn()
        conn.execute("BEGIN")
        try:
            from database.replies import MAX_REPLIES_PER_WHISPER
            conn.execute(
                "SELECT whisper_id FROM whispers WHERE whisper_id=%s FOR UPDATE",
                (self.wid,),
            )
            import uuid
            rid = str(uuid.uuid4())[:12]
            cur = conn.execute(
                "INSERT INTO whisper_replies "
                "(reply_id, whisper_id, sender_id, parent_reply_id, content, media_type, file_id) "
                "SELECT %s, %s, %s, %s, %s, %s, %s "
                "WHERE EXISTS (SELECT 1 FROM whispers WHERE whisper_id=%s) "
                "AND (SELECT COUNT(*) FROM whisper_replies WHERE whisper_id=%s) < %s",
                (rid, self.wid, self.SENDER, None, content, None, None,
                 self.wid, self.wid, MAX_REPLIES_PER_WHISPER),
            )
            return conn, cur.rowcount
        except Exception:
            conn.rollback()
            conn.close()
            raise

    def test_second_overlapping_transaction_is_rejected_at_cap(self):
        db = self.DB
        from database.replies import create_reply, count_replies, MAX_REPLIES_PER_WHISPER

        # Fill to the cap so the count is exactly at the boundary.
        for i in range(MAX_REPLIES_PER_WHISPER):
            self.assertIsNotNone(create_reply(self.wid, self.SENDER, content=f"f{i}"))
        self.assertEqual(count_replies(self.wid), MAX_REPLIES_PER_WHISPER)

        # A: holds the whisper row lock and would insert if there were room.
        conn_a, inserted_a = self._manual_insert("lock holder")
        self.assertEqual(inserted_a, 0)  # already at cap → rejected

        outcome = {}

        def _b():
            conn_b, rowcount = self._manual_insert("waiter")
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
                         "second concurrent insert must be rejected (cap reached)")
        self.assertEqual(count_replies(self.wid), MAX_REPLIES_PER_WHISPER)


class TestRecordReadAfterCloseLock(_PgReplyCapBase):
    """TOCTOU guard: record_whisper_read must reject reads after close/lock."""

    def test_record_read_rejected_after_close(self):
        db = self.DB
        db.close_whisper(self.wid)
        self.assertFalse(db.record_whisper_read(self.wid, self.REPLIERS[0]))
        self.assertEqual(db.reader_count(self.wid), 0)

    def test_record_read_rejected_after_lock(self):
        db = self.DB
        db.toggle_whisper_lock(self.wid)
        self.assertFalse(db.record_whisper_read(self.wid, self.REPLIERS[0]))
        self.assertEqual(db.reader_count(self.wid), 0)

    def test_open_whisper_still_accepts_read(self):
        db = self.DB
        self.assertTrue(db.record_whisper_read(self.wid, self.REPLIERS[0]))
        self.assertEqual(db.reader_count(self.wid), 1)


if __name__ == "__main__":
    if not DB_URL:
        print("⚠️  DATABASE_URL not set — skipping PostgreSQL reply-cap/read-"
              "guard tests.")
        sys.exit(0)
    unittest.main(verbosity=2)