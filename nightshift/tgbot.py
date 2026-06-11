"""Two-way Telegram control.

Outbound notifications live in notify.py; this module long-polls
getUpdates so you can drive nightshift from your phone while in bed:

    /status          quota summary
    /queue           list queued jobs
    /resume          resume limit-interrupted sessions (when quota allows)
    /warmup          ping the 5h window now
    any other text   queued as a job in telegram.default_cwd

Only messages from the configured chat_id are accepted. A heartbeat lock
file prevents two pollers (tray + watch both running) from fighting over
getUpdates.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from .config import DATA_DIR, load_config
from .notify import _tg_settings, notify

OFFSET_PATH = DATA_DIR / "tg_offset.json"
LOCK_PATH = DATA_DIR / "tg_poller.lock"
HEARTBEAT_SEC = 60


def _api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=70) as resp:
        return json.load(resp)


def _load_offset() -> int:
    try:
        return json.loads(OFFSET_PATH.read_text())["offset"]
    except (OSError, json.JSONDecodeError, KeyError):
        return 0


def _save_offset(offset: int) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    OFFSET_PATH.write_text(json.dumps({"offset": offset}))


def _lock_is_fresh() -> bool:
    try:
        data = json.loads(LOCK_PATH.read_text())
        return time.time() - data["ts"] < HEARTBEAT_SEC * 2 \
            and data.get("pid") != os.getpid()
    except (OSError, json.JSONDecodeError, KeyError):
        return False


def _touch_lock() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCK_PATH.write_text(json.dumps({"pid": os.getpid(), "ts": time.time()}))


def _handle(text: str) -> str:
    """Process one inbound message, return the reply text."""
    text = text.strip()
    low = text.lower()

    if low in ("/status", "status"):
        from .quota import fetch_usage, format_snapshot

        try:
            return format_snapshot(fetch_usage(force=True))
        except RuntimeError as e:
            return f"quota unavailable: {e}"

    if low in ("/queue", "queue"):
        from .jobs import format_jobs, load_jobs

        return format_jobs(load_jobs())

    if low in ("/resume", "resume"):
        from .resume import format_pending, pending_resumes

        sessions = pending_resumes()
        if not sessions:
            return "no interrupted sessions."
        # Resuming may take a long time - run it off-thread and report later.
        def work():
            from .resume import resume_session

            for s in sessions[:3]:
                resume_session(s)
        threading.Thread(target=work, daemon=True).start()
        return "resuming in background:\n" + format_pending(sessions)

    if low in ("/warmup", "warmup"):
        from .warmup import warmup

        return "window activated." if warmup() else "skipped/failed (see logs)."

    if low in ("/help", "help", "/start"):
        return ("/status - quota\n/queue - jobs\n/resume - continue cut-off "
                "sessions\n/warmup - activate 5h window\nany other text - "
                "queue it as a job")

    # Anything else becomes a queued job.
    from .jobs import new_job

    cfg = load_config()["telegram"]
    cwd = cfg.get("default_cwd") or str(Path.home())
    job = new_job(text, cwd=cwd)
    return (f"queued {job.id}\ncwd: {cwd}\n"
            "it runs on the next 'nightshift run/watch' cycle.")


def poll_loop(stop: threading.Event | None = None) -> None:
    """Blocking long-poll loop. Exits silently if unconfigured or another
    poller already holds the heartbeat lock."""
    token, chat = _tg_settings()
    if not token or not chat:
        return
    if _lock_is_fresh():
        return  # someone else (tray or watch) is already polling
    offset = _load_offset()
    backoff = 5
    while stop is None or not stop.is_set():
        _touch_lock()
        try:
            resp = _api(token, "getUpdates", offset=offset, timeout=50,
                        allowed_updates='["message"]')
            backoff = 5
        except (urllib.error.URLError, OSError, json.JSONDecodeError):
            time.sleep(backoff)
            backoff = min(backoff * 2, 300)
            continue
        for update in resp.get("result", []):
            offset = update["update_id"] + 1
            _save_offset(offset)
            msg = update.get("message") or {}
            if str(msg.get("chat", {}).get("id")) != str(chat):
                continue
            text = msg.get("text", "")
            if not text:
                continue
            stamp = datetime.now().astimezone().strftime("%H:%M:%S")
            print(f"[{stamp}] telegram: {text[:60]}")
            try:
                reply = _handle(text)
            except Exception as e:  # never let one bad command kill the loop
                reply = f"error: {e}"
            notify(reply)


def start_polling() -> threading.Thread | None:
    """Start the poller in a daemon thread if telegram is configured."""
    cfg = load_config()["telegram"]
    token, chat = _tg_settings()
    if not token or not chat or not cfg.get("polling", True):
        return None
    t = threading.Thread(target=poll_loop, daemon=True, name="tg-poller")
    t.start()
    return t
