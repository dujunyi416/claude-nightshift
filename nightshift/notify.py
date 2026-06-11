"""Optional Telegram notifications (job finished / failed / queue drained)."""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from .config import load_config


def _tg_settings() -> tuple[str, str]:
    cfg = load_config()["telegram"]
    token = os.environ.get("NIGHTSHIFT_TG_TOKEN") or cfg.get("bot_token", "")
    chat = os.environ.get("NIGHTSHIFT_TG_CHAT") or cfg.get("chat_id", "")
    return token, chat


def notify(text: str) -> bool:
    """Send a Telegram message. Silently no-ops when unconfigured."""
    token, chat = _tg_settings()
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({"chat_id": chat, "text": text[:4000]}).encode()
    try:
        with urllib.request.urlopen(url, data=data, timeout=15) as resp:
            return json.load(resp).get("ok", False)
    except OSError:
        return False
