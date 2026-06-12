import os
import shutil
import tempfile
import types
import unittest

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift import jobs, tgbot  # noqa: E402


def _sess(session_id, title, cwd="C:/proj/btc_quant"):
    return types.SimpleNamespace(session_id=session_id, title=title, cwd=cwd)


class TestTgPrefix(unittest.TestCase):
    def setUp(self):
        for d in (jobs.QUEUE_DIR,):
            shutil.rmtree(d, ignore_errors=True)
        tgbot._PENDING.clear()

    def test_prefix_regex(self):
        m = tgbot._PREFIX_RE.match("btc: 把回测补全")
        self.assertEqual(m.group(1), "btc")
        self.assertEqual(m.group(2), "把回测补全")
        # full-width colon also works
        self.assertTrue(tgbot._PREFIX_RE.match("cboe：跑测试"))
        # a long leading phrase with spaces is not treated as a prefix
        self.assertIsNone(tgbot._PREFIX_RE.match("please go and: do it"))

    def test_single_match_queues_resume(self):
        tgbot._match_sessions = lambda kw: [_sess("sess-1", "btc backtest")]
        reply = tgbot._handle("btc: 把回测补全")
        self.assertIn("续写", reply)
        loaded = jobs.load_jobs()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].session_id, "sess-1")
        self.assertEqual(loaded[0].prompt, "把回测补全")

    def test_multi_match_then_number_selects(self):
        tgbot._match_sessions = lambda kw: [
            _sess("s1", "btc one"), _sess("s2", "btc two")]
        reply = tgbot._handle("btc: do it")
        self.assertIn("匹到 2 个", reply)
        self.assertEqual(jobs.load_jobs(), [])  # nothing queued yet
        # the follow-up bare number picks one
        reply2 = tgbot._handle("2")
        self.assertIn("续写", reply2)
        loaded = jobs.load_jobs()
        self.assertEqual(loaded[0].session_id, "s2")
        self.assertEqual(loaded[0].prompt, "do it")

    def test_no_match_falls_through_to_new_job(self):
        tgbot._match_sessions = lambda kw: []
        reply = tgbot._handle("nope: this is a fresh task")
        self.assertIn("排为任务", reply)
        loaded = jobs.load_jobs()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].session_id, "")
        # the whole line (including the prefix) is the job prompt
        self.assertEqual(loaded[0].prompt, "nope: this is a fresh task")

    def test_running_command(self):
        jobs.clear_running()
        self.assertIn("没有任务", tgbot._handle("/running"))


if __name__ == "__main__":
    unittest.main()
