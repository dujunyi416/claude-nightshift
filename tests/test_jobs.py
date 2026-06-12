import os
import shutil
import tempfile
import unittest

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift import jobs  # noqa: E402


class TestJobs(unittest.TestCase):
    def setUp(self):
        for d in (jobs.QUEUE_DIR, jobs.DONE_DIR, jobs.FAILED_DIR):
            shutil.rmtree(d, ignore_errors=True)

    def test_add_and_load(self):
        jobs.new_job("do the thing", cwd=".")
        loaded = jobs.load_jobs()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].prompt, "do the thing")

    def test_priority_ordering(self):
        jobs.new_job("later", cwd=".", priority=9)
        jobs.new_job("first", cwd=".", priority=1)
        jobs.new_job("middle", cwd=".", priority=5)
        prompts = [j.prompt for j in jobs.load_jobs()]
        self.assertEqual(prompts, ["first", "middle", "later"])

    def test_remove_by_prefix(self):
        job = jobs.new_job("temp", cwd=".")
        self.assertTrue(jobs.remove_job(job.id[:12]))
        self.assertEqual(jobs.load_jobs(), [])
        self.assertFalse(jobs.remove_job("nonexistent"))

    def test_archive_success_and_failure(self):
        ok_job = jobs.new_job("works", cwd=".")
        bad_job = jobs.new_job("breaks", cwd=".")
        jobs.archive_job(ok_job, success=True, log_text="fine")
        jobs.archive_job(bad_job, success=False, log_text="boom")
        self.assertEqual(jobs.load_jobs(), [])
        self.assertTrue((jobs.DONE_DIR / f"{ok_job.id}.json").exists())
        self.assertTrue((jobs.DONE_DIR / f"{ok_job.id}.log").exists())
        self.assertTrue((jobs.FAILED_DIR / f"{bad_job.id}.json").exists())
        self.assertEqual(
            (jobs.FAILED_DIR / f"{bad_job.id}.log").read_text(encoding="utf-8"),
            "boom",
        )

    def test_malformed_file_is_skipped(self):
        jobs.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        (jobs.QUEUE_DIR / "bad.json").write_text("{not json", encoding="utf-8")
        jobs.new_job("good", cwd=".")
        self.assertEqual(len(jobs.load_jobs()), 1)

    def test_get_and_update_job(self):
        job = jobs.new_job("original", cwd=".")
        got = jobs.get_job(job.id[:12])
        self.assertIsNotNone(got)
        self.assertEqual(got.id, job.id)
        self.assertTrue(jobs.update_job(job.id, prompt="edited"))
        self.assertEqual(jobs.get_job(job.id).prompt, "edited")
        self.assertFalse(jobs.update_job("nope", prompt="x"))

    def test_pause_and_skip(self):
        job = jobs.new_job("pausable", cwd=".")
        self.assertTrue(jobs.set_paused(job.id, True))
        self.assertTrue(jobs.load_jobs()[0].paused)
        self.assertTrue(jobs.set_paused(job.id, False))
        self.assertFalse(jobs.load_jobs()[0].paused)

    def test_pin_moves_to_front(self):
        jobs.new_job("a", cwd=".")
        jobs.new_job("b", cwd=".")
        last = jobs.new_job("c", cwd=".")
        self.assertNotEqual(jobs.load_jobs()[0].id, last.id)
        self.assertTrue(jobs.pin_job(last.id))
        self.assertEqual(jobs.load_jobs()[0].id, last.id)

    def test_reorder_jobs(self):
        a = jobs.new_job("a", cwd=".")
        b = jobs.new_job("b", cwd=".")
        c = jobs.new_job("c", cwd=".")
        jobs.reorder_jobs([c.id, a.id, b.id])
        self.assertEqual([j.id for j in jobs.load_jobs()], [c.id, a.id, b.id])

    def test_order_defaults_to_creation_and_persists(self):
        job = jobs.new_job("x", cwd=".")
        # Unset order resolves to created_at and survives a round-trip.
        self.assertEqual(job.order, job.created_at)
        self.assertEqual(jobs.get_job(job.id).order, job.created_at)

    def test_reorder_to_front_uses_zero_order(self):
        a = jobs.new_job("a", cwd=".")
        b = jobs.new_job("b", cwd=".")
        # order 0.0 must be respected, not treated as "unset".
        jobs.reorder_jobs([b.id, a.id])
        self.assertEqual(jobs.get_job(b.id).order, 0.0)
        self.assertEqual([j.id for j in jobs.load_jobs()], [b.id, a.id])

    def test_old_file_without_order_field_loads(self):
        import json as _json
        jobs.QUEUE_DIR.mkdir(parents=True, exist_ok=True)
        legacy = {"id": "20200101-000000-aaaaaa", "prompt": "legacy",
                  "cwd": ".", "created_at": 1577836800.0}
        (jobs.QUEUE_DIR / f"{legacy['id']}.json").write_text(
            _json.dumps(legacy), encoding="utf-8")
        loaded = jobs.load_jobs()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0].order, 1577836800.0)

    def test_running_marker_roundtrip(self):
        jobs.clear_running()
        self.assertIsNone(jobs.get_running())
        self.assertIn("没有任务", jobs.format_running())
        job = jobs.new_job("a long running task", cwd=".")
        jobs.set_running(job)
        r = jobs.get_running()
        self.assertEqual(r["id"], job.id)
        self.assertIn("跑步中", jobs.format_running())
        jobs.clear_running()
        self.assertIsNone(jobs.get_running())

    def test_short_dir_hides_absolute_path(self):
        self.assertEqual(jobs.short_dir("C:/Users/me/proj/btc_quant"),
                         "…/btc_quant")
        self.assertNotIn("Users", jobs.short_dir("/home/secret/x/y"))


if __name__ == "__main__":
    unittest.main()
