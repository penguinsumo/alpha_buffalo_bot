#!/usr/bin/env python3
"""Data-honest proxy backtest for Pine v2.4.2 GC futures-RVOL PRZ routing.

The production Pine reads XAUUSD and GC futures at the same timestamp.  This
runner therefore accepts separate spot and GC M15 files and reports their
actual overlap.  It also supports a clearly labelled GC-as-both-instruments
proxy so entry-policy variants can be compared over a longer local window.

Variants:
* HA_ONLY_PRZ: legacy-like PRZ location + BB + PA/HA, then HA15 #2.
* GC_RVOL_AND_PA_SAME_BAR: the rejected double-gate baseline.
* GC_RVOL_PA_ANY_ORDER_HA: GC contract RVOL and PA latch in either order inside the PRZ;
  only the second confirmed HA15 candle can trigger an entry.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.backtest_core_ha15_strong import POINT_VALUE, stats  # noqa: E402
from scripts.backtest_h1_m15_ha15_strong import (  # noqa: E402
    align,
    atr,
    choose_cluster,
    confirmed_swing_range,
    heikin_ashi,
    nearest_target,
    ohlc_resample,
    rsi,
)
from session_clock import SessionClock  # noqa: E402


VARIANTS = (
    "HA_ONLY_PRZ",
    "GC_RVOL_AND_PA_SAME_BAR",
    "GC_RVOL_PA_ANY_ORDER_HA",
)


def finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def load_ohlcv(path: Path) -> pd.DataFrame:
    raw = pd.read_csv(path)
    datetime_column = "datetime" if "datetime" in raw else "Datetime" if "Datetime" in raw else None
    if datetime_column is None:
        raise ValueError(f"{path}: missing datetime/Datetime column")
    raw["datetime"] = pd.to_datetime(raw[datetime_column], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in raw:
            raw[column] = 0.0
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    return (
        raw.dropna(subset=["datetime", "open", "high", "low", "close"])
        .drop_duplicates("datetime", keep="last")
        .set_index("datetime")
        .sort_index()[["open", "high", "low", "close", "volume"]]
    )


def prepare_spot(raw: pd.DataFrame) -> pd.DataFrame:
    clock = SessionClock()
    mask = [clock.get(timestamp).session != "CLOSED" for timestamp in raw.index]
    frame = raw.loc[mask].copy()
    frame["atr14"] = atr(frame, 14)
    basis = frame["close"].rolling(20, min_periods=20).mean()
    deviation = frame["close"].rolling(20, min_periods=20).std(ddof=0) * 2.0
    frame["bb_upper"] = basis + deviation
    frame["bb_lower"] = basis - deviation
    frame["vol_avg20"] = frame["volume"].rolling(20, min_periods=20).mean()

    ha = heikin_ashi(frame)
    frame = frame.join(ha)
    ha_bull = frame["ha_close"] > frame["ha_open"]
    ha_bear = frame["ha_close"] < frame["ha_open"]
    prior_body_low = pd.concat(
        [frame["ha_open"].shift(1), frame["ha_close"].shift(1)], axis=1
    ).min(axis=1)
    prior_body_high = pd.concat(
        [frame["ha_open"].shift(1), frame["ha_close"].shift(1)], axis=1
    ).max(axis=1)
    frame["ha_two_bull_higher"] = ha_bull & ha_bull.shift(1).eq(True) & (frame["ha_close"] > prior_body_high)
    frame["ha_two_bear_lower"] = ha_bear & ha_bear.shift(1).eq(True) & (frame["ha_close"] < prior_body_low)
    frame["ha_bull_reversal"] = ha_bear.shift(1).eq(True) & ha_bull
    frame["ha_bear_reversal"] = ha_bull.shift(1).eq(True) & ha_bear

    h1 = ohlc_resample(frame, "1h")
    h4 = ohlc_resample(frame, "4h")
    d1 = ohlc_resample(frame, "1D")
    h1_ha = heikin_ashi(h1)
    h1["ema20"] = h1["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    h1["ema50"] = h1["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    h1["rsi14"] = rsi(h1["close"], 14)
    previous_high = h1["high"].shift(1).rolling(5, min_periods=5).max()
    previous_low = h1["low"].shift(1).rolling(5, min_periods=5).min()
    h1["bull_break"] = h1["close"] > previous_high
    h1["bear_break"] = h1["close"] < previous_low

    h1_range = (h1["high"] - h1["low"]).clip(lower=0.01)
    h1_body = (h1["close"] - h1["open"]).abs().clip(lower=0.01)
    h1_lower_wick = np.minimum(h1["open"], h1["close"]) - h1["low"]
    h1_upper_wick = h1["high"] - np.maximum(h1["open"], h1["close"])
    h1["bull_pin"] = (h1_lower_wick >= h1_body * 2.0) & (h1_upper_wick <= h1_body)
    h1["bear_pin"] = (h1_upper_wick >= h1_body * 2.0) & (h1_lower_wick <= h1_body)
    del h1_range  # explicit: ratios above use wick/body, range retained only conceptually

    confirmed_h1 = pd.DataFrame(index=h1.index)
    confirmed_h1["context_buy"] = (h1["ema20"] > h1["ema50"]) & (h1["rsi14"] > 50.0)
    confirmed_h1["context_sell"] = (h1["ema20"] < h1["ema50"]) & (h1["rsi14"] < 50.0)
    confirmed_h1["regular_bull"] = h1["close"] > h1["open"]
    confirmed_h1["ha_bear"] = h1_ha["ha_close"] < h1_ha["ha_open"]
    confirmed_h1["bull_pin"] = h1["bull_pin"]
    confirmed_h1["bear_pin"] = h1["bear_pin"]
    confirmed_h1["bull_break"] = h1["bull_break"]
    confirmed_h1["bear_break"] = h1["bear_break"]
    confirmed_h1 = confirmed_h1.shift(1)
    for column in confirmed_h1:
        frame[f"h1_{column}"] = align(confirmed_h1[column].astype(float), frame.index).fillna(0.0)

    for prefix, source, lookback in (("d", d1, 20), ("h4", h4, 30), ("h1", h1, 60)):
        ranges = confirmed_swing_range(source, lookback=lookback, pivot_bars=3)
        frame[f"{prefix}_range_high"] = align(ranges["range_high"], frame.index)
        frame[f"{prefix}_range_low"] = align(ranges["range_low"], frame.index)
    return frame


def attach_gc(frame: pd.DataFrame, gc_raw: pd.DataFrame) -> pd.DataFrame:
    gc = gc_raw.copy()
    gc["vol_avg20"] = gc["volume"].rolling(20, min_periods=20).mean()
    gc["atr14"] = atr(gc, 14)
    gc_h1 = ohlc_resample(gc, "1h")
    gc_ranges = confirmed_swing_range(gc_h1, lookback=60, pivot_bars=3)

    joined = frame.copy()
    for column in ("open", "high", "low", "close", "volume", "vol_avg20", "atr14"):
        joined[f"gc_{column}"] = gc[column].reindex(joined.index)
    joined["gc_resistance_h1"] = align(gc_ranges["range_high"], joined.index)
    joined["gc_support_h1"] = align(gc_ranges["range_low"], joined.index)
    return joined


def close_trade(position: dict, timestamp, exit_price: float, reason: str, equity: float, index: int) -> tuple[dict, float]:
    direction = position["direction"]
    points = exit_price - position["entry"] if direction == "BUY" else position["entry"] - exit_price
    risk = abs(position["entry"] - position["stop"])
    lots = (equity * 0.01) / (risk * POINT_VALUE)
    gross = points * POINT_VALUE * lots
    costs = 0.50 * POINT_VALUE * lots
    pnl = gross - costs
    next_equity = equity + pnl
    return {
        "variant": position["variant"],
        "portfolio_scope": position["portfolio_scope"],
        "direction": direction,
        "entry_mode": position["mode"],
        "signal_time": position["signal_time"].isoformat(),
        "entry_time": position["entry_time"].isoformat(),
        "exit_time": timestamp.isoformat(),
        "entry": position["entry"],
        "initial_sl": position["stop"],
        "be_trigger": position["be_trigger"],
        "tp2": position["target"],
        "exit": exit_price,
        "exit_reason": reason,
        "bars_held": index - position["entry_index"],
        "risk_points": risk,
        "gross_points": points,
        "net_points": points - 0.50,
        "gross_r": points / risk,
        "net_r": (points - 0.50) / risk,
        "lots": lots,
        "gross_pnl": gross,
        "costs": costs,
        "net_pnl": pnl,
        "equity_after": next_equity,
    }, next_equity


def run_variant(
    frame: pd.DataFrame,
    variant: str,
    allowed_directions: tuple[str, ...] = ("BUY", "SELL"),
) -> tuple[list[dict], dict, list[float]]:
    equity = 10_000.0
    curve = [equity]
    trades: list[dict] = []
    arms: dict[str, dict | None] = {"BUY": None, "SELL": None}
    gc_rvol_walls: dict[str, dict | None] = {"BUY": None, "SELL": None}
    pa_zones: dict[str, dict | None] = {"BUY": None, "SELL": None}
    pending: dict | None = None
    position: dict | None = None
    structure_bias = 0
    last_h1_bucket = None
    cooldown = 0
    diagnostics = {
        "bars": 0,
        "prz_reversal_arms": 0,
        "gc_rvol_wall_arms": 0,
        "zone_evidence_complete_arms": 0,
        "same_bar_pa_candidates": 0,
        "ha_confirmations": 0,
        "expired_or_left_location": 0,
        "gc_zone_expired_or_left": 0,
        "pa_zone_arms": 0,
        "pa_zone_expired_or_left": 0,
        "bos_through_invalidations": 0,
        "gc_missing_bars": 0,
        "buy_directional_bars": 0,
        "sell_directional_bars": 0,
        "buy_prz_pin_bb_candidates": 0,
        "sell_prz_pin_bb_candidates": 0,
        "buy_gc_level_walls": 0,
        "sell_gc_level_walls": 0,
        "buy_prz_full_candidates": 0,
        "sell_prz_full_candidates": 0,
    }

    for index in range(1, len(frame)):
        timestamp = frame.index[index]
        row = frame.iloc[index]
        prior = frame.iloc[index - 1]
        if cooldown > 0:
            cooldown -= 1

        if pending is not None and position is None:
            entry = finite(row["open"])
            stop_anchor = finite(pending["stop_anchor"])
            stop = stop_anchor - pending["atr"] * 0.20 if pending["direction"] == "BUY" else stop_anchor + pending["atr"] * 0.20
            risk = entry - stop if pending["direction"] == "BUY" else stop - entry
            if risk > 0.01:
                target = nearest_target(
                    entry,
                    risk * 1.25,
                    pending["opposing"],
                    "ABOVE" if pending["direction"] == "BUY" else "BELOW",
                )
                if target is None:
                    target = entry + risk * 3.0 if pending["direction"] == "BUY" else entry - risk * 3.0
                position = {
                    **pending,
                    "variant": variant,
                    "entry_time": timestamp,
                    "entry_index": index,
                    "entry": entry,
                    "stop": stop,
                    "be_trigger": entry + risk if pending["direction"] == "BUY" else entry - risk,
                    "target": target,
                    "be_armed": False,
                }
            pending = None

        if position is not None:
            direction = position["direction"]
            active_stop = position["entry"] if position["be_armed"] else position["stop"]
            exit_price = None
            reason = ""
            if direction == "BUY":
                if finite(row["low"]) <= active_stop:
                    exit_price, reason = active_stop, "BREAK_EVEN" if position["be_armed"] else "INITIAL_STOP"
                elif finite(row["high"]) >= position["target"]:
                    exit_price, reason = position["target"], "TP2"
                elif not position["be_armed"] and finite(row["high"]) >= position["be_trigger"]:
                    position["be_armed"] = True
            else:
                if finite(row["high"]) >= active_stop:
                    exit_price, reason = active_stop, "BREAK_EVEN" if position["be_armed"] else "INITIAL_STOP"
                elif finite(row["low"]) <= position["target"]:
                    exit_price, reason = position["target"], "TP2"
                elif not position["be_armed"] and finite(row["low"]) <= position["be_trigger"]:
                    position["be_armed"] = True
            if exit_price is None and index - position["entry_index"] >= 40:
                exit_price, reason = finite(row["close"]), "TIMEOUT"
            if exit_price is not None:
                trade, equity = close_trade(position, timestamp, exit_price, reason, equity, index)
                trades.append(trade)
                curve.append(equity)
                position = None
                cooldown = 3

        h1_bucket = timestamp.floor("1h")
        bull_structure_break_now = False
        bear_structure_break_now = False
        if last_h1_bucket is None or h1_bucket != last_h1_bucket:
            if bool(row.get("h1_bull_break", 0.0)):
                structure_bias = 1
                bull_structure_break_now = True
            elif bool(row.get("h1_bear_break", 0.0)):
                structure_bias = -1
                bear_structure_break_now = True
            last_h1_bucket = h1_bucket

        required = [
            row.get("atr14"), row.get("bb_upper"), row.get("bb_lower"),
            row.get("d_range_high"), row.get("d_range_low"),
            row.get("h4_range_high"), row.get("h4_range_low"),
            row.get("h1_range_high"), row.get("h1_range_low"),
        ]
        if not all(math.isfinite(finite(value, math.nan)) for value in required):
            continue
        diagnostics["bars"] += 1

        gc_required = ("gc_open", "gc_high", "gc_low", "gc_close", "gc_volume", "gc_vol_avg20", "gc_atr14")
        gc_ok = all(math.isfinite(finite(row.get(column), math.nan)) for column in gc_required) and finite(row["gc_vol_avg20"]) > 0
        if not gc_ok:
            diagnostics["gc_missing_bars"] += 1

        bkk = timestamp.tz_convert("Asia/Bangkok")
        is_asia = 6 <= bkk.hour < 15
        fib_near, fib_deep = (0.618, 0.705) if is_asia else (0.720, 0.880)
        ranges = [
            (finite(row["d_range_high"]), finite(row["d_range_low"])),
            (finite(row["h4_range_high"]), finite(row["h4_range_low"])),
            (finite(row["h1_range_high"]), finite(row["h1_range_low"])),
        ]
        buy_cluster = choose_cluster(finite(row["close"]), ranges, fib_near, fib_deep, "BUY")
        sell_cluster = choose_cluster(finite(row["close"]), ranges, fib_near, fib_deep, "SELL")
        atr_value = finite(row["atr14"])
        buy_decision = buy_cluster["lo"] - atr_value * 0.25 <= finite(row["close"]) <= buy_cluster["hi"] + atr_value * 0.75
        sell_decision = sell_cluster["lo"] - atr_value * 0.75 <= finite(row["close"]) <= sell_cluster["hi"] + atr_value * 0.25
        buy_directional = buy_decision and not sell_decision
        sell_directional = sell_decision and not buy_decision
        diagnostics["buy_directional_bars"] += int(buy_directional)
        diagnostics["sell_directional_bars"] += int(sell_directional)

        body = max(abs(finite(row["close"]) - finite(row["open"])), 0.01)
        lower_wick = min(finite(row["open"]), finite(row["close"])) - finite(row["low"])
        upper_wick = finite(row["high"]) - max(finite(row["open"]), finite(row["close"]))
        bull_pin = lower_wick >= body * 2.0 and upper_wick <= body
        bear_pin = upper_wick >= body * 2.0 and lower_wick <= body
        h1_bull_pin = bool(row.get("h1_bull_pin", 0.0))
        h1_bear_pin = bool(row.get("h1_bear_pin", 0.0))
        bull_reversal_pin = bull_pin or h1_bull_pin
        bear_reversal_pin = bear_pin or h1_bear_pin
        buy_bb = finite(row["low"]) <= finite(row["bb_lower"]) and finite(row["close"]) > finite(row["bb_lower"])
        sell_bb = finite(row["high"]) >= finite(row["bb_upper"]) and finite(row["close"]) < finite(row["bb_upper"])

        gc_rvol = finite(row.get("gc_volume")) / finite(row.get("gc_vol_avg20")) if gc_ok else 0.0
        gc_tolerance = finite(row.get("gc_atr14")) * 0.35 if gc_ok else 0.0
        gc_support = finite(row.get("gc_support_h1"), math.nan)
        gc_resistance = finite(row.get("gc_resistance_h1"), math.nan)
        gc_at_support = gc_ok and math.isfinite(gc_support) and finite(row["gc_low"]) <= gc_support + gc_tolerance and finite(row["gc_high"]) >= gc_support - gc_tolerance
        gc_at_resistance = gc_ok and math.isfinite(gc_resistance) and finite(row["gc_high"]) >= gc_resistance - gc_tolerance and finite(row["gc_low"]) <= gc_resistance + gc_tolerance
        gc_buy_wall = gc_at_support and gc_rvol >= 1.20 and finite(row["gc_close"]) >= finite(row["gc_open"])
        gc_sell_wall = gc_at_resistance and gc_rvol >= 1.20 and finite(row["gc_close"]) <= finite(row["gc_open"])

        buy_context = bool(row.get("h1_context_buy", 0.0))
        sell_context = bool(row.get("h1_context_sell", 0.0))
        buy_h1 = bool(row.get("h1_regular_bull", 0.0))
        sell_h1 = bool(row.get("h1_ha_bear", 0.0))
        buy_touch = finite(row["low"]) <= buy_cluster["hi"] and finite(row["high"]) >= buy_cluster["lo"]
        sell_touch = finite(row["high"]) >= sell_cluster["lo"] and finite(row["low"]) <= sell_cluster["hi"]
        legacy_buy_pa = bull_pin or bool(row["ha_bull_reversal"])
        legacy_sell_pa = bear_pin or bool(row["ha_bear_reversal"])
        buy_prz_pin_bb = buy_directional and buy_touch and buy_bb and bull_reversal_pin
        sell_prz_pin_bb = sell_directional and sell_touch and sell_bb and bear_reversal_pin
        diagnostics["buy_prz_pin_bb_candidates"] += int(buy_prz_pin_bb)
        diagnostics["sell_prz_pin_bb_candidates"] += int(sell_prz_pin_bb)
        diagnostics["buy_gc_level_walls"] += int(gc_buy_wall)
        diagnostics["sell_gc_level_walls"] += int(gc_sell_wall)

        if bear_structure_break_now:
            had_buy_evidence = any(
                state is not None
                for state in (gc_rvol_walls["BUY"], pa_zones["BUY"], arms["BUY"])
            )
            gc_rvol_walls["BUY"] = None
            pa_zones["BUY"] = None
            arms["BUY"] = None
            diagnostics["bos_through_invalidations"] += int(had_buy_evidence)
        if bull_structure_break_now:
            had_sell_evidence = any(
                state is not None
                for state in (gc_rvol_walls["SELL"], pa_zones["SELL"], arms["SELL"])
            )
            gc_rvol_walls["SELL"] = None
            pa_zones["SELL"] = None
            arms["SELL"] = None
            diagnostics["bos_through_invalidations"] += int(had_sell_evidence)

        for direction, valid_location in (("BUY", buy_directional), ("SELL", sell_directional)):
            if direction not in allowed_directions:
                gc_rvol_walls[direction] = None
                pa_zones[direction] = None
                continue
            for states, diagnostic in (
                (gc_rvol_walls, "gc_zone_expired_or_left"),
                (pa_zones, "pa_zone_expired_or_left"),
            ):
                zone = states[direction]
                if zone is None:
                    continue
                zone["age"] += 1
                if zone["age"] > 8 or not valid_location:
                    states[direction] = None
                    diagnostics[diagnostic] += 1

        buy_gc_zone_now = buy_directional and buy_touch and gc_buy_wall
        sell_gc_zone_now = sell_directional and sell_touch and gc_sell_wall
        buy_pa_base = buy_context and buy_h1 and buy_prz_pin_bb
        sell_pa_base = sell_context and sell_h1 and sell_prz_pin_bb

        if variant == "HA_ONLY_PRZ":
            buy_prz = buy_context and buy_h1 and buy_directional and buy_touch and buy_bb and legacy_buy_pa
            sell_prz = sell_context and sell_h1 and sell_directional and sell_touch and sell_bb and legacy_sell_pa
        elif variant == "GC_RVOL_AND_PA_SAME_BAR":
            buy_prz = buy_pa_base and buy_gc_zone_now and not bear_structure_break_now
            sell_prz = sell_pa_base and sell_gc_zone_now and not bull_structure_break_now
            diagnostics["same_bar_pa_candidates"] += int(buy_prz) + int(sell_prz)
        else:
            buy_prz = not bear_structure_break_now and (gc_rvol_walls["BUY"] is not None or buy_gc_zone_now) and (pa_zones["BUY"] is not None or buy_pa_base)
            sell_prz = not bull_structure_break_now and (gc_rvol_walls["SELL"] is not None or sell_gc_zone_now) and (pa_zones["SELL"] is not None or sell_pa_base)
        diagnostics["buy_prz_full_candidates"] += int(buy_prz)
        diagnostics["sell_prz_full_candidates"] += int(sell_prz)

        if variant == "GC_RVOL_PA_ANY_ORDER_HA":
            if buy_gc_zone_now and not bear_structure_break_now and "BUY" in allowed_directions:
                gc_rvol_walls["BUY"] = {"age": 0}
                diagnostics["gc_rvol_wall_arms"] += 1
            if sell_gc_zone_now and not bull_structure_break_now and "SELL" in allowed_directions:
                gc_rvol_walls["SELL"] = {"age": 0}
                diagnostics["gc_rvol_wall_arms"] += 1
            if buy_pa_base and not bear_structure_break_now and "BUY" in allowed_directions:
                pa_zones["BUY"] = {"age": 0}
                diagnostics["pa_zone_arms"] += 1
            if sell_pa_base and not bull_structure_break_now and "SELL" in allowed_directions:
                pa_zones["SELL"] = {"age": 0}
                diagnostics["pa_zone_arms"] += 1

        for direction in ("BUY", "SELL"):
            if direction not in allowed_directions:
                arms[direction] = None
                continue
            armed = arms[direction]
            if armed is None:
                continue
            armed["age"] += 1
            valid_location = buy_directional if direction == "BUY" else sell_directional
            if armed["age"] > 8 or not valid_location:
                arms[direction] = None
                diagnostics["expired_or_left_location"] += 1

        confirmed: str | None = None
        if "BUY" in allowed_directions and arms["BUY"] is not None and bool(row["ha_two_bull_higher"]):
            confirmed = "BUY"
        if "SELL" in allowed_directions and arms["SELL"] is not None and bool(row["ha_two_bear_lower"]):
            confirmed = "SELL" if confirmed is None else None
        if confirmed and position is None and pending is None and cooldown == 0:
            signal = arms[confirmed]
            if signal is not None:
                pending = signal
                diagnostics["ha_confirmations"] += 1
                arms = {"BUY": None, "SELL": None}

        if position is None and pending is None and cooldown == 0:
            setup_rows = (
                ("BUY", buy_prz, buy_cluster, sell_cluster),
                ("SELL", sell_prz, sell_cluster, buy_cluster),
            )
            candidates = [item for item in setup_rows if item[0] in allowed_directions and item[1]]
            if len(candidates) == 1:
                direction, _, cluster, opposing_cluster = candidates[0]
                stop_anchor = cluster["lo"] if direction == "BUY" else cluster["hi"]
                opposing = [
                    opposing_cluster["d_lo" if direction == "BUY" else "d_hi"],
                    opposing_cluster["h4_lo" if direction == "BUY" else "h4_hi"],
                    opposing_cluster["h1_lo" if direction == "BUY" else "h1_hi"],
                ]
                arms[direction] = {
                    "direction": direction,
                    "mode": "PRZ_REVERSAL",
                    "age": 0,
                    "signal_time": timestamp,
                    "stop_anchor": stop_anchor,
                    "atr": atr_value,
                    "opposing": opposing,
                    "portfolio_scope": "+".join(allowed_directions),
                }
                diagnostics["prz_reversal_arms"] += 1
                if variant == "GC_RVOL_PA_ANY_ORDER_HA":
                    diagnostics["zone_evidence_complete_arms"] += 1
                    gc_rvol_walls[direction] = None
                    pa_zones[direction] = None

    if position is not None:
        timestamp = frame.index[-1]
        trade, equity = close_trade(position, timestamp, finite(frame.iloc[-1]["close"]), "END_OF_DATA", equity, len(frame) - 1)
        trades.append(trade)
        curve.append(equity)
    return trades, diagnostics, curve


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--spot", type=Path, required=True)
    parser.add_argument("--gc", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    spot_raw = load_ohlcv(args.spot)
    gc_raw = load_ohlcv(args.gc)
    frame = attach_gc(prepare_spot(spot_raw), gc_raw)
    overlap_mask = frame[["gc_open", "gc_high", "gc_low", "gc_close"]].notna().all(axis=1)
    frame = frame.loc[overlap_mask].copy()
    if frame.empty:
        raise SystemExit("No timestamp overlap between spot and GC data")

    results = {}
    all_trades = []
    for variant in VARIANTS:
        trades, diagnostics, curve = run_variant(frame, variant)
        standalone = {}
        standalone_trades = []
        for direction in ("BUY", "SELL"):
            direction_trades, direction_diagnostics, direction_curve = run_variant(
                frame,
                variant,
                allowed_directions=(direction,),
            )
            standalone[direction] = {
                "performance": stats(direction_trades, 10_000.0, direction_curve),
                "diagnostics": direction_diagnostics,
            }
            standalone_trades.extend(direction_trades)
        results[variant] = {
            "combined": {
                "performance": stats(trades, 10_000.0, curve),
                "diagnostics": diagnostics,
            },
            "standalone_directions": standalone,
        }
        all_trades.extend(trades)
        all_trades.extend(standalone_trades)

    report = {
        "strategy": "PINE_V242_GC_FUTURES_RVOL_PRZ_PA_HA_PROXY",
        "dataset_label": args.label,
        "coverage": {
            "start_utc": frame.index.min().isoformat(),
            "end_utc": frame.index.max().isoformat(),
            "bars": len(frame),
        },
        "assumptions": {
            "initial_equity": 10_000.0,
            "risk_per_trade_pct": 1.0,
            "round_trip_cost_points": 0.50,
            "entry": "next M15 open after confirmed HA15 #2",
            "gc_rvol_role": "COMEX GC traded-contract RVOL validates the PRZ only; it never triggers an entry",
            "entry_sequence": "Kivanc PRZ -> GC contract RVOL wall plus M15/H1 pinbar+BB in either order -> confirmed HA15 #2",
            "bos_invalidation": "Opposite confirmed H1 BOS clears GC-RVOL, PA and pending entry evidence",
            "gc_rvol_min": 1.20,
            "gc_h1_sr_tolerance_atr": 0.35,
            "arm_ttl_m15_bars": 8,
            "session_guard": "repository SessionClock",
            "ambiguous_bar_order": "stop, target, then BE arm",
        },
        "data": {
            "spot": str(args.spot.resolve()),
            "gc": str(args.gc.resolve()),
            "spot_sha256": hashlib.sha256(args.spot.read_bytes()).hexdigest(),
            "gc_sha256": hashlib.sha256(args.gc.read_bytes()).hexdigest(),
        },
        "results": results,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / f"{args.label}_report.json"
    trades_path = args.output_dir / f"{args.label}_trades.csv"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(all_trades).to_csv(trades_path, index=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT_JSON={report_path.resolve()}")
    print(f"TRADES_CSV={trades_path.resolve()}")


if __name__ == "__main__":
    main()
