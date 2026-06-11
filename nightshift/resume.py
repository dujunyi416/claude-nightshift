"""Auto-resume sessions that were cut off (by the rate limit or otherwise).

Detecting an interruption is trickier than it sounds. Older Claude Code
versions appended an explicit marker when the limit hit mid-task:

    {"type": "assistant", "isApiErrorMessage": true,
     "message": {"content": [{"type": "text",
        "text": "You've hit your session limit · resets 4am (...)"}]}}

but the current app/CLI often does NOT write that marker for interactive
sessions - the transcript simply STOPS after the last assistant turn whose
`stop_reason` is "tool_use" (the model asked to run a tool and the turn
never completed, because the limit cut it off). So we detect interruptions
two ways:

  * high confidence: the trailing entry is an isApiErrorMessage limit marker
  * medium confidence: the session is cut off mid-action (last meaningful
    entry is an assistant `tool_use` with no following tool_result, or a
    tool_result with no following assistant turn) AND the file has been
    idle for a few minutes (so we never touch a session that's actively
    running right now - including this very one).

Resume continues each in its ORIGINAL working directory (Claude scopes
`--resume <id>` to the project of the cwd, so the cwd must match):

    claude -p --resume <sessionId> "<continue prompt>"

A state file records what we resumed, keyed by the interruption timestamp,
so the same cutoff is never resumed twice; if the resumed run hits the
limit again, a newer interruption timestamp lets the next reset pick it up
- the task crawls forward window by window until it finishes.
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


# Transcript entry types that are not real conversation turns.
_NON_TURN = {"summary", "ai-title", "mode", "permission-mode", "attachment",
             "file-history-snapshot", "last-prompt", "queue-operation"}


@dataclass
class InterruptedSession:
    session_id: str
    cwd: str
    interrupted_at: datetime
    error_text: str
    transcript: Path
    reason: str = "limit"          # "limit" | "stalled"
    confidence: str = "high"       # "high" | "medium"
    idle_min: float = 0.0


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


def _has_tool_use(entry: dict) -> bool:
    """True if an assistant entry contains a tool_use block (i.e. it asked
    to run a tool). Robust to both stop_reason and content inspection."""
    msg = entry.get("message") or {}
    if msg.get("stop_reason") == "tool_use":
        return True
    content = msg.get("content")
    if isinstance(content, list):
        return any(isinstance(b, dict) and b.get("type") == "tool_use"
                   for b in content)
    return False


def classify_transcript(path: Path, idle_min_threshold: float,
                        now: datetime | None = None) -> InterruptedSession | None:
    """Classify a single transcript; return an InterruptedSession if it
    looks cut off, else None. `idle_min_threshold` guards against touching
    sessions that are still actively running."""
    now = now or datetime.now(timezone.utc)
    try:
        mtime = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    except OSError:
        return None
    idle_min = (now - mtime).total_seconds() / 60
    if idle_min < idle_min_threshold:
        return None  # still active - never resume from under a live session

    entries = _tail_entries(path)
    if not entries:
        return None

    # Find the last real conversation turn (skip snapshots/titles/etc).
    last = None
    for entry in reversed(entries):
        if entry.get("type") in ("assistant", "user"):
            last = entry
            break
    if last is None:
        return None

    sid = last.get("sessionId") or path.stem
    cwd = last.get("cwd") or str(Path.home())
    when = _parse_ts(last) or mtime

    err = _entry_is_limit_error(last)
    if err:
        return InterruptedSession(sid, cwd, when, err[:120], path,
                                  reason="limit", confidence="high",
                                  idle_min=idle_min)

    # Cut off mid-action: assistant asked to run a tool but the turn never
    # completed, or a tool result came back with no assistant follow-up.
    if last.get("type") == "assistant" and _has_tool_use(last):
        return InterruptedSession(sid, cwd, when,
                                  "cut off mid-action (tool_use)", path,
                                  reason="stalled", confidence="medium",
                                  idle_min=idle_min)
    if last.get("type") == "user":
        content = (last.get("message") or {}).get("content")
        is_tool_result = isinstance(content, list) and any(
            isinstance(b, dict) and b.get("type") == "tool_result"
            for b in content)
        if is_tool_result:
            return InterruptedSession(sid, cwd, when,
                                      "cut off after tool result", path,
                                      reason="stalled", confidence="medium",
                                      idle_min=idle_min)
    return None


def scan_interrupted(lookback_hours: float = 48,
                     projects_dir: Path | None = None,
                     idle_min: float = 5.0,
                     detect_stalled: bool = True) -> list[InterruptedSession]:
    """Find recently-cut-off sessions (limit markers + mid-action stalls)."""
    root = projects_dir or CLAUDE_PROJECTS
    if not root.exists():
        return []
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)
    found = []
    for path in root.glob("*/*.jsonl"):
        try:
            if datetime.fromtimestamp(path.stat().st_mtime, timezone.utc) < cutoff:
                continue
        except OSError:
            continue
        info = classify_transcript(path, idle_min, now)
        if info is None:
            continue
        if info.reason == "stalled" and not detect_stalled:
            continue
        found.append(info)
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


def pending_resumes(lookback_hours: float = 48,
                    projects_dir: Path | None = None,
                    idle_min: float = 5.0,
                    detect_stalled: bool = True) -> list[InterruptedSession]:
    """Interrupted sessions we haven't resumed since their interruption."""
    state = _load_state()
    pending = []
    for s in scan_interrupted(lookback_hours, projects_dir, idle_min,
                              detect_stalled):
        last = state.get(s.session_id, 0)
        if last >= s.interrupted_at.timestamp():
            continue  # already resumed this particular cutoff
        pending.append(s)
    return pending


def resume_session(s: InterruptedSession, cfg: dict | None = None,
                   prompt: str = "") -> bool:
    cfg = cfg or load_config()
    rcfg = cfg["resume"]
    claude = find_claude_cmd(cfg)
    cmd = [claude, "-p", "--resume", s.session_id, prompt or rcfg["prompt"]]
    mode = rcfg.get("permission_mode", "")
    if mode:
        cmd += ["--permission-mode", mode]
    cwd = s.cwd if Path(s.cwd).is_dir() else str(Path.home())
    _log(f"resuming {s.session_id[:8]} ({s.reason}/{s.confidence}) in {cwd}")
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
        lines.append(f"  {s.session_id[:8]}  cut off {local} "
                     f"[{s.reason}/{s.confidence}, idle {s.idle_min:.0f}m]  "
                     f"cwd: {Path(s.cwd).name}")
        lines.append(f"           {s.error_text}")
    return "\n".join(lines)
