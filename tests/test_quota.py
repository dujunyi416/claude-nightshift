import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

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


if __name__ == "__main__":
    unittest.main()
