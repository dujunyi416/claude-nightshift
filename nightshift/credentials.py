"""Read the Claude Code OAuth token from ~/.claude/.credentials.json.

We never refresh or modify the token ourselves; Claude Code rotates it
whenever the CLI runs. If the token has expired, the fix is simply to run
any `claude` command (which our warmup ping does anyway).
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"


@dataclass
class OAuthCreds:
    access_token: str
    expires_at_ms: int
    subscription_type: str
    rate_limit_tier: str

    @property
    def expired(self) -> bool:
        return time.time() * 1000 >= self.expires_at_ms

    @property
    def minutes_left(self) -> float:
        return (self.expires_at_ms / 1000 - time.time()) / 60


def load_creds(path: Path | None = None) -> OAuthCreds:
    p = path or CREDENTIALS_PATH
    data = json.loads(p.read_text(encoding="utf-8"))
    oauth = data["claudeAiOauth"]
    return OAuthCreds(
        access_token=oauth["accessToken"],
        expires_at_ms=oauth.get("expiresAt", 0),
        subscription_type=oauth.get("subscriptionType", "unknown"),
        rate_limit_tier=oauth.get("rateLimitTier", "unknown"),
    )
