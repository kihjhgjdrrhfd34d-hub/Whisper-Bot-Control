"""
tests/test_core.py
Tests for core infrastructure: events, rate_limiter, health, config_validator, plugins.
"""
import os
import sys
import unittest
import time

import tempfile, atexit as _ate
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_PATH"] = _tmpdb
_ate.register(lambda: __import__("os").path.exists(_tmpdb) and __import__("os").unlink(_tmpdb))
os.environ["BOT_TOKEN"]     = "0:test_token_placeholder"  # valid enough for tests
os.environ["ADMIN_IDS"]     = "999"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestEventBus(unittest.TestCase):
    def setUp(self):
        from core.events import EventBus
        self.bus = EventBus()  # fresh instance per test

    def test_subscribe_and_fire(self):
        results = []
        self.bus.subscribe("test_event", lambda **kw: results.append(kw))
        self.bus.fire("test_event", foo="bar")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["foo"], "bar")

    def test_multiple_subscribers(self):
        count = []
        self.bus.subscribe("multi", lambda **kw: count.append(1))
        self.bus.subscribe("multi", lambda **kw: count.append(2))
        self.bus.fire("multi")
        self.assertEqual(count, [1, 2])

    def test_unsubscribe(self):
        results = []
        handler = lambda **kw: results.append(1)
        self.bus.subscribe("unsub", handler)
        self.bus.unsubscribe("unsub", handler)
        self.bus.fire("unsub")
        self.assertEqual(len(results), 0)

    def test_handler_exception_does_not_crash(self):
        def bad(**kw):
            raise RuntimeError("handler error")
        self.bus.subscribe("safe", bad)
        # Should not raise
        try:
            self.bus.fire("safe")
        except Exception:
            self.fail("EventBus.fire() raised an exception despite error isolation")

    def test_fire_no_subscribers(self):
        # Should not raise
        self.bus.fire("no_subscribers_event")

    def test_canonical_event_names(self):
        from core.events import event_bus
        for attr in ["ON_WHISPER_CREATED", "ON_WHISPER_READ", "ON_USER_JOIN",
                     "ON_USER_BANNED", "ON_REPORT_CREATED", "ON_XP_AWARDED",
                     "ON_LEVEL_UP", "ON_ACHIEVEMENT", "ON_BACKUP_CREATED",
                     "ON_SPAM_DETECTED"]:
            self.assertTrue(hasattr(event_bus, attr), f"Missing event name: {attr}")


class TestRateLimiter(unittest.TestCase):
    def setUp(self):
        from core.rate_limiter import RateLimiter
        self.rl = RateLimiter()  # fresh instance per test

    def test_allow_within_limit(self):
        allowed, reason = self.rl.check(1, "message")
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_flood_detection(self):
        from core.rate_limiter import DEFAULT_LIMITS
        max_calls, window = DEFAULT_LIMITS["message"]
        # Fill the window
        for _ in range(max_calls):
            self.rl.check(2, "message")
        # Next call should be blocked
        allowed, reason = self.rl.check(2, "message")
        self.assertFalse(allowed)
        self.assertIn("flood", reason)

    def test_temp_ban_after_flood(self):
        from core.rate_limiter import DEFAULT_LIMITS
        max_calls, _ = DEFAULT_LIMITS["callback"]
        for _ in range(max_calls + 1):
            self.rl.check(3, "callback")
        remaining = self.rl.temp_ban_remaining(3)
        self.assertGreater(remaining, 0)

    def test_clear_user(self):
        from core.rate_limiter import DEFAULT_LIMITS
        max_calls, _ = DEFAULT_LIMITS["message"]
        for _ in range(max_calls + 1):
            self.rl.check(4, "message")
        self.rl.clear_user(4)
        allowed, _ = self.rl.check(4, "message")
        self.assertTrue(allowed)

    def test_spam_score(self):
        score = self.rl.add_spam_score(5, 20)
        self.assertEqual(score, 20)
        self.assertEqual(self.rl.get_spam_score(5), 20)

    def test_is_spammer(self):
        from core.rate_limiter import SPAM_SCORE_THRESHOLD
        self.rl.add_spam_score(6, SPAM_SCORE_THRESHOLD)
        self.assertTrue(self.rl.is_spammer(6))

    def test_active_temp_bans(self):
        from core.rate_limiter import DEFAULT_LIMITS
        max_calls, _ = DEFAULT_LIMITS["message"]
        for _ in range(max_calls + 1):
            self.rl.check(7, "message")
        bans = self.rl.active_temp_bans()
        self.assertIn(7, bans)

    def test_top_spammers(self):
        self.rl.add_spam_score(101, 100)
        self.rl.add_spam_score(102, 50)
        top = self.rl.top_spammers(2)
        self.assertEqual(top[0][0], 101)


