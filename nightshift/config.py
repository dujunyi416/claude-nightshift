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
        # Merge pending fresh-session jobs that share a cwd into a single
        # `claude -p` run (loads the project context once -> saves quota and
        # lets the model coordinate related tasks). Session-bound (resume)
        # jobs are never merged.
        "merge_same_cwd": True,
    },
    "resume": {
        # Auto-resume sessions that were cut off by the rate limit.
        "enabled": True,
        # Only consider interruptions newer than this.
        "lookback_hours": 48,
        # Don't touch a transcript modified within this many minutes - it may
        # still be actively running. This is what keeps the live session you
        # are typing in from being resumed out from under you.
        "idle_min": 5,
        # Also treat "cut off mid-action" (last turn is an unfinished
        # tool_use, no limit marker) as interrupted. The current app usually
        # does NOT write a limit marker, so without this auto-resume rarely
        # fires. Risk: a session you deliberately stopped mid-action could be
        # resumed; the idle gate + max_sessions cap limit the blast radius.
        "detect_stalled": True,
        # In watch, auto-resume medium-confidence (stalled) sessions too.
        # Set False to auto-resume only high-confidence limit markers and
        # leave stalls for one-click resume in the panel.
        "auto_stalled": True,
        "prompt": (
            "You were interrupted (usage limit or a cut-off mid-action). "
            "Continue from where you left off and finish the remaining work."
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
    "keepwarm": {
        # When enabled, during awake hours the 5h window is re-activated the
        # moment it goes idle (so a 16:00 reset is warmed at 16:00, not when
        # you happen to notice at 16:30). Outside these hours nothing fires -
        # the morning scheduled warmup takes over.
        "enabled": False,
        "start": "07:00",
        "end": "23:00",
        # Minimum minutes between ping attempts (safety throttle).
        "min_gap_min": 10,
    },
    "gui": {
        # Local web panel port (127.0.0.1 only).
        "port": 8377,
    },
    "telegram": {
        # Optional: notify when overnight jobs finish, and (if polling is on)
        # control nightshift from your phone: /status /queue /resume, or just
        # send text to queue it as a job. Leave empty to disable.
        # Env vars NIGHTSHIFT_TG_TOKEN / NIGHTSHIFT_TG_CHAT also work.
        "bot_token": "",
        "chat_id": "",
        # Where /add-style jobs from Telegram run by default.
        "default_cwd": "",
        # Two-way control (long-polls getUpdates from tray/watch).
        "polling": True,
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
