"""System tray icon + local web panel.

The tray badge shows your 5h-window utilization, color-coded
(green < 50% < yellow < 70% < orange < 90% < red), refreshed every minute.
Left-click opens the settings panel in your browser (served on 127.0.0.1
only): quota bars, daily warmup time, bedtime prompt queue, watch
autopilot, start-with-Windows.

Tray icon needs `pip install pystray pillow`; without them the web panel
still works (`nightshift tray` just opens the browser).
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

from .config import DATA_DIR, load_config, save_config
from .jobs import load_jobs, new_job
from .jobs import remove_job as jobs_remove
from .quota import UsageSnapshot, fetch_usage

try:
    import pystray
    from PIL import Image, ImageDraw, ImageFont

    HAS_TRAY = True
except ImportError:
    HAS_TRAY = False

REFRESH_SEC = 60
STARTUP_LNK = (
    Path.home() / "AppData/Roaming/Microsoft/Windows/Start Menu/Programs"
    / "Startup/ClaudeNightshift.lnk"
)


def install_launcher() -> tuple[bool, str]:
    """Create a 'Sleep Well' launch shortcut on the Desktop and in the Start
    Menu, so the tray/panel can be relaunched with a double-click (or a Start
    search) after the process is killed. Returns (ok, desktop_path_or_error)."""
    if os.name != "nt":
        return False, "仅支持 Windows（其他系统直接运行 `python -m nightshift tray`）"
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    projroot = Path(__file__).resolve().parent.parent
    # GetFolderPath resolves the *real* Desktop/Programs path even when it is
    # redirected into OneDrive, which a hard-coded ~/Desktop would miss.
    ps = (
        "$ws = New-Object -ComObject WScript.Shell;"
        "$dirs = @([Environment]::GetFolderPath('Desktop'),"
        " [Environment]::GetFolderPath('Programs'));"
        "foreach ($d in $dirs) {"
        "  $l = $ws.CreateShortcut((Join-Path $d 'Sleep Well.lnk'));"
        f"  $l.TargetPath = '{exe}';"
        "  $l.Arguments = '-m nightshift tray';"
        f"  $l.WorkingDirectory = '{projroot}';"
        "  $l.Description = 'Sleep Well - Claude quota automation';"
        "  $l.Save() };"
        "[Environment]::GetFolderPath('Desktop')"
    )
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True)
    if r.returncode != 0:
        return False, (r.stderr or "创建失败").strip()[:200]
    lines = [ln for ln in (r.stdout or "").splitlines() if ln.strip()]
    return True, (lines[-1].strip() if lines else "")


def _badge_color(util: float | None) -> str:
    if util is None:
        return "#5a6472"
    if util >= 90:
        return "#cc3333"
    if util >= 70:
        return "#e07020"
    if util >= 50:
        return "#d8a200"
    return "#2e9e4f"


def _make_icon_image(util: float | None):
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([2, 2, 62, 62], radius=16, fill=_badge_color(util))
    text = "--" if util is None else str(min(int(round(util)), 100))
    size = 28 if len(text) > 2 else 38
    try:
        font = ImageFont.truetype("arialbd.ttf", size)
    except OSError:
        font = ImageFont.load_default()
    box = d.textbbox((0, 0), text, font=font)
    d.text(
        ((64 - box[2] + box[0]) / 2 - box[0],
         (64 - box[3] + box[1]) / 2 - box[1]),
        text, font=font, fill="white",
    )
    return img


def _fmt_countdown(secs: float | None) -> str:
    if secs is None or secs <= 0:
        return ""
    h, m = divmod(int(secs // 60), 60)
    return f"{h}h{m:02d}m"


class App:
    """Shared state + actions, used by both the tray icon and the panel."""

    def __init__(self) -> None:
        self.usage: UsageSnapshot | None = None
        self.watch_proc: subprocess.Popen | None = None
        self.icon = None
        self._schedule_text = ""
        self._stop = threading.Event()

    # ----- quota -----

    def refresh_usage(self, force: bool = False) -> dict:
        try:
            self.usage = fetch_usage(force=force)
        except RuntimeError:
            self.usage = None
        self._update_icon()
        return {"ok": self.usage is not None}

    def _update_icon(self) -> None:
        if self.icon is None:
            return
        u = self.usage
        util = u.five_hour.utilization if u else None
        self.icon.icon = _make_icon_image(util)
        tip = "nightshift"
        if u:
            bits = []
            for label, w in (("5h", u.five_hour), ("7d", u.seven_day)):
                if w.utilization is None:
                    continue
                s = f"{label} {w.utilization:.0f}%"
                if w.active and w.resets_at:
                    s += f" 重置{w.resets_at.astimezone():%H:%M}"
                bits.append(s)
            tip = "nightshift  " + "  ".join(bits)
        self.icon.title = tip[:120]

    def _refresh_loop(self) -> None:
        from .warmup import maybe_keepwarm

        last_keepwarm = 0.0
        while not self._stop.wait(REFRESH_SEC):
            self.refresh_usage()
            last_keepwarm = maybe_keepwarm(last_keepwarm)

    # ----- panel state -----

    def state(self) -> dict:
        u = self.usage
        usage = None
        if u:
            def win(w):
                return {
                    "utilization": w.utilization,
                    "active": w.active,
                    "resets_local": (
                        f"{w.resets_at.astimezone():%a %H:%M}"
                        if w.resets_at else ""),
                    "countdown": _fmt_countdown(w.seconds_to_reset()),
                }
            usage = {
                "five_hour": win(u.five_hour),
                "seven_day": win(u.seven_day),
                "source": u.source,
                "fetched_local": f"{u.fetched_at.astimezone():%H:%M:%S}",
            }
        weekly = None
        if u:
            from .quota import weekly_projection

            p = weekly_projection(u)
            if p:
                weekly = {
                    "projected": round(p["projected_at_reset"]),
                    "reliable": p["reliable"],
                    "exhaust_local": (
                        f"{p['exhaust_at'].astimezone():%a %H:%M}"
                        if p["exhaust_at"] else ""),
                }
        cfg = load_config()
        kw = cfg.get("keepwarm", {})
        tg = cfg.get("telegram", {})
        running = self.watch_proc is not None and self.watch_proc.poll() is None
        return {
            "usage": usage,
            "weekly": weekly,
            "keepwarm": {"enabled": bool(kw.get("enabled")),
                         "start": kw.get("start", "07:00"),
                         "end": kw.get("end", "23:00")},
            "telegram": {"configured": bool(tg.get("bot_token")
                                            and tg.get("chat_id")),
                         "bot_username": tg.get("bot_username", ""),
                         "chat_id": tg.get("chat_id", ""),
                         "default_cwd": tg.get("default_cwd", "")},
            "warmup_time": (load_config()["warmup"]["times"] or ["07:00"])[0],
            "schedule": self._schedule_text or self._load_schedule_text(),
            "queue": [
                {"id": j.id, "prompt": j.prompt.replace("\n", " ")[:80],
                 "cwd_name": Path(j.cwd).name,
                 "paused": j.paused,
                 "session_short": j.session_id[:8] if j.session_id else ""}
                for j in load_jobs()
            ],
            "watch": {"running": running,
                      "pid": self.watch_proc.pid if running else None},
            "autostart": STARTUP_LNK.exists(),
            "data_dir": str(DATA_DIR),
            "home": str(Path.home()),
        }

    def _load_schedule_text(self) -> str:
        from .schedule_win import list_tasks

        self._schedule_text = list_tasks()
        return self._schedule_text

    # ----- warmup -----

    def apply_warmup(self, t: str) -> dict:
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            return {"ok": False, "message": "时间格式应为 HH:MM，例如 07:00"}
        from .schedule_win import register_daily

        ok = register_daily("warmup", t)
        if ok:
            cfg = load_config()
            cfg["warmup"]["times"] = [t]
            save_config(cfg)
            self._load_schedule_text()
        return {"ok": ok, "message":
                f"已注册：每天 {t} 自动预热（可从睡眠唤醒）" if ok else "注册失败"}

    def remove_warmup(self) -> dict:
        from .schedule_win import unregister_all

        n = unregister_all()
        self._load_schedule_text()
        return {"ok": True, "message": f"已移除 {n} 个计划任务"}

    def suggest_warmup(self) -> dict:
        from .learn import analyze, collect_sessions

        r = analyze(collect_sessions())
        if not r.suggested_warmup:
            return {"ok": False, "message": "历史数据不足，先用几天再试", "time": ""}
        mf = r.median_first_activity or 0
        return {
            "ok": True, "time": r.suggested_warmup,
            "message": (f"建议 {r.suggested_warmup} —— 近{r.days_observed}天"
                        f"首次活动中位 {int(mf):02d}:{int((mf % 1) * 60):02d}，"
                        f"典型时长 {r.median_session_hours:.1f}h"),
        }

    def warmup_now(self) -> dict:
        from .warmup import warmup

        ok = warmup()
        self.refresh_usage(force=True)
        return {"ok": ok}

    # ----- sessions -----

    def sessions(self) -> list[dict]:
        from .sessions import list_recent_sessions

        out = []
        for s in list_recent_sessions(days=7, limit=20):
            out.append({
                "session_id": s.session_id,
                "title": s.title,
                "cwd": s.cwd,
                "cwd_name": Path(s.cwd).name,
                "last_local": f"{s.last_active.astimezone():%m-%d %H:%M}",
                "interrupted": s.interrupted,
                "reason": s.reason,
                "confidence": s.confidence,
                "error_text": s.error_text,
                "trivial": s.trivial,
            })
        return out

    def resume_now(self, session_id: str, prompt: str = "") -> dict:
        """One-click resume of a specific session from the panel."""
        from .resume import classify_transcript
        from .sessions import CLAUDE_PROJECTS

        if not session_id:
            return {"ok": False, "message": "缺少会话 ID"}
        matches = list(CLAUDE_PROJECTS.glob(f"*/{session_id}*.jsonl"))
        if not matches:
            return {"ok": False, "message": "找不到该会话的本地记录"}
        # idle_min=0: explicit user action, resume regardless of recency.
        info = classify_transcript(matches[0], idle_min_threshold=0.0)
        if info is None:
            from .resume import InterruptedSession

            # Not detected as interrupted, but the user asked - build a
            # minimal descriptor from the transcript's recorded cwd.
            import json as _json

            cwd = str(Path.home())
            try:
                for line in matches[0].read_text(
                        encoding="utf-8", errors="replace").splitlines():
                    d = _json.loads(line)
                    if d.get("cwd"):
                        cwd = d["cwd"]
                        break
            except (OSError, _json.JSONDecodeError):
                pass
            from datetime import datetime, timezone

            info = InterruptedSession(
                session_id, cwd, datetime.now(timezone.utc),
                "manual resume", matches[0], reason="manual",
                confidence="manual")

        def work():
            from .resume import resume_session

            resume_session(info, prompt=prompt)
        threading.Thread(target=work, daemon=True).start()
        return {"ok": True, "message":
                f"已在后台续跑 {session_id[:8]}（日志见 logs/resume-*.log）"}

    # ----- keepwarm / telegram settings -----

    def set_keepwarm(self, enabled: bool, start: str, end: str) -> dict:
        for t in (start, end):
            if not re.fullmatch(r"\d{1,2}:\d{2}", t):
                return {"ok": False, "message": "时间格式应为 HH:MM"}
        cfg = load_config()
        cfg.setdefault("keepwarm", {})
        cfg["keepwarm"].update(enabled=enabled, start=start, end=end)
        save_config(cfg)
        return {"ok": True, "message":
                f"保温{'开启' if enabled else '关闭'}（{start}–{end}）"}

    def set_telegram(self, token: str, chat: str, default_cwd: str) -> dict:
        from .tgbot import (get_bot_username, mark_offset_current,
                            resolve_chat_id, start_polling)

        cfg = load_config()
        tg = cfg.setdefault("telegram", {})
        prev_token, prev_chat = tg.get("bot_token", ""), tg.get("chat_id", "")
        # Empty fields mean "keep what's already saved" so re-saving (e.g. to
        # change the default dir) doesn't require retyping the token.
        token = token.strip() or prev_token
        chat = chat.strip() or prev_chat

        # cwd-only update on an already-connected bot: save quietly, no ping.
        if token and chat and token == prev_token and chat == prev_chat:
            tg["default_cwd"] = default_cwd.strip()
            save_config(cfg)
            return {"ok": True, "message": "默认目录已保存"}

        if token and not chat:
            # Auto-detect: whoever last messaged the bot.
            chat = resolve_chat_id(token) or ""
            if not chat:
                tg["default_cwd"] = default_cwd.strip()
                save_config(cfg)
                return {"ok": False, "message":
                        "先在手机上给机器人发一条任意消息，再点保存（会自动识别）"}

        username = get_bot_username(token) if token else None
        tg.update(bot_token=token, chat_id=chat, default_cwd=default_cwd.strip(),
                  bot_username=username or tg.get("bot_username", ""))
        save_config(cfg)

        if token and chat:
            from .notify import notify

            mark_offset_current(token)  # don't replay pre-connect messages
            ok = notify("[nightshift] 连接成功 ✓ 发 /help 看指令，或直接发任意"
                        "文字给我，我会把它排成一个任务在睡前队列里。")
            start_polling()
            return {"ok": ok, "message":
                    f"已连接 @{username or '?'}（chat {chat}），测试消息已发送"
                    if ok else "已保存，但测试消息失败（token 可能无效）"}
        return {"ok": True, "message": "已清空 Telegram 配置"}

    def tg_push(self, what: str) -> dict:
        """Push status/queue to the phone on demand (one-tap from the panel)."""
        from .notify import notify

        if what == "status":
            from .quota import fetch_usage, format_snapshot

            try:
                text = format_snapshot(fetch_usage())
            except RuntimeError as e:
                text = f"额度获取失败: {e}"
        elif what == "queue":
            from .jobs import format_jobs, load_jobs

            text = format_jobs(load_jobs())
        else:
            return {"ok": False, "message": "unknown"}
        ok = notify(text)
        return {"ok": ok, "message": "已推送到手机" if ok else "推送失败（未配置或网络）"}

    # ----- history -----

    def history(self) -> list[dict]:
        import json as _json

        from .jobs import DONE_DIR, FAILED_DIR

        items = []
        for d, status in ((DONE_DIR, "done"), (FAILED_DIR, "failed")):
            if not d.exists():
                continue
            for p in d.glob("*.json"):
                try:
                    data = _json.loads(p.read_text(encoding="utf-8"))
                except (OSError, _json.JSONDecodeError):
                    continue
                items.append({
                    "id": data.get("id", p.stem),
                    "prompt": (data.get("prompt") or "").replace("\n", " ")[:70],
                    "status": status,
                    "mtime": p.stat().st_mtime,
                })
        items.sort(key=lambda x: x["mtime"], reverse=True)
        for it in items:
            it["when"] = time.strftime("%m-%d %H:%M", time.localtime(it.pop("mtime")))
        return items[:10]

    def job_log(self, job_id: str, status: str) -> dict:
        from .jobs import DONE_DIR, FAILED_DIR

        d = DONE_DIR if status == "done" else FAILED_DIR
        # job_id comes from our own directory listing, but never trust it
        # as a path component.
        if not job_id or "/" in job_id or "\\" in job_id or ".." in job_id:
            return {"ok": False, "text": "bad id"}
        p = d / f"{job_id}.log"
        if not p.exists():
            return {"ok": False, "text": "log not found"}
        return {"ok": True, "text": p.read_text(encoding="utf-8",
                                                errors="replace")[-8000:]}

    # ----- queue -----

    def add_job(self, prompt: str, cwd: str, session_id: str = "",
                create_dir: bool = False) -> dict:
        prompt = prompt.strip()
        if not prompt:
            return {"ok": False, "message": "提示词为空"}
        cwd = cwd.strip() or str(Path.home())
        if not Path(cwd).is_dir():
            if create_dir:
                try:
                    Path(cwd).mkdir(parents=True)
                except OSError as e:
                    return {"ok": False, "message": f"创建目录失败: {e}"}
            else:
                return {"ok": False,
                        "message": f"目录不存在: {cwd}（勾选'自动创建'可新建项目）"}
        job = new_job(prompt, cwd=cwd, session_id=session_id)
        return {"ok": True, "id": job.id}

    def remove_job(self, job_id: str) -> dict:
        return {"ok": jobs_remove(job_id)}

    def get_job(self, job_id: str) -> dict:
        from .jobs import get_job as jobs_get

        j = jobs_get(job_id)
        if j is None:
            return {"ok": False, "message": "找不到该任务"}
        return {"ok": True, "id": j.id, "prompt": j.prompt, "cwd": j.cwd,
                "session_id": j.session_id}

    def update_job(self, job_id: str, prompt: str, cwd: str) -> dict:
        from .jobs import update_job as jobs_update

        prompt = (prompt or "").strip()
        if not prompt:
            return {"ok": False, "message": "提示词为空"}
        cwd = (cwd or "").strip() or str(Path.home())
        if not Path(cwd).is_dir():
            return {"ok": False, "message": f"目录不存在: {cwd}"}
        ok = jobs_update(job_id, prompt=prompt, cwd=cwd)
        return {"ok": ok, "message": "已保存修改" if ok else "找不到该任务"}

    def reorder_jobs(self, ids: list) -> dict:
        from .jobs import reorder_jobs as jobs_reorder

        return {"ok": True, "n": jobs_reorder([str(i) for i in ids])}

    def pin_job(self, job_id: str) -> dict:
        from .jobs import pin_job as jobs_pin

        return {"ok": jobs_pin(job_id)}

    def pause_job(self, job_id: str, paused: bool) -> dict:
        from .jobs import set_paused

        return {"ok": set_paused(job_id, paused)}

    # ----- watch -----

    def toggle_watch(self) -> dict:
        if self.watch_proc is not None and self.watch_proc.poll() is None:
            self.watch_proc.terminate()
            self.watch_proc = None
            return {"ok": True, "running": False}
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        log = (DATA_DIR / "logs" / "watch_gui.log").open("a", encoding="utf-8")
        self.watch_proc = subprocess.Popen(
            [sys.executable, "-m", "nightshift", "watch"],
            cwd=Path(__file__).resolve().parent.parent,
            stdout=log, stderr=subprocess.STDOUT, creationflags=flags,
        )
        return {"ok": True, "running": True, "pid": self.watch_proc.pid}

    # ----- autostart -----

    def set_autostart(self, enabled: bool) -> dict:
        if enabled:
            pythonw = Path(sys.executable).with_name("pythonw.exe")
            exe = pythonw if pythonw.exists() else Path(sys.executable)
            ps = (
                "$ws = New-Object -ComObject WScript.Shell;"
                f"$l = $ws.CreateShortcut('{STARTUP_LNK}');"
                f"$l.TargetPath = '{exe}';"
                "$l.Arguments = '-m nightshift tray --no-open';"
                f"$l.WorkingDirectory = "
                f"'{Path(__file__).resolve().parent.parent}';"
                "$l.Save()"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True)
            return {"ok": r.returncode == 0}
        STARTUP_LNK.unlink(missing_ok=True)
        return {"ok": True}

    def create_shortcut(self) -> dict:
        ok, info = install_launcher()
        if not ok:
            return {"ok": False, "message": info or "创建失败"}
        where = f"（桌面：{info}）" if info else ""
        return {"ok": True, "message":
                f"已创建「Sleep Well」启动图标，进程被关掉后双击它（或开始菜单"
                f"搜索 Sleep Well）即可重新启动{where}"}

    # ----- lifecycle -----

    def quit(self) -> None:
        self._stop.set()
        if self.watch_proc is not None and self.watch_proc.poll() is None:
            self.watch_proc.terminate()
        if self.icon is not None:
            self.icon.stop()


def run_gui(open_browser: bool = True) -> None:
    from .webpanel import start_server

    cfg = load_config()
    port = int(cfg.get("gui", {}).get("port", 8377))
    app = App()
    url = f"http://127.0.0.1:{port}/"
    try:
        start_server(app, port)
    except OSError:
        # Port taken: assume another instance is alive, just open its panel.
        print(f"panel already running at {url}")
        webbrowser.open(url)
        return

    print(f"panel: {url}")
    app.refresh_usage()
    threading.Thread(target=app._refresh_loop, daemon=True).start()
    from .tgbot import start_polling

    start_polling()  # two-way telegram control, no-op if unconfigured
    if open_browser:
        webbrowser.open(url)

    # watch（监听任务队列）默认开启
    if app.watch_proc is None:
        threading.Thread(target=app.toggle_watch, daemon=True).start()

    if HAS_TRAY:
        def _watch_running() -> bool:
            return (app.watch_proc is not None
                    and app.watch_proc.poll() is None)

        menu = pystray.Menu(
            pystray.MenuItem("打开面板", lambda: webbrowser.open(url),
                             default=True),
            pystray.MenuItem("立即预热", lambda: threading.Thread(
                target=app.warmup_now, daemon=True).start()),
            pystray.MenuItem("刷新用量", lambda: threading.Thread(
                target=lambda: app.refresh_usage(force=True),
                daemon=True).start()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("监听任务队列", lambda: app.toggle_watch(),
                             checked=lambda item: _watch_running()),
            pystray.MenuItem(
                "开机自启",
                lambda: app.set_autostart(not STARTUP_LNK.exists()),
                checked=lambda item: STARTUP_LNK.exists()),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("退出", lambda: app.quit()),
        )
        app.icon = pystray.Icon("claude-nightshift", _make_icon_image(None),
                                "nightshift", menu)
        app._update_icon()
        app.icon.run()  # blocks until 退出
    else:
        print("提示: pip install pystray pillow 可获得任务栏托盘图标。"
              "Ctrl+C 退出。")
        try:
            while True:
                time.sleep(3600)
        except KeyboardInterrupt:
            app.quit()