class TestHealth(unittest.TestCase):
    def test_health_check_returns_dict(self):
        import database as db
        db.init_db()
        from core.health import health_check
        result = health_check()
        self.assertIn("status", result)
        self.assertIn("uptime", result)
        self.assertIn("database", result)
        self.assertIn("scheduler", result)

    def test_uptime(self):
        from core.health import uptime_seconds, uptime_str
        self.assertGreaterEqual(uptime_seconds(), 0)
        s = uptime_str()
        self.assertIn("h", s)
        self.assertIn("m", s)

    def test_metrics(self):
        import database as db
        db.init_db()
        from core.health import metrics
        m = metrics()
        self.assertIn("uptime_seconds", m)
        self.assertIn("temp_bans_active", m)


class TestConfigValidator(unittest.TestCase):
    def test_valid_config_no_raise(self):
        os.environ["BOT_TOKEN"] = "123456:ABCDEFghijklmnop"
        os.environ["ADMIN_IDS"] = "999"
        from core.config_validator import validate_config
        try:
            warnings = validate_config()
            self.assertIsInstance(warnings, list)
        except Exception as exc:
            self.fail(f"validate_config raised unexpectedly: {exc}")

    def test_invalid_token_raises(self):
        os.environ["BOT_TOKEN"] = "YOUR_BOT_TOKEN_HERE"
        from core.config_validator import validate_config, ConfigError
        with self.assertRaises(ConfigError):
            validate_config()
        # Restore
        os.environ["BOT_TOKEN"] = "0:test_token_placeholder"  # valid enough for tests

    def test_missing_token_raises(self):
        saved = os.environ.pop("BOT_TOKEN", None)
        # Default falls back to placeholder
        os.environ["BOT_TOKEN"] = ""
        from core.config_validator import validate_config, ConfigError
        with self.assertRaises(ConfigError):
            validate_config()
        if saved:
            os.environ["BOT_TOKEN"] = saved
        else:
            os.environ["BOT_TOKEN"] = "0:test_token_placeholder"  # valid enough for tests


class TestPluginManager(unittest.TestCase):
    def test_discover_empty_dir(self):
        """Discovering an empty plugins dir should not crash."""
        from core.plugins import PluginManager
        import tempfile
        pm = PluginManager()
        with tempfile.TemporaryDirectory() as tmpdir:
            pm.discover(plugins_dir=tmpdir, bot=None)
            self.assertEqual(pm.list_plugins(), [])

    def test_list_and_disable(self):
        from core.plugins import PluginManager, BasePlugin
        pm = PluginManager()
        # Manually load a dummy plugin
        class DummyPlugin(BasePlugin):
            name = "dummy"
            version = "1.0.0"
            description = "Test plugin"
            def load(self, bot=None):
                pass
        pm._load_plugin(DummyPlugin, bot=None)
        self.assertEqual(len(pm.list_plugins()), 1)
        result = pm.disable("dummy")
        self.assertTrue(result)

    def test_disable_unknown(self):
        from core.plugins import PluginManager
        pm = PluginManager()
        result = pm.disable("nonexistent_plugin")
        self.assertFalse(result)


class TestProtection(unittest.TestCase):
    def setUp(self):
        import database as db
        db.init_db()

    def test_check_user_ok(self):
        import database as db
        db.upsert_user(20001, "ok_user", "OK", None)
        from enterprise.protection import check_user
        allowed, reason = check_user(20001, "message")
        self.assertTrue(allowed)
        self.assertEqual(reason, "ok")

    def test_check_user_banned(self):
        import database as db
        db.upsert_user(20002, "banned", "Banned", None)
        db.ban_user(20002)
        from enterprise.protection import check_user
        allowed, reason = check_user(20002, "message")
        self.assertFalse(allowed)
        self.assertEqual(reason, "permanently_banned")

    def test_clear_user_restrictions(self):
        from enterprise.protection import clear_user_restrictions
        # Should not raise
        clear_user_restrictions(99999)

    def test_get_protection_status(self):
        from enterprise.protection import get_protection_status
        status = get_protection_status(99998)
        self.assertIn("spam_score", status)
        self.assertIn("is_spammer", status)
        self.assertIn("temp_ban_remaining", status)


if __name__ == "__main__":
    unittest.main(verbosity=2)
