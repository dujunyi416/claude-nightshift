"""Quota tracking via the OAuth usage endpoint (what claude-hud-style
statuslines use as their fallback source).

GET https://api.anthropic.com/api/oauth/usage
  Authorization: Bearer <token from ~/.claude/.credentials.json>
  anthropic-beta: oauth-2025-04-20

Response (observed 2026-06):
  {"five_hour": {"utilization": 59.0, "resets_at": "2026-06-10T21:40:00+00:00"},
   "seven_day": {"utilization": 36.0, "resets_at": "..."}, ...}

This endpoint is undocumented and community-discovered; it may change or
rate-limit (429). We therefore cache responses, back off on errors, and can
fall back to a snapshot file written by our optional statusline hook.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .config import DATA_DIR, load_config
from .credentials import load_creds

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
CACHE_PATH = DATA_DIR / "usage_cache.json"
SNAPSHOT_PATH = DATA_DIR / "usage_snapshot.json"  # written by statusline hook


@dataclass
class Window:
    """One rate-limit window (5-hour session or 7-day weekly)."""

    utilization: float | None  # percent, 0-100+
    resets_at: datetime | None

    @property
    def active(self) -> bool:
        """A window is active if it has usage and hasn't reset yet."""
        return (
            self.utilization is not None
            and self.utilization > 0
            and self.resets_at is not None
            and self.resets_at > datetime.now(timezone.utc)
        )

    @property
    def exhausted(self) -> bool:
        return self.active and (self.utilization or 0) >= 100

    def seconds_to_reset(self) -> float | None:
        if self.resets_at is None:
            return None
        return (self.resets_at - datetime.now(timezone.utc)).total_seconds()


@dataclass
class UsageSnapshot:
    five_hour: Window
    seven_day: Window
    fetched_at: datetime
    source: str = "api"  # api | cache | statusline
    raw: dict = field(default_factory=dict)


def _parse_window(block: dict | None) -> Window:
    if not block:
        return Window(utilization=None, resets_at=None)
    resets = block.get("resets_at")
    resets_dt = None
    if resets:
        resets_dt = datetime.fromisoformat(resets)
        if resets_dt.tzinfo is None:
            resets_dt = resets_dt.replace(tzinfo=timezone.utc)
    return Window(utilization=block.get("utilization"), resets_at=resets_dt)


def parse_usage(raw: dict, source: str = "api",
                fetched_at: datetime | None = None) -> UsageSnapshot:
    return UsageSnapshot(
        five_hour=_parse_window(raw.get("five_hour")),
        seven_day=_parse_window(raw.get("seven_day")),
        fetched_at=fetched_at or datetime.now(timezone.utc),
        source=source,
        raw=raw,
    )


