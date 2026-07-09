#!/usr/bin/env python3
"""
Market-close framework builder for Alpha Buffalo.

Runs once after market close / NewDay preparation:
- H4/Day swing H/L
- Lot0 memory
- Kivanc boundary + fibo position map
- completed harmonic PRZ context
- PDH/PDL/PDC + Asia H/L

Intraday scanner must read this map instead of rescanning harmonic every candle.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd
import requests

sys.path.insert(0, os.path.expanduser("~/alpha_buffalo_bot"))

try:
    from core.config.loader import load_env_safely
    load_env_safely()
except Exception:
    pass

try:
    from harmonic_detector import run_harmonic
except Exception as exc:
    run_harmonic = None
    HARMONIC_IMPORT_ERROR = str(exc)
else:
    HARMONIC_IMPORT_ERROR = ""

from core.models.newday_market_map import HarmonicContext, LiquidityZone, NewdayMarketMap

API_KEY = os.getenv("TWELVEDATA_API_KEY") or os.getenv("TWELVE_API_KEY") or os.getenv("TWELVE_DATA_API_KEY")
SYMBOL = os.getenv("ALPHA_SYMBOL", "XAU/USD")
PUBLIC_SYMBOL = os.getenv("ALPHA_PUBLIC_SYMBOL", SYMBOL.replace("/", ""))
OUTPUT_DIR = Path(os.getenv("ALPHA_MARKET_MAP_DIR", "data/market_maps"))


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def fetch_recent(interval: str, outputsize: int = 500, symbol: str = SYMBOL) -> pd.DataFrame:
    if not API_KEY:
        raise RuntimeError("TWELVEDATA_API_KEY missing")

    url = "https://api.twelvedata.com/time_series"
    params = {
        "symbol": symbol,
        "interval": interval,
        "outputsize": outputsize,
        "apikey": API_KEY,
        "format": "JSON",
    }
    response = requests.get(url, params=params, timeout=20)
    data = response.json()
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(f"TwelveData fetch failed interval={interval}: {data.get('message', data)}")

    df = pd.DataFrame(data["values"])
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df.dropna(subset=["datetime"]).set_index("datetime").sort_index()
    for col in ["open", "high", "low", "close"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"])


def filter_date(df: pd.DataFrame, target_date: str) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df.index.strftime("%Y-%m-%d") == target_date]


def kivanc_levels(high: float, low: float) -> Dict[str, float]:
    diff = max(high - low, 0.0)
    return {
        "boundary_high": high,
        "boundary_low": low,
        "fibo_0000": low,
        "fibo_0618": low + diff * 0.618,
        "fibo_0720": low + diff * 0.720,
        "fibo_0786": low + diff * 0.786,
        "fibo_0886": low + diff * 0.886,
        "fibo_1000": high,
    }


def build_lot0(last_close: float, boundary_high: float, boundary_low: float, pdh: float, pdl: float) -> Dict[str, Any]:
    if last_close > boundary_high:
        return {
            "side": "BUY",
            "price": pdh,
            "timeframe": "H4/DAY",
            "source": "close_above_kivanc_100_mark_pdh",
            "boundary_state": "BULLISH_BREAKOUT",
        }
    if last_close < boundary_low:
        return {
            "side": "SELL",
            "price": pdl,
            "timeframe": "H4/DAY",
            "source": "close_below_kivanc_000_mark_pdl",
            "boundary_state": "BEARISH_BREAKOUT",
        }
    return {
        "side": "NONE",
        "price": round((boundary_high + boundary_low) / 2, 3),
        "timeframe": "H4/DAY",
        "source": "inside_kivanc_boundary_no_new_lot0",
        "boundary_state": "IN_BOUNDARY",
    }


def zone_to_harmonic_context(zone: Any, timeframe: str) -> HarmonicContext:
    prz_low = _to_float(getattr(zone, "prz_low", 0.0))
    prz_high = _to_float(getattr(zone, "prz_high", 0.0))
    d_point = _to_float(getattr(zone, "d_point", 0.0))
    direction = str(getattr(zone, "direction", "NONE") or "NONE")

    if direction == "BUY":
        tp1 = d_point + abs(prz_high - prz_low) * 1.272
        tp2 = d_point + abs(prz_high - prz_low) * 1.618
        tp3 = d_point + abs(prz_high - prz_low) * 2.618
        invalidation = prz_low
    elif direction == "SELL":
        tp1 = d_point - abs(prz_high - prz_low) * 1.272
        tp2 = d_point - abs(prz_high - prz_low) * 1.618
        tp3 = d_point - abs(prz_high - prz_low) * 2.618
        invalidation = prz_high
    else:
        tp1 = tp2 = tp3 = invalidation = 0.0

    return HarmonicContext(
        found=bool(prz_low > 0 and prz_high > 0 and d_point > 0),
        pattern=str(getattr(zone, "pattern_name", "") or ""),
        direction=direction,
        timeframe=timeframe,
        source="market_close_harmonic_detector",
        state="COMPLETED_AT_D",
        d_point=round(d_point, 3),
        prz_low=round(prz_low, 3),
        prz_high=round(prz_high, 3),
        tp1=round(tp1, 3),
        tp2=round(tp2, 3),
        tp3=round(tp3, 3),
        invalidation=round(invalidation, 3),
        priority=int(getattr(zone, "priority", 5) or 5),
        reliability=str(getattr(zone, "reliability", "UNKNOWN") or "UNKNOWN"),
    )


def select_harmonic(df_4h: pd.DataFrame, df_1d: pd.DataFrame) -> HarmonicContext:
    if run_harmonic is None:
        return HarmonicContext(found=False, source=f"harmonic_import_error:{HARMONIC_IMPORT_ERROR}")

    candidates = []
    for timeframe, df in (("4H", df_4h), ("1D", df_1d)):
        try:
            for zone in run_harmonic(df) or []:
                ctx = zone_to_harmonic_context(zone, timeframe)
                if ctx.found:
                    candidates.append(ctx)
        except Exception as exc:
            print(f"⚠️ Harmonic scan failed timeframe={timeframe}: {exc}")

    if not candidates:
        return HarmonicContext(found=False, source="market_close_harmonic_detector")

    tf_rank = {"4H": 0, "1D": 1}
    candidates.sort(key=lambda c: (tf_rank.get(c.timeframe, 9), c.priority))
    return candidates[0]


def main() -> None:
    target_date = os.getenv("MARKET_MAP_DATE") or (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    print(f"📡 Building market-close map for {PUBLIC_SYMBOL} {target_date}...")

    df_15m = fetch_recent("15min", outputsize=5000)
    df_1h = fetch_recent("1h", outputsize=1000)
    df_4h = fetch_recent("4h", outputsize=500)
    df_1d = fetch_recent("1day", outputsize=120)

    day_1h = filter_date(df_1h, target_date)
    day_15m = filter_date(df_15m, target_date)
    if day_1h.empty:
        day_1h = df_1h.tail(24)
    if day_15m.empty:
        day_15m = df_15m.tail(96)

    pdh = float(day_1h["high"].max())
    pdl = float(day_1h["low"].min())
    pdc = float(day_1h["close"].iloc[-1])

    asian = day_15m[(day_15m.index.hour >= 1) & (day_15m.index.hour < 8)]
    asian_high = float(asian["high"].max()) if not asian.empty else pdh
    asian_low = float(asian["low"].min()) if not asian.empty else pdl

    h4_prior = df_4h.iloc[:-1].tail(50) if len(df_4h) > 51 else df_4h.tail(50)
    d_prior = df_1d.iloc[:-1].tail(30) if len(df_1d) > 31 else df_1d.tail(30)

    h4_high = float(h4_prior["high"].max())
    h4_low = float(h4_prior["low"].min())
    daily_high = float(d_prior["high"].max())
    daily_low = float(d_prior["low"].min())
    last_4h_close = float(df_4h["close"].iloc[-1])

    lot0 = build_lot0(last_4h_close, h4_high, h4_low, pdh, pdl)
    kivanc = kivanc_levels(h4_high, h4_low)

    day_range = max(pdh - pdl, 0.0)
    projected_high = pdh + day_range * 0.2
    projected_low = pdl - day_range * 0.2
    daily_bias = "BULLISH" if pdc > ((pdh + pdl) / 2) else "BEARISH"

    harmonic_context = select_harmonic(df_4h, df_1d)

    liquidity_zones = [
        LiquidityZone(price=round(pdh, 3), zone_type="BUY_SIDE_PDH", strength=0.9),
        LiquidityZone(price=round(pdl, 3), zone_type="SELL_SIDE_PDL", strength=0.9),
        LiquidityZone(price=round(asian_high, 3), zone_type="ASIA_HIGH", strength=0.7),
        LiquidityZone(price=round(asian_low, 3), zone_type="ASIA_LOW", strength=0.7),
    ]

    market_map = NewdayMarketMap(
        symbol=PUBLIC_SYMBOL,
        map_date=target_date,
        generated_at=datetime.now(timezone.utc).isoformat(),
        daily_bias=daily_bias,
        asian_high=round(asian_high, 3),
        asian_low=round(asian_low, 3),
        previous_day_high=round(pdh, 3),
        previous_day_low=round(pdl, 3),
        previous_day_close=round(pdc, 3),
        projected_high=round(projected_high, 3),
        projected_low=round(projected_low, 3),
        h4_swing_high=round(h4_high, 3),
        h4_swing_low=round(h4_low, 3),
        daily_swing_high=round(daily_high, 3),
        daily_swing_low=round(daily_low, 3),
        lot0={k: (round(v, 3) if isinstance(v, float) else v) for k, v in lot0.items()},
        kivanc={k: round(v, 3) for k, v in kivanc.items()},
        harmonic_context=harmonic_context,
        liquidity_zones=liquidity_zones,
        narrative="Market-close framework: Lot0/Kivanc/Harmonic is locked until BOS.",
    )

    output_dir = OUTPUT_DIR
    if not output_dir.is_absolute():
        output_dir = Path.cwd() / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{PUBLIC_SYMBOL}_{target_date}.json"
    output_path.write_text(market_map.model_dump_json(indent=2), encoding="utf-8")

    print(f"✅ Market-close map saved to {output_path}")
    print(market_map.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
