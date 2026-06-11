"""Overnight runner: waits for quota, then drains the prompt queue.

Typical flow:
    23:30  nightshift add "refactor the data loader, run tests" --cwd D:/proj
    23:31  nightshift run          (leave the terminal open, go to sleep)
    01:40  5h window resets -> job starts
    03:10  job done -> Telegram ping (optional), log written to ~/.nightshift/done/
"""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone

from .config import DATA_DIR, find_claude_cmd, load_config
from .jobs import Job, archive_job, load_jobs
from .notify import notify
from .quota import UsageSnapshot, fetch_usage

RUNNER_LOG = DATA_DIR / "logs" / "runner.log"

# Substrings that indicate claude aborted due to the rate limit, in which
# case the job should be re-queued instead of marked failed.
LIMIT_MARKERS = ("usage limit", "rate limit", "limit reached", "limit will reset")


def _log(msg: str) -> None:
    RUNNER_LOG.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{stamp}] {msg}"
    with RUNNER_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line, flush=True)


def _quota_or_none() -> UsageSnapshot | None:
    try:
        return fetch_usage(force=True)
    except RuntimeError as e:
        _log(f"quota check failed: {e}")
        return None


def _can_start(usage: UsageSnapshot | None, stop_util: float, when: str) -> bool:
    if usage is None:
        # Endpoint down: optimistically try - claude itself will refuse
        # if the limit is hit, and we re-queue on that.
        return True
    w = usage.five_hour
    if not w.active:
        return True  # idle window: first message starts a fresh one
    if when == "now":
        return (w.utilization or 0) < stop_util
    # when == "reset": only start on a comfortably fresh window
    return (w.utilization or 0) < stop_util


def _wait_for_reset(poll_sec: float) -> None:
    """Sleep until the 5h window resets (or quota becomes available)."""
    while True:
        usage = _quota_or_none()
        if usage is None:
            _log(f"no quota data; retrying in {poll_sec:.0f}s")
            time.sleep(poll_sec)
            continue
        w = usage.five_hour
        secs = w.seconds_to_reset()
        if not w.active or secs is None or secs <= 0:
            _log("window reset - quota available.")
            return
        # Sleep until just past the reset, but re-check periodically in case
        # resets_at shifts or the endpoint had stale data.
        sleep_for = min(secs + 60, max(poll_sec, 60))
        local = w.resets_at.astimezone().strftime("%H:%M") if w.resets_at else "?"
        _log(
            f"5h window at {w.utilization:.0f}%, resets {local}; "
            f"sleeping {sleep_for / 60:.0f}m"
        )
        time.sleep(sleep_for)


def _build_cmd(job: Job, cfg: dict) -> list[str]:
    claude = find_claude_cmd(cfg)
    cmd = [claude, "-p", job.prompt]
    if job.model:
        cmd += ["--model", job.model]
    mode = job.permission_mode or cfg["runner"]["permission_mode"]
    if mode:
        cmd += ["--permission-mode", mode]
    return cmd


def _execute(job: Job, cfg: dict) -> tuple[bool, bool, str]:
    """Run one job. Returns (success, hit_rate_limit, combined_output)."""
    cmd = _build_cmd(job, cfg)
    timeout = (job.timeout_min or cfg["runner"]["job_timeout_min"]) * 60
    _log(f"job {job.id}: starting in {job.cwd}")
    _log(f"job {job.id}: prompt: {job.prompt[:120]}")
    start = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=job.cwd, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
    except subprocess.TimeoutExpired:
        return False, False, f"TIMEOUT after {timeout / 60:.0f} minutes"
    except OSError as e:
        return False, False, f"LAUNCH ERROR: {e}"

    elapsed = (time.time() - start) / 60
    output = (
        f"=== exit {result.returncode} after {elapsed:.1f} min ===\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    blob = (result.stdout + result.stderr).lower()
    # A failed run mentioning the limit, or a suspiciously short "reply"
    # that is just the limit banner, means we were rate-limited - not a
    # real job failure. (A long successful output may legitimately contain
    # the words "rate limit", so only short outputs count there.)
    mentions_limit = any(m in blob for m in LIMIT_MARKERS)
    hit_limit = mentions_limit and (
        result.returncode != 0 or len(result.stdout) < 500
    )
    return result.returncode == 0 and not hit_limit, hit_limit, output


def run_queue(when: str = "", once: bool = False) -> int:
    """Drain the queue. Returns number of successfully completed jobs."""
    cfg = load_config()
    rcfg = cfg["runner"]
    when = when or rcfg["start_when"]
    stop_util = rcfg["stop_utilization"]
    poll_sec = rcfg["poll_interval_sec"]

    jobs = load_jobs()
    if not jobs:
        _log("queue is empty - nothing to do.")
        return 0
    _log(f"runner started: {len(jobs)} job(s), start_when={when}")

    completed = 0
    while True:
        jobs = load_jobs()
        if not jobs:
            break

        usage = _quota_or_none()
        if not _can_start(usage, stop_util, when):
            _wait_for_reset(poll_sec)

        job = jobs[0]
        success, hit_limit, output = _execute(job, cfg)

        if hit_limit:
            _log(f"job {job.id}: hit the rate limit - re-queueing.")
            _wait_for_reset(poll_sec)
            continue  # same job retried next iteration

        archive_job(job, success, output)
        if success:
            completed += 1
            _log(f"job {job.id}: done.")
            notify(f"[nightshift] job done: {job.prompt[:100]}")
        else:
            _log(f"job {job.id}: FAILED (see failed/{job.id}.log)")
            notify(f"[nightshift] job FAILED: {job.prompt[:100]}")

        if once:
            break

    remaining = len(load_jobs())
    _log(f"runner finished: {completed} done, {remaining} still queued.")
    if completed and not remaining:
        notify(f"[nightshift] queue drained: {completed} job(s) completed.")
    return completed


def watch(poll_sec: float = 0) -> None:
    """Full autopilot: loop forever, and whenever quota is available,
    (1) resume sessions that were cut off by the limit, then
    (2) drain the prompt queue.

    Leave this running in a terminal (or schedule it at bedtime) and any
    task interrupted by the limit continues the moment the window resets.
    """
    from .resume import format_pending, pending_resumes, resume_session

    cfg = load_config()
    rcfg = cfg["runner"]
    poll_sec = poll_sec or rcfg["poll_interval_sec"]
    stop_util = rcfg["stop_utilization"]
    resume_cfg = cfg["resume"]
    _log(f"watch started (poll {poll_sec:.0f}s, Ctrl+C to stop)")

    while True:
        sessions = (
            pending_resumes(resume_cfg["lookback_hours"])
            if resume_cfg.get("enabled", True) else []
        )
        jobs = load_jobs()
        if not sessions and not jobs:
            time.sleep(poll_sec)
            continue

        if sessions:
            _log(format_pending(sessions))
        usage = _quota_or_none()
        if usage and usage.five_hour.active and \
                (usage.five_hour.utilization or 0) >= stop_util:
            _wait_for_reset(poll_sec)

        for s in sessions[: resume_cfg.get("max_sessions", 3)]:
            resume_session(s, cfg)
            usage = _quota_or_none()
            if usage and usage.five_hour.exhausted:
                break  # the resume itself burned the window; wait again

        if load_jobs():
            run_queue(when="now")
        time.sleep(poll_sec)
