"""Prompt queue: jobs you write before bed, executed when quota allows.

Each job is one JSON file in ~/.nightshift/queue/. Finished jobs move to
done/ or failed/ together with a .log of the full claude output, so you can
review what happened over breakfast.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .config import DATA_DIR

QUEUE_DIR = DATA_DIR / "queue"
DONE_DIR = DATA_DIR / "done"
FAILED_DIR = DATA_DIR / "failed"
LOGS_DIR = DATA_DIR / "logs"
RUNNING_PATH = DATA_DIR / "running.json"


def short_dir(cwd: str) -> str:
    """Last path component only - so Telegram never leaks the absolute path
    (which exposes the username, drive letter and project layout)."""
    name = Path(cwd).name or str(cwd)
    return f"…/{name}"


@dataclass
class Job:
    id: str
    prompt: str
    cwd: str  # working directory claude runs in
    model: str = ""  # empty = Claude Code default
    permission_mode: str = ""  # empty = config default
    priority: int = 5  # 1 = first, 9 = last
    created_at: float = field(default_factory=time.time)
    timeout_min: int = 0  # 0 = config default
    # When set, the job continues an existing session (claude -p --resume)
    # instead of starting a fresh one.
    session_id: str = ""
    # Manual sort position within the queue (drag-to-reorder / pin-to-top).
    # Defaults to created_at so the natural order is creation order; pinning
    # sets it below every other job. None means "unset" (old job files have no
    # order field), and is resolved to created_at in __post_init__ - so 0 and
    # negative values stay valid sort keys.
    order: float | None = None
    # Temporarily skipped by the runner without being deleted.
    paused: bool = False
    # Extra directories claude is allowed to read/write (--add-dir flags).
    add_dirs: list = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.order is None:
            self.order = self.created_at

    @property
    def path(self) -> Path:
        return QUEUE_DIR / f"{self.id}.json"


def new_job(prompt: str, cwd: str, model: str = "", permission_mode: str = "",
            priority: int = 5, timeout_min: int = 0,
            session_id: str = "", add_dirs: list | None = None) -> Job:
    job = Job(
        id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
        prompt=prompt,
        cwd=str(Path(cwd).resolve()),
        model=model,
        permission_mode=permission_mode,
        priority=priority,
        timeout_min=timeout_min,
        session_id=session_id,
        add_dirs=[d.strip() for d in (add_dirs or []) if d and d.strip()],
    )
    _write_job(job)
    return job


def load_jobs() -> list[Job]:
    """Pending jobs, ordered by (priority, manual order)."""
    jobs = []
    if not QUEUE_DIR.exists():
        return jobs
    for p in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            jobs.append(Job(**data))
        except (json.JSONDecodeError, TypeError) as e:
            print(f"warning: skipping malformed job file {p.name}: {e}")
    jobs.sort(key=lambda j: (j.priority, j.order))
    return jobs


def _write_job(job: Job) -> None:
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    job.path.write_text(
        json.dumps(asdict(job), indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_job(job_id: str) -> Job | None:
    """Fetch one pending job by full id or unique prefix."""
    for job in load_jobs():
        if job.id == job_id or job.id.startswith(job_id):
            return job
    return None


def update_job(job_id: str, prompt: str | None = None,
               cwd: str | None = None,
               add_dirs: list | None = None) -> bool:
    """Edit a queued job in place, preserving its id and queue position."""
    job = get_job(job_id)
    if job is None:
        return False
    if prompt is not None:
        job.prompt = prompt
    if cwd is not None:
        job.cwd = str(Path(cwd).resolve())
    if add_dirs is not None:
        job.add_dirs = [d.strip() for d in add_dirs if d and d.strip()]
    _write_job(job)
    return True


def set_paused(job_id: str, paused: bool) -> bool:
    """Pause (skip) or re-enable a queued job without deleting it."""
    job = get_job(job_id)
    if job is None:
        return False
    job.paused = paused
    _write_job(job)
    return True


def pin_job(job_id: str) -> bool:
    """Move a job to the very front of the queue (runs next)."""
    jobs = load_jobs()
    target = next((j for j in jobs if j.id == job_id
                   or j.id.startswith(job_id)), None)
    if target is None:
        return False
    target.priority = min((j.priority for j in jobs), default=target.priority)
    target.order = min((j.order for j in jobs), default=target.order) - 1
    _write_job(target)
    return True


def reorder_jobs(ordered_ids: list[str]) -> int:
    """Reassign each job's order to match the given visual sequence.
    Ids not present are left untouched. Returns the count updated."""
    by_id = {j.id: j for j in load_jobs()}
    n = 0
    for i, job_id in enumerate(ordered_ids):
        job = by_id.get(job_id)
        if job is None:
            continue
        job.order = float(i)
        job.priority = 5  # flatten priority so the manual order wins
        _write_job(job)
        n += 1
    return n


def remove_job(job_id: str) -> bool:
    """Remove a pending job by full id or unique prefix."""
    for job in load_jobs():
        if job.id == job_id or job.id.startswith(job_id):
            job.path.unlink(missing_ok=True)
            return True
    return False


# ----- "currently running" marker (for /running + watch push) -----

def set_running(job: Job) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RUNNING_PATH.write_text(json.dumps({
        "id": job.id, "prompt": job.prompt, "cwd": job.cwd,
        "model": job.model or "默认", "started_at": time.time(),
    }, ensure_ascii=False), encoding="utf-8")


def clear_running() -> None:
    RUNNING_PATH.unlink(missing_ok=True)


def get_running() -> dict | None:
    try:
        return json.loads(RUNNING_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def format_running() -> str:
    r = get_running()
    if not r:
        return "🟢 当前没有任务在跑。"
    mins = (time.time() - r.get("started_at", time.time())) / 60
    preview = (r.get("prompt") or "").replace("\n", " ")[:70]
    return (f"🟢 跑步中 · 已 {mins:.0f} 分钟 · 模型 {r.get('model', '默认')}\n"
            f"{preview}\n目录: {short_dir(r.get('cwd', ''))}")


def archive_job(job: Job, success: bool, log_text: str) -> Path:
    dest_dir = DONE_DIR if success else FAILED_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{job.id}.json"
    job.path.replace(dest)
    log_path = dest_dir / f"{job.id}.log"
    log_path.write_text(log_text, encoding="utf-8")
    return dest


def format_jobs(jobs: list[Job]) -> str:
    if not jobs:
        return "queue is empty."
    lines = [f"{len(jobs)} job(s) queued:"]
    for i, j in enumerate(jobs, 1):
        preview = j.prompt.replace("\n", " ")[:70]
        created = time.strftime("%m-%d %H:%M", time.localtime(j.created_at))
        tag = f" resume:{j.session_id[:8]}" if j.session_id else ""
        flag = " ⏸" if j.paused else ""
        lines.append(f"  {i}.{flag} [{j.id}] p{j.priority} {created}{tag}  {preview}")
        lines.append(f"     目录: {short_dir(j.cwd)}")
    return "\n".join(lines)
