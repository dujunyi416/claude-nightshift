"""List recent Claude Code sessions (the same ones the app shows under
"Recents") so the panel can target follow-up prompts at a specific session.

Each session transcript starts with an {"type": "ai-title", "aiTitle": ...}
entry - exactly the title the desktop app displays. We combine that with
the tail of the file to get last-activity time, working directory, and
whether the session ended in a rate-limit cutoff.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .resume import CLAUDE_PROJECTS, _entry_is_limit_error, _parse_ts, _tail_entries


@dataclass
class SessionInfo:
    session_id: str
    title: str
    cwd: str
    last_active: datetime
    interrupted: bool
    error_text: str
    # Tiny throwaway sessions (warmup pings, one-liner tests) the panel
    # hides by default.
    trivial: bool = False


# Titles that mark our own machine-generated sessions.
_TRIVIAL_TITLES = ("reply with exactly", "say ok", "respond with confirmation")
_TRIVIAL_BYTES = 6 * 1024


def _head_info(path: Path, max_lines: int = 40) -> tuple[str, str, str]:
    """(session_id, title, fallback_cwd) from the first lines of a transcript."""
    session_id, title, cwd = path.stem, "", ""
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if i >= max_lines:
                    break
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if d.get("sessionId"):
                    session_id = d["sessionId"]
                if d.get("cwd") and not cwd:
                    cwd = d["cwd"]
                etype = d.get("type")
                if etype == "ai-title" and d.get("aiTitle"):
                    title = d["aiTitle"]
                elif etype == "summary" and d.get("summary") and not title:
                    title = d["summary"]
                elif etype == "user" and not title:
                    content = (d.get("message") or {}).get("content")
                    text = ""
                    if isinstance(content, str):
                        text = content
                    elif isinstance(content, list):
                        for block in content:
                            if isinstance(block, dict) and block.get("type") == "text":
                                text = block.get("text", "")
                                break
                    text = text.strip()
                    if text and not text.startswith("<"):
                        title = text[:80]
                if title and cwd:
                    break
    except OSError:
        pass
    return session_id, title, cwd


def list_recent_sessions(days: float = 7, limit: int = 20,
                         projects_dir: Path | None = None) -> list[SessionInfo]:
    root = projects_dir or CLAUDE_PROJECTS
    if not root.exists():
        return []
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    paths = []
    for p in root.glob("*/*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(p.stat().st_mtime, timezone.utc)
        except OSError:
            continue
        if mtime >= cutoff:
            paths.append((mtime, p))
    paths.sort(reverse=True)

    out = []
    for mtime, path in paths[:limit]:
        session_id, title, cwd = _head_info(path)
        last_active = mtime
        interrupted, error_text = False, ""
        entries = _tail_entries(path)
        for entry in reversed(entries):
            if entry.get("type") not in ("assistant", "user"):
                continue
            err = _entry_is_limit_error(entry)
            if err:
                interrupted, error_text = True, err[:100]
                last_active = _parse_ts(entry) or mtime
            if entry.get("cwd"):
                cwd = entry["cwd"]
            break
        if not title:
            title = f"(untitled) {session_id[:8]}"
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        trivial = size < _TRIVIAL_BYTES or any(
            title.lower().startswith(t) for t in _TRIVIAL_TITLES)
        out.append(SessionInfo(
            session_id=session_id, title=title,
            cwd=cwd or str(Path.home()),
            last_active=last_active,
            interrupted=interrupted, error_text=error_text,
            trivial=trivial,
        ))
    return out
