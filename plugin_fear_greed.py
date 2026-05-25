"""
plugin_fear_greed.py — Alpha Buffalo v5
Fear & Greed Index
ฟรี 100% alternative.me API

Logic:
Extreme Fear (0-25)  → BUY Gold bias  +2
Fear (26-45)         → BUY bias       +1
Neutral (46-55)      → ไม่ปรับ         0
Greed (56-75)        → SELL bias      +1
Extreme Greed (76-100)→ SELL bias     +2
"""

import requests
from datetime import datetime, timezone, timedelta

_cache_value: int = 50
_cache_label: str = "Neutral"
_cache_time: datetime = None
CACHE_MINUTES = 120  # refresh ทุก 2 ชั่วโมง


def fetch_fear_greed() -> dict:
    """ดึง Fear & Greed Index"""
    global _cache_value, _cache_label, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_MINUTES * 60:
        return {"value": _cache_value, "label": _cache_label}

    try:
        r = requests.get(
            "https://api.alternative.me/fng/?limit=1",
            timeout=10,
        )
        data = r.json()["data"][0]
        _cache_value = int(data["value"])
        _cache_label = data["value_classification"]
        _cache_time  = now
        return {"value": _cache_value, "label": _cache_label}

    except Exception as e:
        print(f"⚠️ Fear&Greed fetch error: {e}")
        return {"value": _cache_value, "label": _cache_label}


def get_fg_score_adj(direction: str) -> dict:
    """
    คืน score adjustment และ bias

    BUY Gold:
    - Extreme Fear → Smart Money สะสม → BUY bias +2
    - Greed → ระวัง → ลด score -1

    SELL Gold:
    - Extreme Greed → Top หรือ Correction → SELL bias +2
    - Fear → ระวัง → ลด score -1
    """
    fg = fetch_fear_greed()
    v  = fg["value"]

    if direction == "BUY":
        if v <= 25:   adj = +2   # Extreme Fear = Gold ถูก สะสม
        elif v <= 45: adj = +1   # Fear = slight buy bias
        elif v <= 55: adj = 0    # Neutral
        elif v <= 75: adj = -1   # Greed = ระวัง
        else:         adj = -2   # Extreme Greed = ระวังมาก
    else:  # SELL
        if v >= 76:   adj = +2   # Extreme Greed = Sell opportunity
        elif v >= 56: adj = +1   # Greed = slight sell bias
        elif v >= 46: adj = 0    # Neutral
        elif v >= 26: adj = -1   # Fear = ระวัง
        else:         adj = -2   # Extreme Fear = ระวังมาก

    emoji = "😱" if v <= 25 else "😨" if v <= 45 else "😐" if v <= 55 else "😁" if v <= 75 else "🤑"

    return {
        "value":     v,
        "label":     fg["label"],
        "score_adj": adj,
        "emoji":     emoji,
        "reason":    f"{emoji} Fear&Greed: {v} ({fg['label']}) → adj:{adj:+d}",
    }
