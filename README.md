# Sleep Well 🌙

*Queue it, let it go, sleep well.*

**Quota-aware automation for Claude Code subscriptions.** Stop babysitting your rate limits. Sleep Well pre-warms your 5-hour window before you wake up, runs your backlog while you sleep, and picks up any session the quota cut off — automatically, the moment your window resets.

> **Sleep Well** is named after the melatonin you take before bed — and it does the same thing for your head. The reason you can't sleep is the unfinished task still running laps in your mind. Hand it to the queue and that loop closes: your brain lets go, Sleep Well runs the job in the quota window you'd have slept through, and you wake up to it already done.

> The CLI command, Python package, and data folder are still named `nightshift` (so existing setups keep working); "Sleep Well" is the display name.

[中文文档 →](README.zh-CN.md)

Zero dependencies (Python 3.10+ standard library only). Windows-first (Task Scheduler integration), core commands work anywhere Claude Code runs.

---

## The Problem Nobody Talks About

Claude subscriptions meter usage in **rolling 5-hour windows** that start from your *first message* — not from when you sit down to work. Three hours of coding later, you hit the cap. Then you wait.

That creates three inefficiencies you can actually fix:

**1. Your window starts too late.**
Wake at 9:00, send your first message at 9:05, and your window runs 9:05–14:05. If a scheduled ping had fired at 7:00, the window would reset at 12:00 — right when a heavy user hits the cap anyway. That's ~2 free hours of quota every morning, recovered while you slept.

**2. The night is wasted.**
You're rate-limited at 23:00. The window resets at 01:00. Nobody's awake to use it. Queue your prompts before bed; Sleep Well executes them headlessly the moment quota returns.

**3. Interrupted work just sits there.**
The limit cuts a session off mid-task and it stalls — until you notice it hours later. Sleep Well scans transcripts for cutoffs and resumes automatically on reset.

---

## Install

```
pip install "claude-nightshift[tray] @ git+https://github.com/dujunyi416/claude-nightshift"
nightshift status
```

Or clone and run without installing anything:

```
git clone https://github.com/dujunyi416/claude-nightshift
cd claude-nightshift
python -m nightshift status
```

