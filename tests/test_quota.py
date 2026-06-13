import os
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift import quota  # noqa: E402
from nightshift.credentials import OAuthCreds  # noqa: E402
from nightshift.quota import Window, parse_usage  # noqa: E402

SAMPLE = {
    "five_hour": {"utilization": 59.0,
                  "resets_at": "2099-06-10T21:40:00.861546+00:00"},
    "seven_day": {"utilization": 36.0,
                  "resets_at": "2099-06-13T17:00:00.861570+00:00"},
    "extra_usage": {"is_enabled": False},
}


class TestParseUsage(unittest.TestCase):
    def test_parses_both_windows(self):
        snap = parse_usage(SAMPLE)
        self.assertEqual(snap.five_hour.utilization, 59.0)
        self.assertEqual(snap.seven_day.utilization, 36.0)
        self.assertEqual(snap.five_hour.resets_at.tzinfo, timezone.utc)

    def test_missing_blocks_are_empty_windows(self):
        snap = parse_usage({})
        self.assertIsNone(snap.five_hour.utilization)
        self.assertFalse(snap.five_hour.active)

    def test_naive_timestamp_treated_as_utc(self):
        snap = parse_usage(
            {"five_hour": {"utilization": 10, "resets_at": "2099-01-01T00:00:00"}}
        )
        self.assertIsNotNone(snap.five_hour.resets_at.tzinfo)


class TestWindow(unittest.TestCase):
    def _future(self, hours=1):
        return datetime.now(timezone.utc) + timedelta(hours=hours)

    def test_active_window(self):
        w = Window(utilization=50, resets_at=self._future())
        self.assertTrue(w.active)
        self.assertFalse(w.exhausted)

    def test_exhausted(self):
        w = Window(utilization=100, resets_at=self._future())
        self.assertTrue(w.exhausted)

    def test_past_reset_means_idle(self):
        w = Window(utilization=99, resets_at=self._future(hours=-1))
        self.assertFalse(w.active)

    def test_zero_utilization_is_idle(self):
        w = Window(utilization=0, resets_at=self._future())
        self.assertFalse(w.active)

    def test_seconds_to_reset(self):
        w = Window(utilization=50, resets_at=self._future(hours=2))
        self.assertAlmostEqual(w.seconds_to_reset(), 7200, delta=5)
        self.assertIsNone(Window(None, None).seconds_to_reset())


class TestFetchUsageForceRaises(unittest.TestCase):
    """force=True must surface API failures instead of silently returning
    stale data — that masked the bug where the panel was 'stuck at 38%'."""

    def _fake_creds(self):
        return OAuthCreds(access_token="x", expires_at_ms=10**13,
                          subscription_type="pro", rate_limit_tier="standard")

    def test_force_raises_on_401_with_token_message(self):
        http_err = urllib.error.HTTPError(
            quota.USAGE_URL, 401, "Unauthorized", {}, BytesIO(b""))
        with patch.object(quota, "load_creds", return_value=self._fake_creds()), \
             patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(RuntimeError) as cm:
                quota.fetch_usage(force=True)
            self.assertIn("token", str(cm.exception))
            self.assertIn("401", str(cm.exception))

    def test_force_raises_on_429(self):
        http_err = urllib.error.HTTPError(
            quota.USAGE_URL, 429, "Too Many Requests", {}, BytesIO(b""))
        with patch.object(quota, "load_creds", return_value=self._fake_creds()), \
             patch("urllib.request.urlopen", side_effect=http_err):
            with self.assertRaises(RuntimeError) as cm:
                quota.fetch_usage(force=True)
            self.assertIn("429", str(cm.exception))

    def test_force_raises_on_network_error(self):
        with patch.object(quota, "load_creds", return_value=self._fake_creds()), \
             patch("urllib.request.urlopen",
                   side_effect=urllib.error.URLError("dns fail")):
            with self.assertRaises(RuntimeError) as cm:
                quota.fetch_usage(force=True)
            self.assertIn("网络", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
