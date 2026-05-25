"""
plugin_dxy.py — Alpha Buffalo v5
DXY (US Dollar Index) Trend Filter

Logic:
DXY แข็ง (uptrend)  → Gold อ่อน → ลด BUY score, เพิ่ม SELL score
DXY อ่อน (downtrend)→ Gold แข็ง → เพิ่ BUY score, ลด SELL score
DXY sideways        → ไม่ปรับ

ใช้ EMA 20 + EMA 50 Cross บน 1H
"""

import requests
import pandas as pd
from datetime import datetime, timezone, timedelta
from typing import Optional

_dxy_cache: Optional[pd.DataFrame] = None
_cache_time: datetime = None
CACHE_MINUTES = 60


def fetch_dxy(api_key: str = "") -> Optional[pd.DataFrame]:
    """ดึง DXY data จาก TwelveData"""
    global _dxy_cache, _cache_time

    now = datetime.now(timezone.utc)
    if _cache_time and (now - _cache_time).total_seconds() < CACHE_MINUTES * 60:
        return _dxy_cache

    try:
        # ถ้าไม่มี TwelveData key ใช้ yfinance แทน
        if not api_key:
            return _fetch_dxy_yfinance()

        r = requests.get(
            "https://api.twelvedata.com/time_series",
            params={
                "symbol":     "DX-Y.NYB",
                "interval":   "1h",
                "outputsize": 60,
                "apikey":     api_key,
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

    except Exception as e:
        print(f"⚠️ DXY fetch error: {e}")
        return _fetch_dxy_yfinance()


def _fetch_dxy_yfinance() -> Optional[pd.DataFrame]:
    """Fallback: yfinance"""
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
    except Exception as e:
        print(f"⚠️ DXY yfinance error: {e}")
        return None


def get_dxy_trend(api_key: str = "") -> str:
    """คืน 'UP', 'DOWN', หรือ 'SIDEWAYS'"""
    df = fetch_dxy(api_key)
    if df is None or len(df) < 50:
        return "SIDEWAYS"

    close = df["close"] if "close" in df.columns else df["Close"]
    ema20 = close.ewm(span=20).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1]
    price = float(close.iloc[-1])

    if ema20 > ema50 and price > ema20:
        return "UP"
    elif ema20 < ema50 and price < ema20:
        return "DOWN"
    return "SIDEWAYS"


def get_dxy_score_adj(direction: str, api_key: str = "") -> dict:
    """
    คืน score adjustment ตาม DXY trend

    Gold vs DXY (inverse correlation):
    DXY UP   → Gold อ่อน → BUY gold ระวัง → SELL gold เพิ่ม
    DXY DOWN → Gold แข็ง → BUY gold เพิ่ม → SELL gold ระวัง
    """
    trend = get_dxy_trend(api_key)

    if direction == "BUY":
        if trend == "DOWN":   adj = +2   # DXY อ่อน = ดี สำหรับ Gold BUY
        elif trend == "UP":   adj = -2   # DXY แข็ง = ระวัง Gold BUY
        else:                 adj = 0
    else:  # SELL
        if trend == "UP":     adj = +2   # DXY แข็ง = ดี สำหรับ Gold SELL
        elif trend == "DOWN": adj = -2   # DXY อ่อน = ระวัง Gold SELL
        else:                 adj = 0

    emoji = "📈" if trend=="UP" else "📉" if trend=="DOWN" else "➡️"

    return {
        "trend":     trend,
        "score_adj": adj,
        "emoji":     emoji,
        "reason":    f"{emoji} DXY: {trend} → Gold {direction} adj:{adj:+d}",
    }
