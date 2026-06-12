import os
import tempfile
import unittest

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift import runner  # noqa: E402
from nightshift.jobs import Job  # noqa: E402


def _job(prompt, cwd=".", session_id="", paused=False, order=0.0):
    return Job(id=prompt, prompt=prompt, cwd=cwd, session_id=session_id,
               paused=paused, order=order)


class TestBatch(unittest.TestCase):
    def test_skips_paused_head(self):
        jobs = [_job("a", paused=True), _job("b")]
        batch = runner._next_batch(jobs, merge=False)
        self.assertEqual([j.id for j in batch], ["b"])

    def test_all_paused_yields_nothing(self):
        jobs = [_job("a", paused=True)]
        self.assertEqual(runner._next_batch(jobs, merge=True), [])

    def test_no_merge_returns_single_head(self):
        jobs = [_job("a"), _job("b")]
        batch = runner._next_batch(jobs, merge=False)
        self.assertEqual([j.id for j in batch], ["a"])

    def test_merges_same_cwd_fresh_sessions(self):
        other = tempfile.mkdtemp(prefix="ns_other_")
        jobs = [_job("a"), _job("b"), _job("c", cwd=other)]
        batch = runner._next_batch(jobs, merge=True)
        # a and b share cwd ".", c is elsewhere -> only a,b merge.
        self.assertEqual([j.id for j in batch], ["a", "b"])

    def test_session_bound_head_never_merges(self):
        jobs = [_job("a", session_id="sess"), _job("b")]
        batch = runner._next_batch(jobs, merge=True)
        self.assertEqual([j.id for j in batch], ["a"])

    def test_session_bound_excluded_from_merge(self):
        jobs = [_job("a"), _job("b", session_id="sess"), _job("c")]
        batch = runner._next_batch(jobs, merge=True)
        self.assertEqual([j.id for j in batch], ["a", "c"])

    def test_merge_prompt_numbers_each_task(self):
        text = runner._merge_prompt([_job("first"), _job("second")])
        self.assertIn("2 个任务", text)
        self.assertIn("1) first", text)
        self.assertIn("2) second", text)


if __name__ == "__main__":
    unittest.main()
