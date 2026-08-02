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

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from core.config.loader import load_env_safely
    load_env_safely()
except Exception:
    pass

try:
    from harmonic_detector import run_harmonic, scan_forming_harmonic
except Exception as exc:
    run_harmonic = None
    scan_forming_harmonic = None
    HARMONIC_IMPORT_ERROR = str(exc)
else:
    HARMONIC_IMPORT_ERROR = ""

from core.models.newday_market_map import HarmonicContext, LiquidityZone, NewdayMarketMap

API_KEY = os.getenv("TWELVEDATA_API_KEY", "")
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
        approach_direction="SELL" if direction == "BUY" else "BUY" if direction == "SELL" else "NONE",
        timeframe=timeframe,
        source="market_close_harmonic_detector",
        state="COMPLETED_AT_D",
        x_point=round(_to_float(getattr(zone, "x_point", 0.0)), 3),
        a_point=round(_to_float(getattr(zone, "a_point", 0.0)), 3),
        b_point=round(_to_float(getattr(zone, "b_point", 0.0)), 3),
        c_point=round(_to_float(getattr(zone, "c_point", 0.0)), 3),
        d_point=round(d_point, 3),
        x_idx=int(getattr(zone, "x_idx", -1)),
        a_idx=int(getattr(zone, "a_idx", -1)),
        b_idx=int(getattr(zone, "b_idx", -1)),
        c_idx=int(getattr(zone, "c_idx", -1)),
        d_idx=int(getattr(zone, "d_idx", -1)),
        ratios={
            str(key): round(_to_float(value), 6)
            for key, value in dict(getattr(zone, "ratios", {}) or {}).items()
        },
        prz_low=round(prz_low, 3),
        prz_high=round(prz_high, 3),
        tp1=round(tp1, 3),
        tp2=round(tp2, 3),
        tp3=round(tp3, 3),
        invalidation=round(invalidation, 3),
        priority=int(getattr(zone, "priority", 5) or 5),
        reliability=str(getattr(zone, "reliability", "UNKNOWN") or "UNKNOWN"),
        projection_mode="COMPLETED_XABCD",
        execution_authority=False,
        selected_pattern=str(getattr(zone, "pattern_name", "") or ""),
        ratio_model="COMPLETED_XABCD_RATIOS",
        confirmation_required=[
            "PRZ_REVERSAL_CANDLE",
            "DIRECTIONAL_CANDLE_BREAKOUT",
            "HTF_STRUCTURE_ALIGNMENT",
        ],
        stop_reference="CONFIRMATION_CANDLE_EXTREME",
        statistics_status="INSUFFICIENT_SAMPLE",
        statistics_sample_size=0,
        statistics_source="NONE",
    )


