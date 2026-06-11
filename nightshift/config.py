"""Configuration: defaults merged with ~/.nightshift/config.json."""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

DATA_DIR = Path(os.environ.get("NIGHTSHIFT_HOME", Path.home() / ".nightshift"))
CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS: dict[str, Any] = {
    # Command used to invoke Claude Code. On Windows npm installs claude.cmd.
    "claude_cmd": "",  # empty = auto-detect
    "warmup": {
        # Daily time(s) to pre-warm the 5h window, 24h "HH:MM" local time.
        "times": ["07:00"],
        "model": "haiku",
        "prompt": "Reply with exactly: ok",
        # Don't send a ping if a window is already active (it would be wasted).
        "skip_if_active": True,
    },
    "runner": {
        # "reset" = wait for the current 5h window to reset before running jobs.
        # "now"   = start immediately if quota is available.
        "start_when": "reset",
        # Pause the queue when 5h utilization reaches this percentage.
        "stop_utilization": 95,
        "job_timeout_min": 240,
        # Permission mode passed to `claude -p`. "acceptEdits" lets jobs edit
        # files without prompting but still blocks risky shell commands.
        # Use "bypassPermissions" only if you understand the risk.
        "permission_mode": "acceptEdits",
        # Seconds between quota checks while waiting for a reset.
        "poll_interval_sec": 300,
    },
    "resume": {
        # Auto-resume sessions that were cut off by the rate limit.
        "enabled": True,
        # Only consider interruptions newer than this.
        "lookback_hours": 24,
        "prompt": (
            "You were interrupted by the usage limit. Continue from where "
            "you left off and finish the remaining work."
        ),
        "permission_mode": "acceptEdits",
        # Safety cap per watch cycle.
        "max_sessions": 3,
    },
    "quota": {
        # Cache usage API responses for this long to avoid hammering the
        # (undocumented) endpoint. claude-code-statusline uses 300s.
        "cache_ttl_sec": 60,
    },
    "gui": {
        # Local web panel port (127.0.0.1 only).
        "port": 8377,
    },
    "telegram": {
        # Optional: notify when overnight jobs finish. Leave empty to disable.
        # Can also be set via env vars NIGHTSHIFT_TG_TOKEN / NIGHTSHIFT_TG_CHAT.
        "bot_token": "",
        "chat_id": "",
    },
}


def _merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> dict[str, Any]:
    cfg = DEFAULTS
    if CONFIG_PATH.exists():
        try:
            user_cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            cfg = _merge(DEFAULTS, user_cfg)
        except (json.JSONDecodeError, OSError) as e:
            print(f"warning: could not read {CONFIG_PATH}: {e}", file=sys.stderr)
    return cfg


def save_config(cfg: dict[str, Any]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def ensure_dirs() -> None:
    for sub in ("queue", "done", "failed", "logs"):
        (DATA_DIR / sub).mkdir(parents=True, exist_ok=True)


def find_claude_cmd(cfg: dict[str, Any] | None = None) -> str:
    """Locate the Claude Code CLI executable."""
    if cfg and cfg.get("claude_cmd"):
        return cfg["claude_cmd"]
    if os.name == "nt":
        # npm shims: claude.cmd is callable from subprocess without a shell.
        for name in ("claude.cmd", "claude.exe", "claude"):
            path = shutil.which(name)
            if path:
                return path
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "claude.cmd"
        if candidate.exists():
            return str(candidate)
    else:
        path = shutil.which("claude")
        if path:
            return path
    raise FileNotFoundError(
        "Claude Code CLI not found. Set 'claude_cmd' in "
        f"{CONFIG_PATH} to the full path of your claude executable."
    )
