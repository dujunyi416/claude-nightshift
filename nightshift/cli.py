"""Command-line interface: python -m nightshift <command>."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import CONFIG_PATH, DATA_DIR, ensure_dirs, load_config, save_config


def cmd_status(args: argparse.Namespace) -> int:
    from .quota import fetch_usage, format_snapshot

    try:
        usage = fetch_usage(force=args.fresh)
    except RuntimeError as e:
        print(f"error: {e}")
        return 1
    print(format_snapshot(usage))
    if args.json:
        print(json.dumps(usage.raw, indent=2))
    return 0


def cmd_warmup(args: argparse.Namespace) -> int:
    from .warmup import warmup

    return 0 if warmup(force=args.force) else 1


def cmd_add(args: argparse.Namespace) -> int:
    from .jobs import new_job

    prompt = args.prompt
    if prompt == "-":
        prompt = sys.stdin.read().strip()
    if not prompt:
        print("error: empty prompt")
        return 1
    job = new_job(
        prompt=prompt,
        cwd=args.cwd or os.getcwd(),
        model=args.model,
        permission_mode=args.permission_mode,
        priority=args.priority,
        timeout_min=args.timeout,
    )
    print(f"queued job {job.id}")
    print(f"  cwd: {job.cwd}")
    print("run the queue with:  nightshift run")
    return 0


def cmd_queue(args: argparse.Namespace) -> int:
    from .jobs import format_jobs, load_jobs

    print(format_jobs(load_jobs()))
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    from .jobs import remove_job

    if remove_job(args.job_id):
        print(f"removed {args.job_id}")
        return 0
    print(f"no queued job matching '{args.job_id}'")
    return 1


def cmd_run(args: argparse.Namespace) -> int:
    from .runner import run_queue

    run_queue(when=args.when, once=args.once)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    from .resume import format_pending, pending_resumes, resume_session

    cfg = load_config()
    lookback = args.lookback or cfg["resume"]["lookback_hours"]
    sessions = pending_resumes(lookback)
    print(format_pending(sessions))
    if args.scan or not sessions:
        return 0
    from .quota import fetch_usage

    try:
        usage = fetch_usage(force=True)
        if usage.five_hour.exhausted:
            local = usage.five_hour.resets_at.astimezone().strftime("%H:%M") \
                if usage.five_hour.resets_at else "?"
            print(f"5h window exhausted (resets {local}); "
                  "use 'nightshift watch' to resume automatically on reset.")
            return 1
    except RuntimeError:
        pass
    ok = 0
    for s in sessions[: cfg["resume"]["max_sessions"]]:
        if resume_session(s, cfg):
            ok += 1
    print(f"resumed {ok}/{len(sessions)} session(s).")
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    from .runner import watch

    try:
        watch(poll_sec=args.poll)
    except KeyboardInterrupt:
        print("\nwatch stopped.")
    return 0


def cmd_learn(args: argparse.Namespace) -> int:
    from .learn import analyze, collect_sessions, format_rhythm

    spans = collect_sessions(days=args.days)
    print(format_rhythm(analyze(spans)))
    return 0


def cmd_schedule(args: argparse.Namespace) -> int:
    if os.name != "nt":
        print("automatic scheduling is implemented for Windows only; "
              "use cron on other systems (see README).")
        return 1
    from .schedule_win import list_tasks, register_daily

    did_something = False
    if args.auto:
        from .learn import analyze, collect_sessions

        rhythm = analyze(collect_sessions())
        if not rhythm.suggested_warmup:
            print("not enough session history to learn a schedule; "
                  "pass --warmup HH:MM instead.")
            return 1
        print(f"learned warmup time: {rhythm.suggested_warmup}")
        args.warmup = rhythm.suggested_warmup
    if args.warmup:
        register_daily("warmup", args.warmup)
        cfg = load_config()
        cfg["warmup"]["times"] = [args.warmup]
        save_config(cfg)
        did_something = True
    if args.runner:
        register_daily("run", args.runner)
        did_something = True
    if not did_something:
        print(list_tasks())
    return 0


def cmd_unschedule(args: argparse.Namespace) -> int:
    if os.name != "nt":
        print("nothing to do (Windows only).")
        return 0
    from .schedule_win import unregister_all

    unregister_all()
    return 0


def cmd_statusline(args: argparse.Namespace) -> int:
    from .statusline import run_statusline

    run_statusline()
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config()
    if not CONFIG_PATH.exists():
        save_config(cfg)
        print(f"created default config at {CONFIG_PATH}")
    print(f"config file: {CONFIG_PATH}")
    print(f"data dir:    {DATA_DIR}")
    print(json.dumps(cfg, indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="nightshift",
        description="Quota-aware automation for Claude Code: check limits, "
                    "pre-warm the 5h window, queue prompts overnight.",
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    sp = sub.add_parser("status", help="show 5h/7d quota and reset times")
    sp.add_argument("--fresh", action="store_true", help="bypass cache")
    sp.add_argument("--json", action="store_true", help="also dump raw JSON")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("warmup", help="ping claude to start the 5h window")
    sp.add_argument("--force", action="store_true",
                    help="ping even if a window is already active")
    sp.set_defaults(func=cmd_warmup)

    sp = sub.add_parser("add", help="queue a prompt for later execution")
    sp.add_argument("prompt", help="the prompt text, or '-' to read stdin")
    sp.add_argument("--cwd", default="", help="working directory for the job")
    sp.add_argument("--model", default="", help="model override (e.g. opus)")
    sp.add_argument("--permission-mode", default="",
                    help="claude permission mode (default from config)")
    sp.add_argument("--priority", type=int, default=5, help="1=first, 9=last")
    sp.add_argument("--timeout", type=int, default=0, help="minutes (0=config)")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("queue", help="list queued jobs")
    sp.set_defaults(func=cmd_queue)

    sp = sub.add_parser("remove", help="remove a queued job by id (or prefix)")
    sp.add_argument("job_id")
    sp.set_defaults(func=cmd_remove)

    sp = sub.add_parser("run", help="wait for quota, then drain the queue")
    sp.add_argument("--when", choices=["reset", "now"], default="",
                    help="start on window reset (default) or immediately")
    sp.add_argument("--once", action="store_true", help="run one job and stop")
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("resume",
                        help="resume sessions that were cut off by the limit")
    sp.add_argument("--scan", action="store_true",
                    help="only list interrupted sessions, don't resume")
    sp.add_argument("--lookback", type=float, default=0,
                    help="hours to look back (default from config)")
    sp.set_defaults(func=cmd_resume)

    sp = sub.add_parser("watch",
                        help="autopilot: auto-resume cut-off sessions and "
                             "drain the queue whenever quota allows")
    sp.add_argument("--poll", type=float, default=0,
                    help="seconds between checks (default from config)")
    sp.set_defaults(func=cmd_watch)

    sp = sub.add_parser("learn", help="analyze your rhythm, suggest warmup time")
    sp.add_argument("--days", type=int, default=30, help="lookback window")
    sp.set_defaults(func=cmd_learn)

    sp = sub.add_parser("schedule",
                        help="register daily Windows tasks (no args: list)")
    sp.add_argument("--warmup", metavar="HH:MM", default="",
                    help="daily warmup time")
    sp.add_argument("--runner", metavar="HH:MM", default="",
                    help="daily queue-runner start time")
    sp.add_argument("--auto", action="store_true",
                    help="learn the warmup time from your history")
    sp.set_defaults(func=cmd_schedule)

    sp = sub.add_parser("unschedule", help="remove all nightshift tasks")
    sp.set_defaults(func=cmd_unschedule)

    sp = sub.add_parser("statusline",
                        help="(for Claude Code settings.json) statusline hook")
    sp.set_defaults(func=cmd_statusline)

    sp = sub.add_parser("config", help="show config path and current values")
    sp.set_defaults(func=cmd_config)

    return p


def main(argv: list[str] | None = None) -> int:
    ensure_dirs()
    args = build_parser().parse_args(argv)
    return args.func(args)