def projection_to_harmonic_context(projection: Dict[str, Any], timeframe: str) -> HarmonicContext:
    """Serialize a confirmed-XABC forecast before D exists."""
    direction = str(projection.get("direction", "NONE") or "NONE").upper()
    prz_low = _to_float(projection.get("prz_low"))
    prz_high = _to_float(projection.get("prz_high"))
    d_point = _to_float(projection.get("d_point"))
    width = max(prz_high - prz_low, 0.0)
    if direction == "BUY":
        tp1, tp2, tp3 = d_point + width * 1.272, d_point + width * 1.618, d_point + width * 2.618
        invalidation = prz_low - max(width * 0.25, d_point * 0.0015)
    elif direction == "SELL":
        tp1, tp2, tp3 = d_point - width * 1.272, d_point - width * 1.618, d_point - width * 2.618
        invalidation = prz_high + max(width * 0.25, d_point * 0.0015)
    else:
        tp1 = tp2 = tp3 = invalidation = 0.0

    explicit_tp1 = _to_float(projection.get("tp1"))
    explicit_tp2 = _to_float(projection.get("tp2"))
    explicit_tp3 = _to_float(projection.get("tp3"))
    if explicit_tp1 > 0:
        tp1 = explicit_tp1
    if explicit_tp2 > 0:
        tp2 = explicit_tp2
    if explicit_tp3 > 0:
        tp3 = explicit_tp3

    candidates = []
    for candidate in projection.get("candidates") or []:
        candidates.append(
            {
                "pattern": str(candidate.get("pattern", "")),
                "state": str(candidate.get("state", "NONE")),
                "prz_low": round(_to_float(candidate.get("prz_low")), 3),
                "prz_high": round(_to_float(candidate.get("prz_high")), 3),
                "current_xad": round(_to_float(candidate.get("current_xad")), 6),
                "current_bcd": round(_to_float(candidate.get("current_bcd")), 6),
                "next_xad": round(_to_float(candidate.get("next_xad")), 6),
                "fallback": bool(candidate.get("fallback", False)),
                "execution_authority": False,
                "entry_authority": False,
                "target_authority_after_bos": True,
                "ratio_model": str(candidate.get("ratio_model", "NONE")),
                "confirmation_required": list(candidate.get("confirmation_required") or []),
                "stop_reference": str(candidate.get("stop_reference", "NONE")),
                "morph_state": str(candidate.get("morph_state", "BASE_PROJECTION")),
                "morph_from": list(candidate.get("morph_from") or []),
                "morph_to": str(candidate.get("morph_to", "")),
                "morph_reason": str(candidate.get("morph_reason", "NONE")),
                "statistics_status": str(candidate.get("statistics_status", "INSUFFICIENT_SAMPLE")),
                "statistics_sample_size": int(candidate.get("statistics_sample_size", 0) or 0),
                "statistics_source": str(candidate.get("statistics_source", "NONE")),
            }
        )

    return HarmonicContext(
        found=bool(direction in {"BUY", "SELL"} and prz_low > 0 and prz_high > 0),
        pattern=str(projection.get("pattern", "") or ""),
        direction=direction,
        approach_direction=str(projection.get("approach_direction", "NONE") or "NONE"),
        timeframe=timeframe,
        source=str(projection.get("source", "market_close_harmonic_projection")),
        state=str(projection.get("state", "FORMING") or "FORMING"),
        x_point=round(_to_float(projection.get("x")), 3),
        a_point=round(_to_float(projection.get("a")), 3),
        b_point=round(_to_float(projection.get("b")), 3),
        c_point=round(_to_float(projection.get("c")), 3),
        d_point=round(d_point, 3),
        x_idx=int(projection.get("x_idx", -1)),
        a_idx=int(projection.get("a_idx", -1)),
        b_idx=int(projection.get("b_idx", -1)),
        c_idx=int(projection.get("c_idx", -1)),
        d_idx=-1,
        ratios={
            str(key): round(_to_float(value), 6)
            for key, value in dict(projection.get("ratios") or {}).items()
        },
        prz_low=round(prz_low, 3),
        prz_high=round(prz_high, 3),
        tp1=round(tp1, 3),
        tp2=round(tp2, 3),
        tp3=round(tp3, 3),
        invalidation=round(invalidation, 3),
        priority=int(projection.get("priority", 5) or 5),
        reliability="PROJECTED",
        projection_mode=str(projection.get("projection_mode", "FORMING_XABC_TO_D")),
        execution_authority=False,
        selected_pattern=str(projection.get("selected_pattern", projection.get("pattern", "")) or ""),
        candidate_patterns=candidates,
        current_xad=round(_to_float(projection.get("current_xad")), 6),
        current_bcd=round(_to_float(projection.get("current_bcd")), 6),
        next_xad=round(_to_float(projection.get("next_xad")), 6),
        ratio_model=str(projection.get("ratio_model", "NONE")),
        confirmation_required=list(projection.get("confirmation_required") or []),
        stop_reference=str(projection.get("stop_reference", "NONE")),
        morph_state=str(projection.get("morph_state", "BASE_PROJECTION")),
        morph_from=list(projection.get("morph_from") or []),
        morph_to=str(projection.get("morph_to", "")),
        morph_reason=str(projection.get("morph_reason", "NONE")),
        statistics_status=str(projection.get("statistics_status", "INSUFFICIENT_SAMPLE")),
        statistics_sample_size=int(projection.get("statistics_sample_size", 0) or 0),
        statistics_source=str(projection.get("statistics_source", "NONE")),
    )


def select_harmonic(
    df_4h: pd.DataFrame,
    df_1d: pd.DataFrame,
    df_1h: Optional[pd.DataFrame] = None,
) -> HarmonicContext:
    if run_harmonic is None:
        return HarmonicContext(found=False, source=f"harmonic_import_error:{HARMONIC_IMPORT_ERROR}")

    candidates = []
    if scan_forming_harmonic is not None:
        projection_frames = [("1H", df_1h), ("4H", df_4h), ("1D", df_1d)]
        for timeframe, df in projection_frames:
            if df is None or df.empty:
                continue
            try:
                projection = scan_forming_harmonic(df)
                if projection.get("found"):
                    candidates.append(projection_to_harmonic_context(projection, timeframe))
            except Exception as exc:
                print(f"⚠️ Forming harmonic projection failed timeframe={timeframe}: {exc}")

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

    state_rank = {"ACTIVE": 0, "ARMED": 1, "FORMING": 2, "COMPLETED_AT_D": 3, "PASSED": 9}
    tf_rank = {"1H": 0, "4H": 1, "1D": 2}
    candidates.sort(
        key=lambda c: (
            state_rank.get(str(c.state).upper(), 5),
            tf_rank.get(c.timeframe, 9),
            c.priority,
        )
    )
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

    harmonic_context = select_harmonic(df_4h, df_1d, df_1h)

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
