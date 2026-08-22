"""
fundamental/cot.py — ported from clean v5's plugin_cot.py.
CFTC Commitment of Traders (Gold, COMEX), free public data, weekly update.
Context-only adjustment, see fundamental/dxy.py's module docstring.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import requests

_cot_cache: dict = {}
_cache_time: Optional[datetime] = None
CACHE_HOURS = 24


def fetch_cot_gold() -> dict:
    global _cot_cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_HOURS * 3600:
        return _cot_cache

    try:
        url = "https://publicreporting.cftc.gov/api/odata/v1/FinancialFuturesOnly_Contracts_Disaggregated_AllYears"
        params = {
            "$filter": "Market_and_Exchange_Names eq 'GOLD - COMMODITY EXCHANGE INC.'",
            "$orderby": "Report_Date_as_YYYY_MM_DD desc",
            "$top": "10",
            "$format": "json",
        }
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return _cot_cache

        data = r.json().get("value", [])
        if not data:
            return _cot_cache

        latest = data[0]
        net_long = int(latest.get("NonComm_Positions_Long_All", 0)) - int(
            latest.get("NonComm_Positions_Short_All", 0)
        )

        net_longs = []
        for row in data[:52]:
            nl = int(row.get("NonComm_Positions_Long_All", 0)) - int(
                row.get("NonComm_Positions_Short_All", 0)
            )
            net_longs.append(nl)

        max_nl = max(net_longs) if net_longs else 1
        min_nl = min(net_longs) if net_longs else -1
        rng = max_nl - min_nl if max_nl != min_nl else 1
        pct = (net_long - min_nl) / rng * 100

        _cot_cache = {
            "net_long": net_long,
            "pct_rank": round(pct, 1),
            "report_date": latest.get("Report_Date_as_YYYY_MM_DD", ""),
            "bias": "BUY" if net_long > 0 else "SELL",
        }
        _cache_time = now
        return _cot_cache

    except Exception:
        return _cot_cache or {
            "net_long": 0,
            "pct_rank": 50,
            "report_date": "",
            "bias": "NEUTRAL",
        }


def get_cot_score_adj(direction: str) -> dict:
    cot = fetch_cot_gold()
    pct = cot.get("pct_rank", 50)
    bias = cot.get("bias", "NEUTRAL")
    direction = str(direction or "").upper()

    if direction == "BUY":
        if pct >= 80:
            adj = 1
        elif pct >= 60:
            adj = 2
        elif pct >= 40:
            adj = 0
        elif pct >= 20:
            adj = -1
        else:
            adj = -2
    else:
        if pct <= 20:
            adj = 1
        elif pct <= 40:
            adj = 2
        elif pct <= 60:
            adj = 0
        elif pct <= 80:
            adj = -1
        else:
            adj = -2

    emoji = "🐂" if pct >= 60 else "🐻" if pct <= 40 else "⚖️"
    return {
        "pct_rank": pct,
        "bias": bias,
        "score_adj": adj,
        "emoji": emoji,
        "report_date": cot.get("report_date", ""),
        "reason": f"{emoji} COT Gold: {pct}% rank ({bias}) -> adj:{adj:+d}",
    }


def get_cot_context() -> dict:
    cot = fetch_cot_gold()
    return {
        "pct_rank": cot.get("pct_rank", 50),
        "bias": cot.get("bias", "NEUTRAL"),
        "report_date": cot.get("report_date", ""),
        "buy_adj": get_cot_score_adj("BUY")["score_adj"],
        "sell_adj": get_cot_score_adj("SELL")["score_adj"],
    }
