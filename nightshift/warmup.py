"""Pre-warm the 5-hour rate-limit window.

The 5h window starts counting from your *first message*, not from when you
sit down. If you wake at 09:00 and a scheduled ping fires at 07:00, the
window already expires at 12:00 instead of 14:00 - you effectively reclaim
two hours of the morning.

The ping is one tiny haiku-model prompt: negligible quota cost.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime

from .config import DATA_DIR, find_claude_cmd, load_config
from .quota import fetch_usage

WARMUP_LOG = DATA_DIR / "logs" / "warmup.log"


def _log(msg: str) -> None:
    WARMUP_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    with WARMUP_LOG.open("a", encoding="utf-8") as f:
        f.write(f"[{stamp}] {msg}\n")
    print(msg)


def warmup(force: bool = False) -> bool:
    """Ensure the 5h window is active. Returns True on success - which
    includes the no-op case where a window is already active (the goal is
    met) - and False only when an actual ping attempt fails."""
    cfg = load_config()
    wcfg = cfg["warmup"]

    if wcfg.get("skip_if_active", True) and not force:
        try:
            usage = fetch_usage(force=True)
            if usage.five_hour.active:
                resets = usage.five_hour.resets_at
                local = resets.astimezone().strftime("%H:%M") if resets else "?"
                _log(
                    f"skip: 5h window already active "
                    f"({usage.five_hour.utilization:.0f}% used, resets {local})"
                )
                return True  # goal already met - not a failure
        except RuntimeError as e:
            _log(f"quota check failed ({e}); pinging anyway")

    claude = find_claude_cmd(cfg)
    cmd = [claude, "--model", wcfg["model"], "-p", wcfg["prompt"]]
    _log(f"pinging: {' '.join(cmd[1:])}")
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=180,
            encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        _log("ping timed out after 180s")
        return False
    except OSError as e:
        _log(f"failed to launch claude: {e}")
        return False

    if result.returncode == 0:
        _log(f"window activated. claude replied: {result.stdout.strip()[:80]}")
        return True
    _log(f"ping failed (exit {result.returncode}): {result.stderr.strip()[:200]}")
    return False


def within_hours(hhmm_start: str, hhmm_end: str,
                 now: datetime | None = None) -> bool:
    """Is `now` inside the [start, end) local-time range? Handles ranges
    that wrap past midnight (e.g. 22:00-06:00)."""
    now = now or datetime.now()
    try:
        sh, sm = map(int, hhmm_start.split(":"))
        eh, em = map(int, hhmm_end.split(":"))
    except ValueError:
        return False
    cur = now.hour * 60 + now.minute
    start, end = sh * 60 + sm, eh * 60 + em
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end


def maybe_keepwarm(last_attempt_ts: float) -> float:
    """Keep-warm tick: if enabled, inside awake hours, and the 5h window is
    idle, ping immediately so the new window starts the second the old one
    resets - not half an hour later when you notice.

    Called periodically by the tray app / watch loop with the timestamp of
    the previous attempt; returns the (possibly updated) timestamp.
    """
    import time as _time

    cfg = load_config()
    kw = cfg.get("keepwarm", {})
    if not kw.get("enabled"):
        return last_attempt_ts
    if not within_hours(kw.get("start", "07:00"), kw.get("end", "23:00")):
        return last_attempt_ts
    gap = kw.get("min_gap_min", 10) * 60
    if _time.time() - last_attempt_ts < gap:
        return last_attempt_ts
    try:
        usage = fetch_usage()
    except RuntimeError:
        return last_attempt_ts
    if usage.five_hour.active:
        return last_attempt_ts
    _log("keepwarm: window idle inside awake hours - pinging")
    warmup()  # warmup() re-checks and logs on its own
    return _time.time()


if __name__ == "__main__":
    ok = warmup(force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
