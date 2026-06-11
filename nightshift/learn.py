"""Learn your daily Claude rhythm from local transcripts and suggest a
warmup time.

Claude Code keeps one JSONL transcript per session under
~/.claude/projects/<project>/<session>.jsonl with a "timestamp" field on
each entry. We sample the first and last timestamp of every recent session
(cheap: head + tail of each file, never the whole transcript) and build an
hour-of-day activity histogram.

Heuristic for the suggestion: if you typically start at 09:00 and burn
through your quota in ~3 hours, warming the window 2h before your start
means it resets right when you'd otherwise hit the wall - so the default
suggestion is (typical first activity) minus (5h - typical session length),
clamped to at most 2h of lead.
"""

from __future__ import annotations

import json
import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLAUDE_PROJECTS = Path.home() / ".claude" / "projects"


@dataclass
class SessionSpan:
    start: datetime  # local time
    end: datetime


def _parse_ts(line: str) -> datetime | None:
    try:
        ts = json.loads(line).get("timestamp")
        if not ts:
            return None
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone()  # local
    except (json.JSONDecodeError, ValueError, AttributeError):
        return None


def _head_tail_ts(path: Path) -> tuple[datetime | None, datetime | None]:
    """First and last timestamp of a JSONL file without reading it all."""
    first = last = None
    try:
        with path.open("rb") as f:
            # head: scan first few lines for a timestamp
            for _ in range(20):
                line = f.readline()
                if not line:
                    break
                first = _parse_ts(line.decode("utf-8", errors="replace"))
                if first:
                    break
            # tail: read the last 16KB and scan backwards
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 16384))
            tail_lines = f.read().decode("utf-8", errors="replace").splitlines()
            for line in reversed(tail_lines):
                last = _parse_ts(line)
                if last:
                    break
    except OSError:
        pass
    return first, last


def collect_sessions(days: int = 30,
                     projects_dir: Path | None = None) -> list[SessionSpan]:
    root = projects_dir or CLAUDE_PROJECTS
    if not root.exists():
        return []
    cutoff = datetime.now().astimezone() - timedelta(days=days)
    spans = []
    for path in root.glob("*/*.jsonl"):
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime).astimezone()
        except OSError:
            continue
        if mtime < cutoff:
            continue
        start, end = _head_tail_ts(path)
        if start and end and end >= start:
            spans.append(SessionSpan(start=start, end=end))
    return spans


@dataclass
class Rhythm:
    days_observed: int
    sessions: int
    hour_histogram: dict[int, int]  # hour -> active-day count
    median_first_activity: float | None  # hour as float, e.g. 9.5
    median_session_hours: float | None
    suggested_warmup: str | None  # "HH:MM"


def analyze(spans: list[SessionSpan], max_lead_hours: float = 2.0) -> Rhythm:
    if not spans:
        return Rhythm(0, 0, {}, None, None, None)

    # Activity histogram: which hours of the day you're typically working.
    hour_hits: dict[int, set] = {h: set() for h in range(24)}
    first_by_day: dict[str, datetime] = {}
    durations = []
    for s in spans:
        day = s.start.strftime("%Y-%m-%d")
        if day not in first_by_day or s.start < first_by_day[day]:
            first_by_day[day] = s.start
        hours = (s.end - s.start).total_seconds() / 3600
        if hours <= 16:  # ignore always-on background sessions in the stats
            durations.append(hours)
        end = min(s.end, s.start + timedelta(hours=16))
        t = s.start.replace(minute=0, second=0, microsecond=0)
        while t <= end:
            hour_hits[t.hour].add(t.strftime("%Y-%m-%d"))
            t += timedelta(hours=1)

    firsts = [d.hour + d.minute / 60 for d in first_by_day.values()]
    median_first = statistics.median(firsts)
    median_len = statistics.median(durations) if durations else None

    # Lead time: enough that the warmed window resets when you'd hit the
    # wall, but never more than max_lead_hours (a too-early ping wastes the
    # warmed window entirely if you sleep in).
    burn = min(median_len or 3.0, 5.0)
    lead = min(max(5.0 - burn, 0.5), max_lead_hours)
    warm_at = median_first - lead
    if warm_at < 0:
        warm_at += 24
    hh, mm = int(warm_at), int(round((warm_at % 1) * 60 / 15) * 15) % 60
    suggested = f"{hh:02d}:{mm:02d}"

    return Rhythm(
        days_observed=len(first_by_day),
        sessions=len(spans),
        hour_histogram={h: len(d) for h, d in hour_hits.items() if d},
        median_first_activity=median_first,
        median_session_hours=median_len,
        suggested_warmup=suggested,
    )


def format_rhythm(r: Rhythm) -> str:
    if r.sessions == 0:
        return "no recent sessions found under ~/.claude/projects."
    lines = [
        f"Observed {r.sessions} sessions across {r.days_observed} active days.",
        "",
        "Activity by hour (days you were active in that hour):",
    ]
    peak = max(r.hour_histogram.values(), default=1)
    for h in range(24):
        n = r.hour_histogram.get(h, 0)
        bar = "#" * round(n / peak * 40)
        lines.append(f"  {h:02d}:00 {bar}{' ' if bar else ''}{n if n else ''}")
    mf = r.median_first_activity or 0
    lines += [
        "",
        f"Typical first activity: {int(mf):02d}:{int((mf % 1) * 60):02d}",
        f"Typical session length: {r.median_session_hours:.1f}h"
        if r.median_session_hours else "",
        f"Suggested warmup time:  {r.suggested_warmup}",
        "",
        f"Apply it with:  nightshift schedule --warmup {r.suggested_warmup}",
    ]
    return "\n".join(line for line in lines if line is not None)
