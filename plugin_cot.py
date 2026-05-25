"""
plugin_cot.py — Alpha Buffalo v5
CFTC Commitment of Traders (COT) Report
ฟรี 100% — อัพเดทราย Fri

Logic:
Non-Commercial Net Long > 0 → Institutional BUY bias
Non-Commercial Net Long < 0 → Institutional SELL bias
เปรียบเทียบกับ 52-week range → Extreme positioning
"""

import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional

_cot_cache: dict = {}
_cache_time: datetime = None
CACHE_HOURS = 24  # COT อัพเดทรายสัปดาห์


def fetch_cot_gold() -> dict:
    """
    ดึง COT data สำหรับ Gold (COMEX)
    จาก CFTC public data
    """
    global _cot_cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_HOURS * 3600:
        return _cot_cache

    try:
        # CFTC Disaggregated Futures-Only Report
        # Gold = "GOLD - COMMODITY EXCHANGE INC."
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
        net_long = (
            int(latest.get("NonComm_Positions_Long_All", 0)) -
            int(latest.get("NonComm_Positions_Short_All", 0))
        )

        # หา 52-week range
        net_longs = []
        for row in data[:52]:
            nl = (int(row.get("NonComm_Positions_Long_All", 0)) -
                  int(row.get("NonComm_Positions_Short_All", 0)))
            net_longs.append(nl)

        max_nl = max(net_longs) if net_longs else 1
        min_nl = min(net_longs) if net_longs else -1
        rng    = max_nl - min_nl if max_nl != min_nl else 1
        pct    = (net_long - min_nl) / rng * 100  # 0-100%

        _cot_cache = {
            "net_long":    net_long,
            "pct_rank":    round(pct, 1),
            "report_date": latest.get("Report_Date_as_YYYY_MM_DD", ""),
            "bias":        "BUY" if net_long > 0 else "SELL",
        }
        _cache_time = now
        return _cot_cache

    except Exception as e:
        print(f"⚠️ COT fetch error: {e}")
        return _cot_cache or {
            "net_long": 0, "pct_rank": 50,
            "report_date": "", "bias": "NEUTRAL"
        }


def get_cot_score_adj(direction: str) -> dict:
    """
    คืน score adjustment ตาม COT

    pct_rank > 80% = Extreme Long → BUY bias แต่ระวัง reversal
    pct_rank 60-80% = Strong Long → BUY bias
    pct_rank 40-60% = Neutral
    pct_rank 20-40% = Strong Short → SELL bias
    pct_rank < 20% = Extreme Short → SELL bias แต่ระวัง reversal
    """
    cot = fetch_cot_gold()
    pct = cot.get("pct_rank", 50)
    bias= cot.get("bias", "NEUTRAL")

    if direction == "BUY":
        if pct >= 80:   adj = +1   # Institutional Long แต่ระวัง crowded
        elif pct >= 60: adj = +2   # Strong Long → align
        elif pct >= 40: adj = 0    # Neutral
        elif pct >= 20: adj = -1   # Short bias ระวัง
        else:           adj = -2   # Extreme Short → สวนทาง Institutional
    else:  # SELL
        if pct <= 20:   adj = +1   # Institutional Short
        elif pct <= 40: adj = +2   # Strong Short → align
        elif pct <= 60: adj = 0    # Neutral
        elif pct <= 80: adj = -1   # Long bias ระวัง
        else:           adj = -2   # Extreme Long → สวนทาง

    emoji = "🐂" if pct >= 60 else "🐻" if pct <= 40 else "⚖️"

    return {
        "pct_rank":    pct,
        "bias":        bias,
        "score_adj":   adj,
        "emoji":       emoji,
        "report_date": cot.get("report_date", ""),
        "reason": f"{emoji} COT Gold: {pct}% rank ({bias}) → adj:{adj:+d}",
    }
