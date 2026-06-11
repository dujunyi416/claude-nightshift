import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift import resume  # noqa: E402


def _entry(ts: datetime, etype: str = "assistant", text: str = "",
           is_error: bool = False, session_id: str = "sess-1") -> dict:
    return {
        "type": etype,
        "sessionId": session_id,
        "cwd": "C:\\proj",
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "isApiErrorMessage": is_error,
        "message": {"role": etype, "content": [{"type": "text", "text": text}]},
    }


def _write_transcript(root: Path, name: str, entries: list[dict]) -> Path:
    proj = root / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{name}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    return path


class TestScanInterrupted(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ns_projects_"))
        self.now = datetime.now(timezone.utc)
        resume.RESUME_STATE.unlink(missing_ok=True)

    def test_detects_limit_cutoff(self):
        _write_transcript(self.root, "a", [
            _entry(self.now - timedelta(hours=2), text="working on it"),
            _entry(self.now - timedelta(hours=1), is_error=True,
                   text="You've hit your session limit · resets 4am"),
        ])
        found = resume.scan_interrupted(24, self.root)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].session_id, "sess-1")
        self.assertEqual(found[0].cwd, "C:\\proj")

    def test_ignores_session_that_moved_on(self):
        _write_transcript(self.root, "b", [
            _entry(self.now - timedelta(hours=3), is_error=True,
                   text="You've hit your session limit"),
            _entry(self.now - timedelta(hours=1), text="resumed and finished"),
        ])
        self.assertEqual(resume.scan_interrupted(24, self.root), [])

    def test_ignores_non_limit_api_errors(self):
        _write_transcript(self.root, "c", [
            _entry(self.now - timedelta(hours=1), is_error=True,
                   text="API overloaded, please retry"),
        ])
        self.assertEqual(resume.scan_interrupted(24, self.root), [])

    def test_ignores_old_interruptions(self):
        path = _write_transcript(self.root, "d", [
            _entry(self.now - timedelta(hours=50), is_error=True,
                   text="usage limit reached"),
        ])
        old = time.time() - 50 * 3600
        os.utime(path, (old, old))
        self.assertEqual(resume.scan_interrupted(24, self.root), [])

    def test_pending_respects_state(self):
        _write_transcript(self.root, "e", [
            _entry(self.now - timedelta(hours=1), is_error=True,
                   text="You've hit your session limit", session_id="sess-9"),
        ])
        self.assertEqual(
            len(resume.pending_resumes(24, projects_dir=self.root)), 1
        )
        resume._save_state({"sess-9": time.time()})
        self.assertEqual(
            resume.pending_resumes(24, projects_dir=self.root), []
        )


if __name__ == "__main__":
    unittest.main()
