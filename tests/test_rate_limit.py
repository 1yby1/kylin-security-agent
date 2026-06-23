import os
import unittest
from unittest import mock

from backend.config import get_rate_limit_settings
from backend.security.auth import session_principal
from backend.security.rate_limit import ConcurrencyGate, RateLimiter, rate_limit_key


class RateLimiterTest(unittest.TestCase):
    def test_allows_up_to_limit_then_blocks(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=3, window_seconds=60, clock=lambda: clock[0])
        self.assertTrue(limiter.allow("k"))
        self.assertTrue(limiter.allow("k"))
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))

    def test_keys_are_independent(self):
        limiter = RateLimiter(limit_per_window=1, window_seconds=60, clock=lambda: 0.0)
        self.assertTrue(limiter.allow("a"))
        self.assertTrue(limiter.allow("b"))
        self.assertFalse(limiter.allow("a"))

    def test_window_slides(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=1, window_seconds=10, clock=lambda: clock[0])
        self.assertTrue(limiter.allow("k"))
        self.assertFalse(limiter.allow("k"))
        clock[0] = 11.0
        self.assertTrue(limiter.allow("k"))

    def test_retry_after_positive_when_limited(self):
        clock = [0.0]
        limiter = RateLimiter(limit_per_window=1, window_seconds=10, clock=lambda: clock[0])
        limiter.allow("k")
        self.assertFalse(limiter.allow("k"))
        self.assertGreater(limiter.retry_after("k"), 0)

    def test_bounds_distinct_key_memory(self):
        limiter = RateLimiter(limit_per_window=5, window_seconds=60, max_keys=3, clock=lambda: 0.0)
        for i in range(50):
            limiter.allow(f"ip:{i}")
        self.assertLessEqual(len(limiter._hits), 3)


class ConcurrencyGateTest(unittest.TestCase):
    def test_blocks_when_full_and_recovers(self):
        gate = ConcurrencyGate(2)
        self.assertTrue(gate.try_acquire())
        self.assertTrue(gate.try_acquire())
        self.assertFalse(gate.try_acquire())
        gate.release()
        self.assertTrue(gate.try_acquire())


class RateLimitKeyTest(unittest.TestCase):
    def test_token_uses_principal(self):
        self.assertEqual(rate_limit_key("tok", "1.2.3.4"), session_principal("tok"))

    def test_anon_uses_ip(self):
        self.assertEqual(rate_limit_key(None, "1.2.3.4"), "ip:1.2.3.4")
        self.assertEqual(rate_limit_key(None, None), "ip:unknown")


class RateLimitSettingsTest(unittest.TestCase):
    def test_defaults(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            settings = get_rate_limit_settings()
            self.assertTrue(settings.enabled)
            self.assertEqual(settings.per_minute, 30)
            self.assertEqual(settings.max_concurrent, 8)

    def test_env_override_and_disable(self):
        with mock.patch.dict(os.environ, {
            "AGENT_RATE_LIMIT_PER_MIN": "5",
            "AGENT_MAX_CONCURRENT": "2",
            "AGENT_RATE_LIMIT_ENABLED": "false",
        }, clear=True):
            settings = get_rate_limit_settings()
            self.assertFalse(settings.enabled)
            self.assertEqual(settings.per_minute, 5)
            self.assertEqual(settings.max_concurrent, 2)

    def test_upper_clamp(self):
        with mock.patch.dict(os.environ, {"AGENT_RATE_LIMIT_PER_MIN": "999999999", "AGENT_MAX_CONCURRENT": "999999999"}, clear=True):
            settings = get_rate_limit_settings()
            self.assertEqual(settings.per_minute, 100000)
            self.assertEqual(settings.max_concurrent, 4096)


if __name__ == "__main__":
    unittest.main()
