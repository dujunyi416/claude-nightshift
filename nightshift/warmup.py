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
    """Send a minimal ping to start the 5h window. Returns True if pinged."""
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
                return False
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


if __name__ == "__main__":
    ok = warmup(force="--force" in sys.argv)
    sys.exit(0 if ok else 1)
