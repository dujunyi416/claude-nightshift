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
           is_error: bool = False, session_id: str = "sess-1",
           tool_use: bool = False, tool_result: bool = False) -> dict:
    if tool_use:
        content = [{"type": "tool_use", "name": "Bash", "input": {}}]
        stop = "tool_use"
    elif tool_result:
        content = [{"type": "tool_result", "content": "ok"}]
        stop = None
    else:
        content = [{"type": "text", "text": text}]
        stop = "end_turn"
    return {
        "type": etype,
        "sessionId": session_id,
        "cwd": "C:\\proj",
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "isApiErrorMessage": is_error,
        "message": {"role": etype, "content": content, "stop_reason": stop},
    }


def _write_transcript(root: Path, name: str, entries: list[dict],
                      age_min: float = 30) -> Path:
    """Write a transcript and backdate its mtime so the idle gate passes."""
    proj = root / "proj"
    proj.mkdir(parents=True, exist_ok=True)
    path = proj / f"{name}.jsonl"
    path.write_text(
        "\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8"
    )
    old = time.time() - age_min * 60
    os.utime(path, (old, old))
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
        found = resume.scan_interrupted(48, self.root, idle_min=5)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].session_id, "sess-1")
        self.assertEqual(found[0].reason, "limit")
        self.assertEqual(found[0].confidence, "high")

    def test_detects_stalled_mid_tool_use(self):
        # Current app behaviour: no limit marker, just stops at a tool_use.
        _write_transcript(self.root, "stall", [
            _entry(self.now - timedelta(hours=1), text="let me run that"),
            _entry(self.now - timedelta(minutes=50), tool_use=True),
        ])
        found = resume.scan_interrupted(48, self.root, idle_min=5)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0].reason, "stalled")
        self.assertEqual(found[0].confidence, "medium")

    def test_stalled_can_be_disabled(self):
        _write_transcript(self.root, "stall2", [
            _entry(self.now - timedelta(minutes=50), tool_use=True),
        ])
        self.assertEqual(
            resume.scan_interrupted(48, self.root, idle_min=5,
                                    detect_stalled=False), [])

    def test_idle_gate_protects_active_session(self):
        # Written just now (age 0) -> still active -> not flagged.
        _write_transcript(self.root, "live", [
            _entry(self.now, tool_use=True),
        ], age_min=0)
        self.assertEqual(resume.scan_interrupted(48, self.root, idle_min=5), [])

    def test_completed_session_not_flagged(self):
        _write_transcript(self.root, "done", [
            _entry(self.now - timedelta(hours=1), text="all finished, bye"),
        ])
        self.assertEqual(resume.scan_interrupted(48, self.root, idle_min=5), [])

    def test_ignores_session_that_moved_on(self):
        _write_transcript(self.root, "b", [
            _entry(self.now - timedelta(hours=3), is_error=True,
                   text="You've hit your session limit"),
            _entry(self.now - timedelta(hours=1), text="resumed and finished"),
        ])
        self.assertEqual(resume.scan_interrupted(48, self.root, idle_min=5), [])

    def test_ignores_non_limit_api_errors(self):
        _write_transcript(self.root, "c", [
            _entry(self.now - timedelta(hours=1), is_error=True,
                   text="API overloaded, please retry"),
        ])
        self.assertEqual(resume.scan_interrupted(48, self.root, idle_min=5), [])

    def test_ignores_old_interruptions(self):
        _write_transcript(self.root, "d", [
            _entry(self.now - timedelta(hours=50), is_error=True,
                   text="usage limit reached"),
        ], age_min=50 * 60)
        self.assertEqual(resume.scan_interrupted(48, self.root, idle_min=5), [])

    def test_pending_respects_state(self):
        _write_transcript(self.root, "e", [
            _entry(self.now - timedelta(hours=1), is_error=True,
                   text="You've hit your session limit", session_id="sess-9"),
        ])
        self.assertEqual(
            len(resume.pending_resumes(48, projects_dir=self.root,
                                       idle_min=5)), 1)
        resume._save_state({"sess-9": time.time()})
        self.assertEqual(
            resume.pending_resumes(48, projects_dir=self.root, idle_min=5), [])


if __name__ == "__main__":
    unittest.main()
