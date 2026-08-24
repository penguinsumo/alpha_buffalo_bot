"""
fundamental/dxy.py — ported from clean v5's plugin_dxy.py (main branch).

DXY (US Dollar Index) trend context. XAU/USD and DXY are inversely
correlated, so DXY trend is used as a *context* adjustment only -- per
PROJECT_CONTRACT.md / ALPHA_FUSION_CONTRACT.md this must never become a V4
entry gate (that list explicitly bans EMA-agreement gates as an entry
blocker). Kept for logging/diagnostics and as an optional soft
risk_adjustment input, same role clean v5 gave it in score_manager.py's
Bucket E.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import requests

_dxy_cache: Optional[pd.DataFrame] = None
_cache_time: Optional[datetime] = None
# H1 tier: DXY is an H1-candle context input, so its cache should refresh no
# slower than roughly once per H1 candle -- matches the H1 fetch cadence in
# alpha_buffalo_signal.py's TF_FETCH_TTL_SECONDS. Configurable so a deploy
# can retune it without a code change; default keeps prior behavior.
CACHE_MINUTES = int(os.getenv("FUNDAMENTAL_DXY_CACHE_MINUTES", "60"))


def fetch_dxy(api_key: str = "") -> Optional[pd.DataFrame]:
    """Fetch DXY 1h candles from TwelveData, falling back to yfinance."""
    global _dxy_cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_MINUTES * 60:
        return _dxy_cache

    try:
        if not api_key:
            return _fetch_dxy_yfinance()

        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol": "DX-Y.NYB",
                "interval": "1h",
                "outputsize": 60,
                "apikey": api_key,
            },
            timeout=10,
        )
        data = r.json()
        if "values" not in data:
            return _fetch_dxy_yfinance()

        df = pd.DataFrame(data["values"])
        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)
        df["close"] = df["close"].astype(float)

        _dxy_cache = df
        _cache_time = now
        return df

    except Exception:
        return _fetch_dxy_yfinance()


def _fetch_dxy_yfinance() -> Optional[pd.DataFrame]:
    global _dxy_cache, _cache_time
    try:
        import yfinance as yf

        df = yf.Ticker("DX-Y.NYB").history(period="5d", interval="1h")
        df.columns = [c.lower() for c in df.columns]
        df = df.reset_index()
        df = df.rename(columns={"Datetime": "datetime", "index": "datetime"})
        _dxy_cache = df
        _cache_time = datetime.now(timezone.utc)
        return df
    except Exception:
        return None


def get_dxy_trend(api_key: str = "") -> str:
    """Return 'UP', 'DOWN', or 'SIDEWAYS'. Never raises."""
    try:
        df = fetch_dxy(api_key)
        if df is None or len(df) < 50:
            return "SIDEWAYS"

        close = df["close"] if "close" in df.columns else df["Close"]
        ema20 = close.ewm(span=20).mean().iloc[-1]
        ema50 = close.ewm(span=50).mean().iloc[-1]
        price = float(close.iloc[-1])

        if ema20 > ema50 and price > ema20:
            return "UP"
        if ema20 < ema50 and price < ema20:
            return "DOWN"
        return "SIDEWAYS"
    except Exception:
        return "SIDEWAYS"


def get_dxy_score_adj(direction: str, api_key: str = "") -> dict:
    """Context-only score adjustment. direction: 'BUY' or 'SELL'."""
    trend = get_dxy_trend(api_key)
    direction = str(direction or "").upper()

    if direction == "BUY":
        adj = {"DOWN": 2, "UP": -2}.get(trend, 0)
    else:
        adj = {"UP": 2, "DOWN": -2}.get(trend, 0)

    emoji = "📈" if trend == "UP" else "📉" if trend == "DOWN" else "➡️"
    return {
        "trend": trend,
        "score_adj": adj,
        "emoji": emoji,
        "reason": f"{emoji} DXY: {trend} -> Gold {direction} adj:{adj:+d}",
    }


def get_dxy_context(api_key: str = "") -> dict:
    """Direction-agnostic snapshot, for /context/fundamental diagnostics."""
    return {
        "trend": get_dxy_trend(api_key),
        "buy_adj": get_dxy_score_adj("BUY", api_key)["score_adj"],
        "sell_adj": get_dxy_score_adj("SELL", api_key)["score_adj"],
    }
