import os
import tempfile
import unittest
from datetime import datetime, timedelta

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift.learn import SessionSpan, analyze  # noqa: E402


def _span(day: int, start_h: float, hours: float) -> SessionSpan:
    base = datetime(2026, 6, day, 0, 0).astimezone()
    start = base + timedelta(hours=start_h)
    return SessionSpan(start=start, end=start + timedelta(hours=hours))


class TestAnalyze(unittest.TestCase):
    def test_empty(self):
        r = analyze([])
        self.assertEqual(r.sessions, 0)
        self.assertIsNone(r.suggested_warmup)

    def test_consistent_morning_user(self):
        # Starts at 09:00 every day, ~3h sessions -> warmup 2h before.
        spans = [_span(d, 9.0, 3.0) for d in range(1, 8)]
        r = analyze(spans)
        self.assertEqual(r.days_observed, 7)
        self.assertEqual(r.suggested_warmup, "07:00")

    def test_short_sessions_cap_lead_at_max(self):
        # 0.5h sessions would imply a 4.5h lead; capped to 2h max.
        spans = [_span(d, 10.0, 0.5) for d in range(1, 6)]
        r = analyze(spans)
        self.assertEqual(r.suggested_warmup, "08:00")

    def test_background_marathon_does_not_skew_length(self):
        spans = [_span(d, 9.0, 3.0) for d in range(1, 8)]
        spans.append(_span(8, 9.0, 60.0))  # 2.5-day autonomous run
        r = analyze(spans)
        self.assertEqual(r.median_session_hours, 3.0)

    def test_histogram_covers_session_hours(self):
        r = analyze([_span(1, 9.0, 3.0)])
        for h in (9, 10, 11, 12):
            self.assertIn(h, r.hour_histogram)
        self.assertNotIn(14, r.hour_histogram)


if __name__ == "__main__":
    unittest.main()