Requires [Claude Code](https://code.claude.com) logged in with a Pro/Max subscription — Sleep Well reads the same local OAuth credential the CLI uses. Nothing is sent anywhere except Anthropic's own usage endpoint.

Optionally `pip install -e .` to get a global `nightshift` command.

---

## Commands

| Command | What it does |
|---|---|
| `nightshift status` | 5h / 7d quota, % used, reset countdown |
| `nightshift warmup` | Minimal haiku ping to start the 5h window (skips if already active) |
| `nightshift add "prompt" --cwd DIR` | Queue a job for later |
| `nightshift queue` / `remove ID` | Inspect or edit the queue |
| `nightshift run` | Wait for quota, then drain the queue headlessly |
| `nightshift resume` | Find and continue sessions the limit cut off |
| `nightshift watch` | Autopilot: auto-resume + drain queue whenever quota allows |
| `nightshift learn` | Analyze your usage rhythm, suggest a warmup time |
| `nightshift schedule --warmup 07:00` | Register a daily Windows task (wakes PC from sleep) |
| `nightshift schedule --auto` | Learn the warmup time from your history, then schedule it |
| `nightshift tray` | System tray icon + web settings panel |
| `nightshift config` | Show config file path and current settings |

---

## Three Workflows

### 1. Morning Pre-Warm — recover 2 hours for free

```
nightshift schedule --warmup 07:00     # or: nightshift schedule --auto
```

Registers a Windows scheduled task (with *wake-from-sleep* enabled) that sends one minimal haiku-model prompt at 07:00. Cost: a fraction of a percent of quota. Effect: your 5h window runs 07:00–12:00 instead of 09:00–14:00, so a fresh window is already waiting when the first one would have run dry.

`--auto` infers the optimal time from your last 30 days of session history — `nightshift learn` shows the full analysis with an hourly activity histogram.

### 2. Bedtime Queue — run work while you sleep

```
nightshift add "Refactor data/loader.py per TODOs, run tests, fix failures" --cwd D:\myproject
nightshift add "Write docstrings for every public function in utils/" --cwd D:\myproject
nightshift run        # leave it running, go to sleep
```

`run` polls your quota. When the window resets, it executes each job as `claude -p <prompt>` in the job's working directory, archives everything to `~/.nightshift/done/` (or `failed/`), and optionally pings you on Telegram. Jobs that hit the limit mid-run are re-queued for the next window — not marked failed.

### 3. Auto-Resume Interrupted Work — the overnight autopilot

```
nightshift watch
```

When the quota limit cuts a session, `watch` detects it — either from an explicit error marker in the transcript, or from a session that ended mid-tool-call and has been idle for 5+ minutes (the idle threshold ensures it never touches an active session). Once quota returns, it continues each one with `claude -p --resume <session-id>` in its original directory, then drains the prompt queue.

A state file guarantees each interruption is resumed at most once. If the resumed run hits the limit again, the next reset picks it up — so long tasks crawl forward window after window while you sleep.

---

## Tray Icon + Settings Panel

```
pip install pystray pillow     # tray dependencies (the panel itself is stdlib)
nightshift tray
```

A color-coded badge in your system tray shows your 5h utilization at a glance — green → yellow → orange → red as you approach the cap, refreshing every minute. Left-click opens a local web panel (`127.0.0.1:8377`) where everything is one click:

- Both quota windows with live reset countdowns
- **Recent sessions list** — same titles as the app's Recents, with rate-limit-interrupted sessions highlighted in red. Click one and your queued prompt *continues that session* (`claude -p --resume`) instead of starting fresh.
- Type bedtime prompts straight into the queue; non-existent directories can be auto-created ("new project" mode)
- Run history with one-click log viewing
- Set or remove the daily warmup time, or learn it from your history automatically
- **Weekly budget line** — at your current burn rate, will the 7-day quota survive until reset? If not, when does it run out?
- **Keep-warm mode** — during your awake hours, re-activate the 5h window the moment it goes idle. A 16:00 reset is warmed at 16:00, not when you notice at 16:30. Outside those hours it stays quiet.
- **Telegram two-way control** — get job-done pings, and reply from bed: `/status`, `/queue`, `/resume`, `/warmup`, or send any text to queue it as a job.
- Start / stop the watch autopilot
- "Start with Windows" toggle (creates a Startup shortcut)

Without pystray/pillow the panel still works — `nightshift tray` simply opens it in your browser.

---

## How It Reads Your Quota

Two sources, in priority order:

**1. OAuth usage endpoint** — `GET https://api.anthropic.com/api/oauth/usage` with the Bearer token from `~/.claude/.credentials.json` and the `anthropic-beta: oauth-2025-04-20` header. Returns `five_hour` / `seven_day` blocks with `utilization` (%) and `resets_at`. Responses are cached (60s default); on errors Sleep Well degrades to stale cache rather than crashing.

**2. Statusline snapshot** (optional, zero-network) — Claude Code pipes `rate_limits` into statusline commands. Add to `~/.claude/settings.json`:

```json
{"statusLine": {"type": "command", "command": "python -m nightshift statusline"}}
```

Sleep Well renders a compact status line *and* captures each update to `~/.nightshift/usage_snapshot.json` as a fallback quota source.

The OAuth token expires every few hours and is refreshed whenever any `claude` command runs — which the warmup ping conveniently is. Sleep Well never writes to Claude's credential file.

---

## Configuration

`nightshift config` creates and prints `~/.nightshift/config.json`:

```jsonc
{
  "claude_cmd": "",                    // empty = auto-detect
  "warmup":  { "times": ["07:00"], "model": "haiku",
               "prompt": "Reply with exactly: ok", "skip_if_active": true },
  "runner":  { "start_when": "reset",  // or "now"
               "stop_utilization": 95, "job_timeout_min": 240,
               "permission_mode": "acceptEdits", "poll_interval_sec": 300 },
  "resume":  { "enabled": true, "lookback_hours": 24,
               "permission_mode": "acceptEdits", "max_sessions": 3,
               "prompt": "You were interrupted by the usage limit. Continue..." },
  "quota":   { "cache_ttl_sec": 60 },
  "telegram": { "bot_token": "", "chat_id": "" }   // or env NIGHTSHIFT_TG_TOKEN / _CHAT
}
```

All state lives under `~/.nightshift/` (override with `NIGHTSHIFT_HOME`): `queue/`, `done/`, `failed/`, `logs/`, `usage_cache.json`, `resumed.json`.

---

## Safety Notes

- Queued jobs and resumes run with `--permission-mode acceptEdits` by default: Claude can edit files in the job's directory, but risky shell commands fail rather than prompt. Set `bypassPermissions` only if you fully trust the prompts you queue — this is unattended execution.
- The usage endpoint is **undocumented** and could change. Everything else — queue, runner, resume, scheduling — keeps working without it, because the Claude CLI itself enforces limits and Sleep Well detects that from the output.
- The warmup ping costs ~nothing, but it *does* start a 5h window. If your morning is unpredictable, `--auto` learning or a later time avoids warming a window you then sleep through.

---

## Tests

```
python -m unittest discover -s tests
```

## License

MIT
