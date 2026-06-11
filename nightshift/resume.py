"""Auto-resume sessions that were cut off by the rate limit.

When Claude Code hits your 5h limit mid-task, it appends an entry like

    {"type": "assistant", "isApiErrorMessage": true, "apiErrorStatus": ...,
     "sessionId": "...", "cwd": "...",
     "message": {"content": [{"type": "text",
        "text": "You've hit your session limit · resets 4am (...)"}]}}

to the session transcript and stops. We scan recent transcripts for
sessions whose *tail* contains such an entry with no real progress after
it, then - once the window resets - continue each one headlessly:

    claude -p --resume <sessionId> "<continue prompt>"   (in its original cwd)

A state file remembers what we already resumed so the same interruption is
never resumed twice; if the resumed run hits the limit again, a fresh error
entry appears and the next reset picks it up again - the task keeps
crawling forward window by window until it finishes.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .config import DATA_DIR, find_claude_cmd, load_config
from .notify import notify

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"
RESUME_STATE = DATA_DIR / "resumed.json"
RESUME_LOG = DATA_DIR / "logs" / "resume.log"

# Substrings (lowercased) that mark a limit interruption, as opposed to
# other API errors (overloaded, billing, ...) we should not blindly retry.
LIMIT_TEXTS = ("hit your session limit", "usage limit", "rate limit",
               "limit reached", "hit your limit", "weekly limit")

TAIL_BYTES = 64 * 1024


def _log(msg: str) -> None:
    RESUME_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    with RESUME_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


@dataclass
class InterruptedSession:
    session_id: str
    cwd: str
    interrupted_at: datetime
    error_text: str
    transcript: Path


def _entry_is_limit_error(entry: dict) -> str | None:
    """Return the error text if this transcript entry is a limit cutoff."""
    if not entry.get("isApiErrorMessage"):
        return None
    content = (entry.get("message") or {}).get("content") or []
    if isinstance(content, str):
        texts = [content]
    else:
        texts = [c.get("text", "") for c in content if isinstance(c, dict)]
    blob = " ".join(texts)
    if any(marker in blob.lower() for marker in LIMIT_TEXTS):
        return blob.strip()
    return None


def _tail_entries(path: Path, max_bytes: int = TAIL_BYTES) -> list[dict]:
    try:
        with path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            raw = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    entries = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue  # first line may be a partial record from the seek
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries


def _parse_ts(entry: dict) -> datetime | None:
    ts = entry.get("timestamp")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def scan_interrupted(lookback_hours: float = 24,
                     projects_dir: Path | None = None) -> list[InterruptedSession]:
    """Find sessions whose last activity is a rate-limit cutoff."""
    root = projects_dir or CLAUDE_PROJECTS
    if not root.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    found = []
    for path in root.glob("*/*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            continue
        entries = _tail_entries(path)
        if not entries:
            continue
        # Walk backwards: the limit error must be the last *meaningful*
        # event. Anything substantive after it means the session already
        # moved on (e.g. the user resumed it interactively).
        for entry in reversed(entries):
            etype = entry.get("type")
            if etype not in ("assistant", "user"):
                continue  # summaries, file-history snapshots, etc.
            err = _entry_is_limit_error(entry)
            if err:
                when = _parse_ts(entry) or mtime
                if when < cutoff:
                    break
                found.append(InterruptedSession(
                    session_id=entry.get("sessionId") or path.stem,
                    cwd=entry.get("cwd") or str(Path.home()),
                    interrupted_at=when,
                    error_text=err[:120],
                    transcript=path,
                ))
            break  # only inspect the last meaningful entry
    found.sort(key=lambda s: s.interrupted_at)
    return found


def _load_state() -> dict:
    try:
        return json.loads(RESUME_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESUME_STATE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def pending_resumes(lookback_hours: float = 24,
                    projects_dir: Path | None = None) -> list[InterruptedSession]:
    """Interrupted sessions we haven't resumed since their interruption."""
    state = _load_state()
    pending = []
    for s in scan_interrupted(lookback_hours, projects_dir):
        last = state.get(s.session_id, 0)
        if last >= s.interrupted_at.timestamp():
            continue  # already resumed this particular cutoff
        pending.append(s)
    return pending


def resume_session(s: InterruptedSession, cfg: dict | None = None) -> bool:
    cfg = cfg or load_config()
    rcfg = cfg["resume"]
    claude = find_claude_cmd(cfg)
    cmd = [claude, "-p", "--resume", s.session_id, rcfg["prompt"]]
    mode = rcfg.get("permission_mode", "")
    if mode:
        cmd += ["--permission-mode", mode]
    cwd = s.cwd if Path(s.cwd).is_dir() else str(Path.home())
    _log(f"resuming {s.session_id[:8]} in {cwd}")
    _log(f"  (was: {s.error_text})")

    # Mark as attempted *before* running so a crash can't cause a loop.
    state = _load_state()
    state[s.session_id] = time.time()
    _save_state(state)

    timeout = cfg["runner"]["job_timeout_min"] * 60
    try:
        result = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        _log(f"  resume timed out after {timeout / 60:.0f} min")
        return False
    except OSError as e:
        _log(f"  failed to launch claude: {e}")
        return False

    log_path = DATA_DIR / "logs" / f"resume-{s.session_id[:8]}.log"
    log_path.write_text(
        f"=== exit {result.returncode} ===\n--- stdout ---\n{result.stdout}\n"
        f"--- stderr ---\n{result.stderr}", encoding="utf-8",
    )
    if result.returncode == 0:
        _log(f"  done (log: {log_path.name})")
        notify(f"[nightshift] resumed session finished: {s.session_id[:8]} "
               f"in {Path(cwd).name}")
        return True
    _log(f"  exit {result.returncode} (log: {log_path.name})")
    notify(f"[nightshift] resume FAILED for {s.session_id[:8]} "
           f"(exit {result.returncode})")
    return False


def format_pending(sessions: list[InterruptedSession]) -> str:
    if not sessions:
        return "no interrupted sessions found."
    lines = [f"{len(sessions)} interrupted session(s):"]
    for s in sessions:
        local = s.interrupted_at.astimezone().strftime("%m-%d %H:%M")
        lines.append(f"  {s.session_id[:8]}  cut off {local}  "
                     f"cwd: {Path(s.cwd).name}")
        lines.append(f"           {s.error_text}")
    return "\n".join(lines)
