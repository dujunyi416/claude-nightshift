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


if __name__ == "__main__":
    unittest.main()
