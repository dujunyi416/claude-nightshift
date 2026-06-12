"""Overnight runner: waits for quota, then drains the prompt queue.

Typical flow:
    23:30  nightshift add "refactor the data loader, run tests" --cwd D:/proj
    23:31  nightshift run          (leave the terminal open, go to sleep)
    01:40  5h window resets -> job starts
    03:10  job done -> Telegram ping (optional), log written to ~/.nightshift/done/
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import DATA_DIR, find_claude_cmd, load_config
from .jobs import (Job, archive_job, clear_running, load_jobs, set_running,
                   short_dir)
from .notify import notify
from .quota import UsageSnapshot, fetch_usage

RUNNER_LOG = DATA_DIR / "logs" / "runner.log"
STREAM_TAIL_PATH = DATA_DIR / "logs" / "stream_tail.txt"
_STREAM_KEEP = 20  # lines to keep in the rolling tail

# Substrings that indicate claude aborted due to the rate limit, in which
# case the job should be re-queued instead of marked failed.
LIMIT_MARKERS = ("usage limit", "rate limit", "limit reached", "limit will reset")


def _fmt_tool(name: str, inp: dict) -> str:
    if name == "Read":
        return f"📖 读 {Path(inp.get('file_path', '?')).name}"
    if name in ("Write", "Edit"):
        return f"✏️ {name} {Path(inp.get('file_path', '?')).name}"
    if name == "Bash":
        return f"⚙️ {(inp.get('command') or '')[:60]}"
    if name in ("Glob", "Grep"):
        return f"🔍 {name} {(inp.get('pattern') or inp.get('query') or '')[:40]}"
    return f"🔧 {name}"


def _fmt_event(ev: dict) -> str | None:
    """Return a one-line human summary for a stream-json event, or None."""
    stamp = datetime.now().astimezone().strftime("%H:%M:%S")
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        return f"[{stamp}] 🚀 初始化 ({ev.get('model', '')})"
    if t == "assistant":
        parts = []
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                txt = (block.get("text") or "").strip()
                if txt:
                    parts.append(f"💬 {txt[:120]}")
            elif block.get("type") == "tool_use":
                parts.append(_fmt_tool(block.get("name", ""), block.get("input") or {}))
        return f"[{stamp}] " + "; ".join(parts[:3]) if parts else None
    if t == "result":
        sub = ev.get("subtype", "")
        suffix = (": " + (ev.get("result") or "").strip()[:60]) if ev.get("result") else ""
        return f"[{stamp}] {'✅ 完成' if sub == 'success' else '❌ 结束 (' + sub + ')'}{suffix}"
    return None


def _append_stream_tail(line: str) -> None:
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return
    summary = _fmt_event(ev)
    if not summary:
        return
    STREAM_TAIL_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = STREAM_TAIL_PATH.read_text(encoding="utf-8").splitlines()
    except OSError:
        existing = []
    existing.append(summary)
    STREAM_TAIL_PATH.write_text(
        "\n".join(existing[-_STREAM_KEEP:]) + "\n", encoding="utf-8")


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
    cmd = [claude, "-p", "--output-format", "stream-json"]
    if job.session_id:
        cmd += ["--resume", job.session_id]
    cmd.append(job.prompt)
    if job.model:
        cmd += ["--model", job.model]
    mode = job.permission_mode or cfg["runner"]["permission_mode"]
    if mode:
        cmd += ["--permission-mode", mode]
    for d in (job.add_dirs or []):
        cmd += ["--add-dir", d]
    return cmd


def _execute(job: Job, cfg: dict) -> tuple[bool, bool, str]:
    """Run one job. Returns (success, hit_rate_limit, combined_output)."""
    cmd = _build_cmd(job, cfg)
    timeout = (job.timeout_min or cfg["runner"]["job_timeout_min"]) * 60
    _log(f"job {job.id}: starting in {job.cwd}")
    _log(f"job {job.id}: prompt: {job.prompt[:120]}")
    start = time.time()
    STREAM_TAIL_PATH.unlink(missing_ok=True)

    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        proc = subprocess.Popen(
            cmd, cwd=job.cwd,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            creationflags=flags,
        )
    except OSError as e:
        return False, False, f"LAUNCH ERROR: {e}"

    stderr_buf: list[str] = []

    def _drain_stderr() -> None:
        for ln in proc.stderr:
            stderr_buf.append(ln)

    threading.Thread(target=_drain_stderr, daemon=True).start()

    stdout_buf: list[str] = []
    timed_out = False
    try:
        for raw in proc.stdout:
            stdout_buf.append(raw)
            _append_stream_tail(raw.rstrip())
            if time.time() - start > timeout:
                proc.kill()
                timed_out = True
                break
        proc.stdout.close()
    except OSError:
        pass

    if timed_out:
        return False, False, f"TIMEOUT after {timeout / 60:.0f} minutes"

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()

    stdout_text = "".join(stdout_buf)
    stderr_text = "".join(stderr_buf)
    elapsed = (time.time() - start) / 60
    output = (
        f"=== exit {proc.returncode} after {elapsed:.1f} min ===\n"
        f"--- stdout (stream-json) ---\n{stdout_text}\n--- stderr ---\n{stderr_text}"
    )
    blob = (stdout_text + stderr_text).lower()
    mentions_limit = any(m in blob for m in LIMIT_MARKERS)
    hit_limit = mentions_limit and proc.returncode != 0
    return proc.returncode == 0 and not hit_limit, hit_limit, output


def _merge_prompt(jobs: list[Job]) -> str:
    """One combined prompt so a single `claude -p` loads the project context
    once and does several related tasks in order (saves quota + time)."""
    body = "\n".join(f"{i}) {j.prompt}" for i, j in enumerate(jobs, 1))
    return (f"你有 {len(jobs)} 个任务，依次完成，每条做完用一句话报告结果：\n"
            f"{body}")


def _next_batch(jobs: list[Job], merge: bool) -> list[Job]:
    """The job(s) to run next. Paused jobs are skipped. When merge is on and
    the head job starts a fresh session, all pending same-cwd fresh-session
    jobs are batched into one run. Session-bound (resume) jobs never merge."""
    runnable = [j for j in jobs if not j.paused]
    if not runnable:
        return []
    head = runnable[0]
    if not merge or head.session_id:
        return [head]
    head_cwd = Path(head.cwd).resolve()
    return [j for j in runnable
            if not j.session_id and Path(j.cwd).resolve() == head_cwd]


def run_queue(when: str = "", once: bool = False) -> int:
    """Drain the queue. Returns number of successfully completed jobs."""
    cfg = load_config()
    rcfg = cfg["runner"]
    when = when or rcfg["start_when"]
    stop_util = rcfg["stop_utilization"]
    poll_sec = rcfg["poll_interval_sec"]
    merge = rcfg.get("merge_same_cwd", True)

    jobs = load_jobs()
    if not jobs:
        _log("queue is empty - nothing to do.")
        return 0
    _log(f"runner started: {len(jobs)} job(s), start_when={when}")

    completed = 0
    while True:
        batch = _next_batch(load_jobs(), merge)
        if not batch:
            break  # only paused jobs left (or empty)

        usage = _quota_or_none()
        if not _can_start(usage, stop_util, when):
            _wait_for_reset(poll_sec)

        head = batch[0]
        if len(batch) > 1:
            run_job = Job(
                id=head.id, prompt=_merge_prompt(batch), cwd=head.cwd,
                model=head.model, permission_mode=head.permission_mode,
                timeout_min=head.timeout_min)
            label = f"{len(batch)} 个任务（合并）: {head.prompt[:50]}"
        else:
            run_job = head
            label = head.prompt[:70]

        set_running(run_job)
        notify(f"🟢 开始: {label}（{short_dir(head.cwd)}）")
        start = time.time()
        success, hit_limit, output = _execute(run_job, cfg)
        mins = (time.time() - start) / 60
        clear_running()

        if hit_limit:
            _log(f"job {head.id}: hit the rate limit - re-queueing.")
            _wait_for_reset(poll_sec)
            continue  # same job(s) retried next iteration

        for job in batch:
            archive_job(job, success, output)
        if success:
            completed += len(batch)
            _log(f"job {head.id}: done ({len(batch)} task(s), {mins:.0f}m).")
            notify(f"✅ 完成 · {mins:.0f}分钟: {label[:60]}")
        else:
            _log(f"job {head.id}: FAILED (see failed/{head.id}.log)")
            notify(f"❌ 失败: {label[:60]}（看 failed/ 日志）")

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
    from .tgbot import start_polling
    from .warmup import maybe_keepwarm

    cfg = load_config()
    rcfg = cfg["runner"]
    poll_sec = poll_sec or rcfg["poll_interval_sec"]
    stop_util = rcfg["stop_utilization"]
    resume_cfg = cfg["resume"]
    start_polling()  # two-way telegram control, no-op if unconfigured
    last_keepwarm = 0.0
    cycle = 0
    _log(f"watch started (poll {poll_sec:.0f}s, lookback "
         f"{resume_cfg['lookback_hours']}h, idle gate {resume_cfg['idle_min']}m, "
         f"Ctrl+C to stop)")

    while True:
        cycle += 1
        last_keepwarm = maybe_keepwarm(last_keepwarm)
        sessions = (
            pending_resumes(resume_cfg["lookback_hours"], None,
                            resume_cfg.get("idle_min", 5),
                            resume_cfg.get("detect_stalled", True))
            if resume_cfg.get("enabled", True) else []
        )
        if not resume_cfg.get("auto_stalled", True):
            sessions = [s for s in sessions if s.confidence == "high"]
        jobs = load_jobs()

        usage = _quota_or_none()
        util = usage.five_hour.utilization if usage else None
        # Heartbeat so the loop is never a silent black box.
        _log(f"cycle {cycle}: 5h={util if util is not None else '?'}% "
             f"interrupted={len(sessions)} queued={len(jobs)}")

        if not sessions and not jobs:
            time.sleep(poll_sec)
            continue

        if sessions:
            _log(format_pending(sessions))
        if usage and usage.five_hour.active and (util or 0) >= stop_util:
            _log(f"5h at {util:.0f}% (>= {stop_util}); waiting for reset")
            _wait_for_reset(poll_sec)

        for s in sessions[: resume_cfg.get("max_sessions", 3)]:
            resume_session(s, cfg)
            usage = _quota_or_none()
            if usage and usage.five_hour.exhausted:
                _log("window exhausted by resume; waiting for reset")
                _wait_for_reset(poll_sec)

        if load_jobs():
            run_queue(when="now")
        time.sleep(poll_sec)
