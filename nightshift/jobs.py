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

    @property
    def path(self) -> Path:
        return QUEUE_DIR / f"{self.id}.json"


def new_job(prompt: str, cwd: str, model: str = "", permission_mode: str = "",
            priority: int = 5, timeout_min: int = 0,
            session_id: str = "") -> Job:
    job = Job(
        id=time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6],
        prompt=prompt,
        cwd=str(Path(cwd).resolve()),
        model=model,
        permission_mode=permission_mode,
        priority=priority,
        timeout_min=timeout_min,
        session_id=session_id,
    )
    QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    job.path.write_text(
        json.dumps(asdict(job), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return job


def load_jobs() -> list[Job]:
    """Pending jobs, ordered by (priority, creation time)."""
    jobs = []
    if not QUEUE_DIR.exists():
        return jobs
    for p in QUEUE_DIR.glob("*.json"):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            jobs.append(Job(**data))
        except (json.JSONDecodeError, TypeError) as e:
            print(f"warning: skipping malformed job file {p.name}: {e}")
    jobs.sort(key=lambda j: (j.priority, j.created_at))
    return jobs


def remove_job(job_id: str) -> bool:
    """Remove a pending job by full id or unique prefix."""
    for job in load_jobs():
        if job.id == job_id or job.id.startswith(job_id):
            job.path.unlink(missing_ok=True)
            return True
    return False


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
        lines.append(f"  {i}. [{j.id}] p{j.priority} {created}{tag}  {preview}")
        lines.append(f"     cwd: {j.cwd}")
    return "\n".join(lines)
