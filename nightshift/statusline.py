"""Optional statusline hook (the claude-hud trick).

Claude Code >= 2.1 pipes a JSON payload to your statusline command on every
update; for subscribers it includes `rate_limits` with the same five_hour /
seven_day shape as the OAuth usage endpoint. We capture that to a snapshot
file (a zero-network quota source for the rest of nightshift) and print a
compact one-line status.

Enable it in ~/.claude/settings.json:
  {"statusLine": {"type": "command",
                  "command": "python -m nightshift statusline"}}
"""

from __future__ import annotations

import json
import sys
import time

from .config import DATA_DIR
from .quota import SNAPSHOT_PATH


def run_statusline() -> None:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        print("nightshift: no statusline payload")
        return

    rate_limits = payload.get("rate_limits") or {}
    if rate_limits:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_PATH.write_text(
            json.dumps({"ts": time.time(), "raw": rate_limits}),
            encoding="utf-8",
        )

    parts = []
    model = (payload.get("model") or {}).get("display_name")
    if model:
        parts.append(model)
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        block = rate_limits.get(key) or {}
        util = block.get("utilization")
        if util is not None:
            parts.append(f"{label} {util:.0f}%")
    cwd = (payload.get("workspace") or {}).get("current_dir", "")
    if cwd:
        parts.append(cwd.rsplit("\\", 1)[-1].rsplit("/", 1)[-1])
    print(" | ".join(parts) if parts else "nightshift")