def _read_cache(ttl: float) -> UsageSnapshot | None:
    try:
        cached = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
        age = time.time() - cached["ts"]
        if age <= ttl:
            return parse_usage(
                cached["raw"], source="cache",
                fetched_at=datetime.fromtimestamp(cached["ts"], timezone.utc),
            )
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _write_cache(raw: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(
        json.dumps({"ts": time.time(), "raw": raw}), encoding="utf-8"
    )


def _read_statusline_snapshot(max_age_sec: float = 600) -> UsageSnapshot | None:
    """Fallback: rate_limits captured from Claude Code's statusline stdin."""
    try:
        snap = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
        if time.time() - snap["ts"] <= max_age_sec:
            return parse_usage(
                snap["raw"], source="statusline",
                fetched_at=datetime.fromtimestamp(snap["ts"], timezone.utc),
            )
    except (OSError, json.JSONDecodeError, KeyError, ValueError):
        pass
    return None


def _explain_fetch_error(e: Exception) -> str:
    """Translate a fetch failure into a short Chinese reason for the UI."""
    if isinstance(e, urllib.error.HTTPError):
        if e.code == 401:
            return "token 已过期（HTTP 401）"
        if e.code == 429:
            return "被限流（HTTP 429），稍后再试"
        return f"HTTP {e.code}"
    if isinstance(e, json.JSONDecodeError):
        return "返回内容不是合法 JSON"
    reason = getattr(e, "reason", None) or e
    return f"网络错误：{reason}"


def fetch_usage(force: bool = False, ttl: float | None = None) -> UsageSnapshot:
    """Fetch quota, preferring fresh cache, then the API, then fallbacks.

    When force=True the API failure is raised (no silent fallback to stale
    cache) — callers asking for fresh data deserve to know it didn't happen.
    When force=False we degrade gracefully through stale cache and the
    statusline snapshot before raising.
    """
    cfg = load_config()
    ttl = ttl if ttl is not None else cfg["quota"]["cache_ttl_sec"]

    if not force:
        cached = _read_cache(ttl)
        if cached:
            return cached

    creds = load_creds()
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {creds.access_token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-nightshift",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = json.load(resp)
        _write_cache(raw)
        return parse_usage(raw)
    except (urllib.error.URLError, urllib.error.HTTPError, OSError,
            json.JSONDecodeError) as e:
        if force:
            raise RuntimeError(_explain_fetch_error(e)) from e
        stale = _read_cache(ttl=86400)
        if stale:
            stale.source = "cache(stale)"
            return stale
        snap = _read_statusline_snapshot(max_age_sec=86400)
        if snap:
            return snap
        raise RuntimeError(f"usage endpoint unreachable and no cache: {e}") from e


def weekly_projection(s: UsageSnapshot,
                      now: datetime | None = None) -> dict | None:
    """Budget view of the 7-day window: at the current burn rate, when does
    the weekly quota run out (or how much will be used by reset)?

    Returns None when there's no usable data, or a dict:
      {"used": %, "elapsed_h": h, "projected_at_reset": %,
       "exhaust_at": datetime|None, "reliable": bool}
    """
    w = s.seven_day
    if w.utilization is None or w.resets_at is None:
        return None
    now = now or datetime.now(timezone.utc)
    start = w.resets_at - timedelta(days=7)
    elapsed_h = (now - start).total_seconds() / 3600
    if elapsed_h <= 0:
        return None
    burn_per_h = w.utilization / elapsed_h
    projected = burn_per_h * 168
    exhaust_at = None
    if burn_per_h > 0:
        eta = start + timedelta(hours=100 / burn_per_h)
        if eta < w.resets_at:
            exhaust_at = eta
    return {
        "used": w.utilization,
        "elapsed_h": elapsed_h,
        "projected_at_reset": projected,
        "exhaust_at": exhaust_at,
        # Early in the window a few heavy hours wildly skew the rate.
        "reliable": elapsed_h >= 12,
    }


def format_snapshot(s: UsageSnapshot) -> str:
    """Human-readable multi-line summary in local time."""
    lines = []
    now_local = datetime.now().astimezone()
    offset = now_local.strftime("%z")
    offset = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    lines.append(
        f"Quota as of {now_local:%Y-%m-%d %H:%M} {offset} (source: {s.source})"
    )
    for label, w in (("5-hour window", s.five_hour), ("7-day window ", s.seven_day)):
        if w.utilization is None:
            lines.append(f"  {label}: no data")
            continue
        state = "ACTIVE" if w.active else "idle"
        if w.exhausted:
            state = "EXHAUSTED"
        line = f"  {label}: {w.utilization:5.1f}% used [{state}]"
        secs = w.seconds_to_reset()
        if w.resets_at and secs is not None and secs > 0:
            local = w.resets_at.astimezone()
            h, m = divmod(int(secs // 60), 60)
            line += f"  resets {local:%a %H:%M} (in {h}h{m:02d}m)"
        lines.append(line)
    if not s.five_hour.active:
        lines.append("  note: no active 5h window - your next message starts one.")
    if "stale" in s.source or s.source == "statusline":
        try:
            if load_creds().expired:
                lines.append(
                    "  warning: OAuth token expired, live data unavailable - "
                    "any 'claude' command (e.g. nightshift warmup) refreshes it."
                )
        except (OSError, KeyError, ValueError):
            pass
    return "\n".join(lines)
