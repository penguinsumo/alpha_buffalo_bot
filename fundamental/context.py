"""
fundamental/context.py — aggregates DXY, Fear & Greed, COT, and news
calendar into one diagnostic snapshot for the API.

This is v12-core's missing fundamental layer (identified when comparing
against clean v5's context_engine.py). It is deliberately diagnostic-only:
nothing in engine_v4 reads this, so it cannot become a trend/EMA-style
entry blocker of the kind PROJECT_CONTRACT.md's Red Lines section forbids.
Every individual plugin already fails closed to a neutral value on network
error (see each module) -- this aggregator adds one more layer of defense
so a single misbehaving source can never take down /context/fundamental or
any endpoint that includes it.
"""
from __future__ import annotations

import os

from fundamental.cot import get_cot_context, get_cot_score_adj
from fundamental.dxy import get_dxy_context, get_dxy_score_adj
from fundamental.fear_greed import get_fear_greed_context, get_fg_score_adj
from fundamental.news import check_news_filter

TWELVEDATA_API_KEY = os.getenv("TWELVEDATA_API_KEY", "")


def _safe(fn, default):
    try:
        return fn()
    except Exception as exc:
        return {**default, "error": f"{type(exc).__name__}: {exc}"}


def fundamental_diagnostic() -> dict:
    """Point-in-time fundamental snapshot, direction-agnostic."""
    dxy = _safe(lambda: get_dxy_context(TWELVEDATA_API_KEY), {"trend": "SIDEWAYS", "buy_adj": 0, "sell_adj": 0})
    fg = _safe(get_fear_greed_context, {"value": 50, "label": "Neutral", "buy_adj": 0, "sell_adj": 0})
    cot = _safe(get_cot_context, {"pct_rank": 50, "bias": "NEUTRAL", "buy_adj": 0, "sell_adj": 0})
    news = _safe(check_news_filter, {"safe": True, "score_adj": 0, "reason": "unavailable", "next_news": "", "impact": ""})

    return {
        "dxy": dxy,
        "fear_greed": fg,
        "cot": cot,
        "news": news,
    }


def fundamental_bias_for_direction(direction: str) -> dict:
    """Non-blocking combined adjustment for a candidate direction.

    Returns a `total_adj` int for optional use as a soft risk_adjustment
    input (same non-gate role as runtime_layers/newday.py's
    newday_bias_for_direction). Never returns an `allowed`/`blocked` field
    -- there is nothing here a caller could wire up as an entry veto even
    by mistake.
    """
    direction = str(direction or "").upper()
    diag = fundamental_diagnostic()

    dxy_adj = int(_safe(lambda: get_dxy_score_adj(direction, TWELVEDATA_API_KEY), {"score_adj": 0})["score_adj"])
    fg_adj = int(_safe(lambda: get_fg_score_adj(direction), {"score_adj": 0})["score_adj"])
    cot_adj = int(_safe(lambda: get_cot_score_adj(direction), {"score_adj": 0})["score_adj"])
    news_adj = int(diag["news"].get("score_adj", 0))

    return {
        "direction": direction,
        "dxy_adj": dxy_adj,
        "fear_greed_adj": fg_adj,
        "cot_adj": cot_adj,
        "news_adj": news_adj,
        "total_adj": dxy_adj + fg_adj + cot_adj + news_adj,
        "news_safe": bool(diag["news"].get("safe", True)),
        "detail": diag,
    }
