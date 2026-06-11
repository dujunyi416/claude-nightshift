import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("NIGHTSHIFT_HOME", tempfile.mkdtemp(prefix="ns_test_"))

from nightshift.sessions import list_recent_sessions  # noqa: E402


def _entry(ts, etype="assistant", text="", is_error=False, sid="sess-1",
           tool_use=False):
    if tool_use:
        content = [{"type": "tool_use", "name": "Bash", "input": {}}]
        stop = "tool_use"
    else:
        content = [{"type": "text", "text": text}]
        stop = "end_turn"
    return {
        "type": etype, "sessionId": sid, "cwd": "C:\\proj",
        "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        "isApiErrorMessage": is_error,
        "message": {"role": etype, "content": content, "stop_reason": stop},
    }


class TestSessions(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="ns_sess_"))
        self.now = datetime.now(timezone.utc)

    def _write(self, name, entries, title=None, age_min=30):
        proj = self.root / "proj"
        proj.mkdir(parents=True, exist_ok=True)
        lines = []
        if title:
            lines.append(json.dumps(
                {"type": "ai-title", "aiTitle": title, "sessionId": name}))
        lines += [json.dumps(e) for e in entries]
        path = proj / f"{name}.jsonl"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        old = time.time() - age_min * 60
        os.utime(path, (old, old))

    def test_title_from_ai_title(self):
        self._write("s1", [_entry(self.now, text="hi", sid="s1")],
                    title="Fix the data loader")
        got = list_recent_sessions(projects_dir=self.root)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0].title, "Fix the data loader")
        self.assertEqual(got[0].session_id, "s1")
        self.assertEqual(got[0].cwd, "C:\\proj")
        self.assertFalse(got[0].interrupted)

    def test_fallback_title_from_first_user_message(self):
        self._write("s2", [
            _entry(self.now, etype="user", text="please refactor utils",
                   sid="s2")])
        got = list_recent_sessions(projects_dir=self.root)
        self.assertIn("please refactor", got[0].title)

    def test_interrupted_flag(self):
        self._write("s3", [
            _entry(self.now - timedelta(hours=2), text="working", sid="s3"),
            _entry(self.now - timedelta(hours=1), is_error=True, sid="s3",
                   text="You've hit your session limit · resets 4am"),
        ], title="Long refactor")
        got = list_recent_sessions(projects_dir=self.root)
        self.assertTrue(got[0].interrupted)
        self.assertIn("session limit", got[0].error_text)

    def test_sorted_by_recency_and_limited(self):
        for i in range(5):
            self._write(f"s{i}", [_entry(self.now, sid=f"s{i}", text="x")],
                        title=f"T{i}")
        got = list_recent_sessions(limit=3, projects_dir=self.root)
        self.assertEqual(len(got), 3)


if __name__ == "__main__":
    unittest.main()
