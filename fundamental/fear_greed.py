"""
fundamental/fear_greed.py — ported from clean v5's plugin_fear_greed.py.
Free alternative.me Fear & Greed Index. Context-only adjustment, see
fundamental/dxy.py's module docstring for why this never gates entry.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

_cache_value: int = 50
_cache_label: str = "Neutral"
_cache_time: Optional[datetime] = None
CACHE_MINUTES = 120


def fetch_fear_greed() -> dict:
    global _cache_value, _cache_label, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_MINUTES * 60:
        return {"value": _cache_value, "label": _cache_label}

    try:
        r = requests.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        data = r.json()["data"][0]
        _cache_value = int(data["value"])
        _cache_label = data["value_classification"]
        _cache_time = now
    except Exception:
        pass
    return {"value": _cache_value, "label": _cache_label}


def get_fg_score_adj(direction: str) -> dict:
    fg = fetch_fear_greed()
    v = fg["value"]
    direction = str(direction or "").upper()

    if direction == "BUY":
        if v <= 25:
            adj = 2
        elif v <= 45:
            adj = 1
        elif v <= 55:
            adj = 0
        elif v <= 75:
            adj = -1
        else:
            adj = -2
    else:
        if v >= 76:
            adj = 2
        elif v >= 56:
            adj = 1
        elif v >= 46:
            adj = 0
        elif v >= 26:
            adj = -1
        else:
            adj = -2

    emoji = "😱" if v <= 25 else "😨" if v <= 45 else "😐" if v <= 55 else "😁" if v <= 75 else "🤑"
    return {
        "value": v,
        "label": fg["label"],
        "score_adj": adj,
        "emoji": emoji,
        "reason": f"{emoji} Fear&Greed: {v} ({fg['label']}) -> adj:{adj:+d}",
    }


def get_fear_greed_context() -> dict:
    return {
        "value": fetch_fear_greed()["value"],
        "label": fetch_fear_greed()["label"],
        "buy_adj": get_fg_score_adj("BUY")["score_adj"],
        "sell_adj": get_fg_score_adj("SELL")["score_adj"],
    }
