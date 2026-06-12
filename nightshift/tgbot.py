"""Two-way Telegram control.

Outbound notifications live in notify.py; this module long-polls
getUpdates so you can drive nightshift from your phone while in bed:

    /status          quota summary (+ currently-running task)
    /running         how long the current task has been running
    /queue           list queued jobs
    /resume          resume limit-interrupted sessions (when quota allows)
    /warmup          ping the 5h window now
    keyword: prompt  continue the recent session matching "keyword"
    any other text   queued as a job in telegram.default_cwd

Only messages from the configured chat_id are accepted. A heartbeat lock
file prevents two pollers (tray + watch both running) from fighting over
getUpdates.
"""

from __future__ import annotations

import json
import os
import re
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

# A "keyword: prompt" message can match several recent sessions. We stash the
# candidates here so a follow-up bare number ("1", "2") picks one without us
# ever dumping a long list. Single-user tool, so one global slot is enough.
_PENDING: dict = {}

# keyword (no spaces/colon, 1-20 chars) + ":" / "：" + the rest of the message.
_PREFIX_RE = re.compile(r"^([^\s:：]{1,20})[:：]\s*(.+)$", re.S)


def _api(token: str, method: str, **params) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urllib.parse.urlencode(params).encode()
    with urllib.request.urlopen(url, data=data, timeout=70) as resp:
        return json.load(resp)


def get_bot_username(token: str) -> str | None:
    """Return the bot's @username, or None if the token is invalid."""
    try:
        me = _api(token, "getMe")
        return (me.get("result") or {}).get("username") if me.get("ok") else None
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None


def mark_offset_current(token: str) -> None:
    """Advance our update offset past all pending messages, so messages sent
    *before* connecting (e.g. the one used to detect the chat id) are not
    replayed and executed as jobs once polling starts."""
    try:
        up = _api(token, "getUpdates")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return
    ids = [u["update_id"] for u in up.get("result", []) if "update_id" in u]
    if ids:
        _save_offset(max(ids) + 1)


def resolve_chat_id(token: str) -> str | None:
    """Find the chat id of whoever last messaged the bot - so the user just
    pastes a token and texts the bot, no manual chat-id lookup needed."""
    try:
        up = _api(token, "getUpdates")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    for u in reversed(up.get("result", [])):
        msg = u.get("message") or u.get("edited_message") or {}
        cid = (msg.get("chat") or {}).get("id")
        if cid:
            return str(cid)
    return None


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


def _match_sessions(keyword: str) -> list:
    """Recent (non-trivial) sessions whose title or folder contains keyword."""
    from .sessions import list_recent_sessions

    kw = keyword.lower()
    out = []
    for s in list_recent_sessions(days=7, limit=20):
        if s.trivial:
            continue
        if kw in s.title.lower() or kw in Path(s.cwd).name.lower():
            out.append(s)
    return out


def _queue_resume(session, prompt: str) -> str:
    from .jobs import new_job, short_dir

    job = new_job(prompt, cwd=session.cwd, session_id=session.session_id)
    return (f"✅ 续写「{session.title[:30]}」（{short_dir(session.cwd)}），"
            f"{len(prompt)} 字，任务 {job.id}")


def _handle(text: str) -> str:
    """Process one inbound message, return the reply text."""
    from .jobs import short_dir

    text = text.strip()
    low = text.lower()

    # A bare number resolves a pending "keyword matched several sessions" prompt.
    if _PENDING and text.isdigit():
        choice, matches = int(text), _PENDING.get("matches", [])
        prompt = _PENDING.get("prompt", "")
        _PENDING.clear()
        if 1 <= choice <= len(matches):
            return _queue_resume(matches[choice - 1], prompt)
        return "编号超出范围，已取消。再发一次「关键词: 指令」试试。"

    if low in ("/status", "status"):
        from .jobs import format_running, get_running
        from .quota import fetch_usage, format_snapshot

        try:
            snap = format_snapshot(fetch_usage(force=True))
        except RuntimeError as e:
            snap = f"quota unavailable: {e}"
        if get_running():
            snap += "\n\n" + format_running()
        return snap

    if low in ("/running", "running", "在跑吗", "还在跑吗"):
        from .jobs import format_running

        return format_running()

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
        return ("/status - 额度 + 在跑任务\n/running - 当前任务跑多久了\n"
                "/queue - 队列\n/resume - 续跑被打断的会话\n"
                "/warmup - 立即预热窗口\n\n"
                "直接发文字 = 在默认目录排一个新任务\n"
                "「关键词: 指令」= 续写匹配该关键词的最近会话\n"
                "（例：btc: 把回测补全）")

    # "keyword: prompt" continues a matching recent session instead of starting
    # a new job. If nothing matches, fall through and queue it as a new job.
    m = _PREFIX_RE.match(text)
    if m:
        keyword, rest = m.group(1).strip(), m.group(2).strip()
        matches = _match_sessions(keyword)
        if len(matches) == 1:
            return _queue_resume(matches[0], rest)
        if len(matches) > 1:
            top = matches[:5]
            _PENDING.clear()
            _PENDING.update(prompt=rest, matches=top)
            lines = [f"「{keyword}」匹到 {len(top)} 个，回数字选一个："]
            for i, s in enumerate(top, 1):
                lines.append(f"{i}) {s.title[:30]}（{short_dir(s.cwd)}）")
            return "\n".join(lines)
        # no match: keyword wasn't a project, treat the whole line as a job.

    # Anything else becomes a queued job.
    from .jobs import new_job

    cfg = load_config()["telegram"]
    cwd = cfg.get("default_cwd") or str(Path.home())
    job = new_job(text, cwd=cwd)
    return (f"✅ 收到 {len(text)} 字，排为任务 {job.id}\n"
            f"目录: {short_dir(cwd)}\n预览: {text[:50]}…\n"
            "⚠ 超 4096 字 Telegram 会拆成多条→多个任务；超长提示词请用电脑面板粘贴")


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
