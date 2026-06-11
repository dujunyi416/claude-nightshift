import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift.quota import parse_usage, weekly_projection  # noqa: E402
from nightshift.warmup import within_hours  # noqa: E402


def _snap(util: float, resets_in_h: float):
    resets = datetime.now(timezone.utc) + timedelta(hours=resets_in_h)
    return parse_usage({"seven_day": {"utilization": util,
                                      "resets_at": resets.isoformat()}})


class TestWeeklyProjection(unittest.TestCase):
    def test_halfway_at_half_budget_is_safe(self):
        # 84h elapsed (resets in 84h), 50% used -> exactly on pace, ~100%.
        p = weekly_projection(_snap(50.0, 84))
        self.assertIsNotNone(p)
        self.assertAlmostEqual(p["projected_at_reset"], 100, delta=2)
        self.assertIsNone(p["exhaust_at"])
        self.assertTrue(p["reliable"])

    def test_burning_too_fast_predicts_exhaustion(self):
        # 1 day in, 80% used -> will exhaust well before reset.
        p = weekly_projection(_snap(80.0, 144))
        self.assertIsNotNone(p["exhaust_at"])
        self.assertGreater(p["projected_at_reset"], 100)

    def test_fresh_window_marked_unreliable(self):
        p = weekly_projection(_snap(5.0, 167))  # 1h into the window
        self.assertFalse(p["reliable"])

    def test_no_data_returns_none(self):
        self.assertIsNone(weekly_projection(parse_usage({})))


class TestWithinHours(unittest.TestCase):
    def _at(self, h, m=0):
        return datetime(2026, 6, 11, h, m)

    def test_normal_range(self):
        self.assertTrue(within_hours("07:00", "23:00", self._at(12)))
        self.assertFalse(within_hours("07:00", "23:00", self._at(23, 30)))
        self.assertFalse(within_hours("07:00", "23:00", self._at(3)))

    def test_boundaries(self):
        self.assertTrue(within_hours("07:00", "23:00", self._at(7, 0)))
        self.assertFalse(within_hours("07:00", "23:00", self._at(23, 0)))

    def test_overnight_range(self):
        self.assertTrue(within_hours("22:00", "06:00", self._at(23)))
        self.assertTrue(within_hours("22:00", "06:00", self._at(2)))
        self.assertFalse(within_hours("22:00", "06:00", self._at(12)))

    def test_bad_format(self):
        self.assertFalse(within_hours("7am", "23:00", self._at(12)))


if __name__ == "__main__":
    unittest.main()
