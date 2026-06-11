"""Windows Task Scheduler integration.

Creates daily tasks that wake the machine from sleep (-WakeToRun) and run
the warmup ping (or the queue runner) even if you forgot to leave a
terminal open. Tasks are named "ClaudeNightshift <kind> <HHMM>" so they are
easy to find in Task Scheduler and easy to remove in bulk.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from .config import DATA_DIR

TASK_PREFIX = "ClaudeNightshift"


def _python_exe() -> str:
    return sys.executable


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _wrapper_cmd(kind: str, extra_args: str = "") -> Path:
    """Generate a .cmd wrapper so the scheduled task has a clean action."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / "logs").mkdir(exist_ok=True)
    path = DATA_DIR / f"task_{kind}.cmd"
    log = DATA_DIR / "logs" / f"task_{kind}.log"
    path.write_text(
        "@echo off\r\n"
        f'cd /d "{_repo_root()}"\r\n'
        f'"{_python_exe()}" -m nightshift {kind} {extra_args} '
        f'>> "{log}" 2>&1\r\n',
        encoding="utf-8",
    )
    return path


def _run_ps(command: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def register_daily(kind: str, hhmm: str, extra_args: str = "") -> bool:
    """Register (or replace) a daily task. kind: 'warmup' or 'run'."""
    wrapper = _wrapper_cmd(kind, extra_args)
    task_name = f"{TASK_PREFIX} {kind} {hhmm.replace(':', '')}"
    ps = (
        f"$action = New-ScheduledTaskAction -Execute 'cmd.exe' "
        f"-Argument '/c \"\"{wrapper}\"\"';"
        f"$trigger = New-ScheduledTaskTrigger -Daily -At {hhmm};"
        f"$settings = New-ScheduledTaskSettingsSet -WakeToRun "
        f"-StartWhenAvailable -AllowStartIfOnBatteries "
        f"-DontStopIfGoingOnBatteries;"
        f"Register-ScheduledTask -TaskName '{task_name}' -Action $action "
        f"-Trigger $trigger -Settings $settings -Force | Out-Null;"
        f"Write-Output 'registered'"
    )
    result = _run_ps(ps)
    ok = result.returncode == 0 and "registered" in result.stdout
    if ok:
        print(f"scheduled: '{task_name}' daily at {hhmm} (wakes PC from sleep)")
        print(f"  action: {wrapper}")
    else:
        print(f"failed to register task: {result.stderr.strip()[:300]}")
    return ok


def unregister_all() -> int:
    ps = (
        f"$tasks = Get-ScheduledTask -TaskName '{TASK_PREFIX}*' "
        f"-ErrorAction SilentlyContinue;"
        f"$tasks | Unregister-ScheduledTask -Confirm:$false;"
        f"Write-Output $tasks.Count"
    )
    result = _run_ps(ps)
    try:
        n = int(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        n = 0
    print(f"removed {n} scheduled task(s).")
    return n


def list_tasks() -> str:
    ps = (
        f"Get-ScheduledTask -TaskName '{TASK_PREFIX}*' "
        f"-ErrorAction SilentlyContinue | "
        f"ForEach-Object {{ $info = $_ | Get-ScheduledTaskInfo; "
        f"'{{0}}  next run: {{1}}' -f $_.TaskName, $info.NextRunTime }}"
    )
    result = _run_ps(ps)
    out = result.stdout.strip()
    return out if out else "no nightshift tasks scheduled."
