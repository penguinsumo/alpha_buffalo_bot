#!/usr/bin/env python3
"""Backtest the AlphaBuff H1/M15 decision stack on local XAUUSD M15 data.

This is the data-constrained H1/M15 variant requested on 2026-07-13:

* H1 EMA200 + RSI14 provide directional context.
* BUY requires the last confirmed regular H1 candle to be bullish.
* SELL requires the last confirmed H1 Heikin-Ashi candle to be bearish.
* Confirmed D1/H4/H1 swing PRZ clusters, session Fibonacci profiles,
  M15 sweep/reclaim, Bollinger rejection and M15 PA/HA triggers are used.
* After 1R/break-even is armed, a strong opposite M15 Heikin-Ashi candle
  closes the runner. A hard opposing PRZ/3R target remains active.

The local data has zero volume, so the VSA-volume branch is deliberately not
invented.  The report records this limitation and the exact data coverage.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


STRATEGY = "ALPHABUFF_H1_M15_HA15_STRONG"
POINT_VALUE_PER_LOT = 100.0


@dataclass
class Trade:
    direction: str
    signal_time: str
    entry_time: str
    exit_time: str
    entry: float
    initial_stop: float
    be_trigger: float
    target: float
    exit: float
    exit_reason: str
    cluster_score: int
    cluster_count: int
    risk_points: float
    gross_points: float
    net_points: float
    gross_r: float
    net_r: float
    lots: float
    gross_pnl: float
    costs: float
    net_pnl: float
    equity_after: float


def rma(series: pd.Series, length: int) -> pd.Series:
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=length).mean()


def rsi(series: pd.Series, length: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = rma(gain, length)
    avg_loss = rma(loss, length)
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - 100.0 / (1.0 + rs)
    out = out.where(avg_loss != 0.0, 100.0)
    return out.where(avg_gain != 0.0, 0.0)


def atr(frame: pd.DataFrame, length: int = 14) -> pd.Series:
    prev_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - prev_close).abs(),
            (frame["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return rma(true_range, length)


def heikin_ashi(frame: pd.DataFrame) -> pd.DataFrame:
    ha_close = frame[["open", "high", "low", "close"]].mean(axis=1)
    ha_open = np.empty(len(frame), dtype=float)
    ha_open[0] = (frame["open"].iloc[0] + frame["close"].iloc[0]) / 2.0
    close_values = ha_close.to_numpy()
    for i in range(1, len(frame)):
        ha_open[i] = (ha_open[i - 1] + close_values[i - 1]) / 2.0
    out = pd.DataFrame(index=frame.index)
    out["ha_open"] = ha_open
    out["ha_close"] = ha_close
    out["ha_high"] = np.maximum.reduce(
        [frame["high"].to_numpy(), ha_open, close_values]
    )
    out["ha_low"] = np.minimum.reduce(
        [frame["low"].to_numpy(), ha_open, close_values]
    )
    return out


def ohlc_resample(frame: pd.DataFrame, rule: str) -> pd.DataFrame:
    return (
        frame.resample(rule, label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
    )


def confirmed_swing_range(
    frame: pd.DataFrame, lookback: int, pivot_bars: int = 3
) -> pd.DataFrame:
    """Approximate Pine confirmed pivots without using future bars at runtime."""
    highs = frame["high"]
    lows = frame["low"]
    pivot_high_at_confirmation = pd.Series(np.nan, index=frame.index)
    pivot_low_at_confirmation = pd.Series(np.nan, index=frame.index)

    for center in range(pivot_bars, len(frame) - pivot_bars):
        window = slice(center - pivot_bars, center + pivot_bars + 1)
        confirmation = center + pivot_bars
        if highs.iloc[center] >= highs.iloc[window].max():
            pivot_high_at_confirmation.iloc[confirmation] = highs.iloc[center]
        if lows.iloc[center] <= lows.iloc[window].min():
            pivot_low_at_confirmation.iloc[confirmation] = lows.iloc[center]

    last_high = pivot_high_at_confirmation.ffill()
    last_low = pivot_low_at_confirmation.ffill()
    fallback_high = highs.rolling(lookback, min_periods=1).max()
    fallback_low = lows.rolling(lookback, min_periods=1).min()
    raw_high = last_high.where(last_high.notna(), fallback_high)
    raw_low = last_low.where(last_low.notna(), fallback_low)

    result = pd.DataFrame(index=frame.index)
    # Pine helper returns [1], keeping only the prior confirmed HTF state.
    result["range_high"] = pd.concat([raw_high, raw_low], axis=1).max(axis=1).shift(1)
    result["range_low"] = pd.concat([raw_high, raw_low], axis=1).min(axis=1).shift(1)
    return result


def align(source: pd.Series, target_index: pd.DatetimeIndex) -> pd.Series:
    return source.reindex(target_index, method="ffill")


def overlap(lo1: float, hi1: float, lo2: float, hi2: float) -> bool:
    return all(math.isfinite(x) for x in (lo1, hi1, lo2, hi2)) and max(lo1, lo2) <= min(hi1, hi2)


def distance(price: float, lo: float, hi: float) -> float:
    if price < lo:
        return lo - price
    if price > hi:
        return price - hi
    return 0.0


def zones(high: float, low: float, fib_near: float, fib_deep: float) -> tuple[float, float, float, float]:
    width = max(high - low, 0.01)
    buy_lo = high - width * fib_deep
    buy_hi = high - width * fib_near
    sell_lo = low + width * fib_near
    sell_hi = low + width * fib_deep
    return buy_lo, buy_hi, sell_lo, sell_hi


def choose_cluster(
    close: float,
    ranges: Iterable[tuple[float, float]],
    fib_near: float,
    fib_deep: float,
    side: str,
) -> dict[str, float | int]:
    zone_rows = [zones(high, low, fib_near, fib_deep) for high, low in ranges]
    if side == "BUY":
        pairs = [(z[0], z[1]) for z in zone_rows]
        extremes = [low for _, low in ranges]
    else:
        pairs = [(z[2], z[3]) for z in zone_rows]
        extremes = [high for high, _ in ranges]

    ov01 = overlap(*pairs[0], *pairs[1])
    ov02 = overlap(*pairs[0], *pairs[2])
    ov12 = overlap(*pairs[1], *pairs[2])
    scores = [
        3 + (2 if ov01 else 0) + (1 if ov02 else 0),
        2 + (3 if ov01 else 0) + (1 if ov12 else 0),
        1 + (3 if ov02 else 0) + (2 if ov12 else 0),
    ]
    memberships = [
        [0] + ([1] if ov01 else []) + ([2] if ov02 else []),
        [1] + ([0] if ov01 else []) + ([2] if ov12 else []),
        [2] + ([0] if ov02 else []) + ([1] if ov12 else []),
    ]
    choice = 0
    best_distance = distance(close, *pairs[0])
    for candidate in (1, 2):
        candidate_distance = distance(close, *pairs[candidate])
        if scores[candidate] > scores[choice] or (
            scores[candidate] == scores[choice] and candidate_distance < best_distance
        ):
            choice = candidate
            best_distance = candidate_distance

    members = memberships[choice]
    return {
        "lo": min(pairs[i][0] for i in members),
        "hi": max(pairs[i][1] for i in members),
        "extreme": extremes[choice],
        "score": scores[choice],
        "count": len(members),
        "d_lo": pairs[0][0],
        "d_hi": pairs[0][1],
        "h4_lo": pairs[1][0],
        "h4_hi": pairs[1][1],
        "h1_lo": pairs[2][0],
        "h1_hi": pairs[2][1],
    }


def nearest_target(entry: float, minimum: float, candidates: Iterable[float], side: str) -> float | None:
    finite = [float(value) for value in candidates if math.isfinite(float(value))]
    if side == "ABOVE":
        valid = [value for value in finite if value >= entry + minimum]
        return min(valid) if valid else None
    valid = [value for value in finite if value <= entry - minimum]
    return max(valid) if valid else None


def prepare(path: Path) -> tuple[pd.DataFrame, dict[str, str | int | float | bool]]:
    raw = pd.read_csv(path)
    required = {"datetime", "open", "high", "low", "close"}
    missing = required - set(raw.columns)
    if missing:
        raise ValueError(f"Missing CSV columns: {sorted(missing)}")
    if "volume" not in raw:
        raw["volume"] = 0.0
    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = (
        raw.dropna(subset=["datetime", "open", "high", "low", "close"])
        .drop_duplicates("datetime", keep="last")
        .set_index("datetime")
        .sort_index()
    )
    source_start = raw.index.min()
    source_end = raw.index.max()

    bkk = raw.index.tz_convert("Asia/Bangkok")
    market_mask = bkk.weekday < 5
    frame = raw.loc[market_mask].copy()
    if frame.empty:
        raise ValueError("No Monday-Friday rows remain after the market guard")

    frame["atr14"] = atr(frame, 14)
    basis = frame["close"].rolling(20, min_periods=20).mean()
    deviation = frame["close"].rolling(20, min_periods=20).std(ddof=0) * 2.0
    frame["bb_basis"] = basis
    frame["bb_upper"] = basis + deviation
    frame["bb_lower"] = basis - deviation

    ha15 = heikin_ashi(frame)
    frame = frame.join(ha15)
    ha_range = (frame["ha_high"] - frame["ha_low"]).clip(lower=0.01)
    ha_body = (frame["ha_close"] - frame["ha_open"]).abs()
    prior_body_mean = ha_body.rolling(20, min_periods=20).mean().shift(1)
    frame["ha_bull_reversal"] = (frame["ha_close"].shift(1) <= frame["ha_open"].shift(1)) & (frame["ha_close"] > frame["ha_open"])
    frame["ha_bear_reversal"] = (frame["ha_close"].shift(1) >= frame["ha_open"].shift(1)) & (frame["ha_close"] < frame["ha_open"])
    frame["ha_strong_bear"] = (
        (frame["ha_close"] < frame["ha_open"])
        & (ha_body >= prior_body_mean * 1.20)
        & (ha_body / ha_range >= 0.60)
    )
    frame["ha_strong_bull"] = (
        (frame["ha_close"] > frame["ha_open"])
        & (ha_body >= prior_body_mean * 1.20)
        & (ha_body / ha_range >= 0.60)
    )

    h1 = ohlc_resample(frame, "1h")
    h4 = ohlc_resample(frame, "4h")
    d1 = ohlc_resample(frame, "1D")
    h1_ha = heikin_ashi(h1)

    frame["h1_context_close"] = align(h1["close"].shift(1), frame.index)
    frame["h1_ema200"] = align(h1["close"].ewm(span=200, adjust=False, min_periods=200).mean().shift(1), frame.index)
    frame["h1_rsi14"] = align(rsi(h1["close"], 14).shift(1), frame.index)
    frame["h1_regular_bull"] = align((h1["close"].shift(1) > h1["open"].shift(1)).astype(float), frame.index).fillna(0.0).astype(bool)
    frame["h1_ha_bear"] = align((h1_ha["ha_close"].shift(1) < h1_ha["ha_open"].shift(1)).astype(float), frame.index).fillna(0.0).astype(bool)

    previous_high = h1["high"].shift(1).rolling(5, min_periods=5).max()
    previous_low = h1["low"].shift(1).rolling(5, min_periods=5).min()
    bull_break = (h1["close"] > previous_high).shift(1)
    bear_break = (h1["close"] < previous_low).shift(1)
    frame["bull_break"] = align(bull_break.astype(float), frame.index).fillna(0.0).astype(bool)
    frame["bear_break"] = align(bear_break.astype(float), frame.index).fillna(0.0).astype(bool)

    for prefix, source, lookback in (("d", d1, 20), ("h4", h4, 30), ("h1", h1, 60)):
        ranges = confirmed_swing_range(source, lookback=lookback, pivot_bars=3)
        frame[f"{prefix}_range_high"] = align(ranges["range_high"], frame.index)
        frame[f"{prefix}_range_low"] = align(ranges["range_low"], frame.index)

    metadata = {
        "source_file": str(path.resolve()),
        "source_rows": int(len(raw)),
        "source_start_utc": source_start.isoformat(),
        "source_end_utc": source_end.isoformat(),
        "market_rows_after_bangkok_weekend_guard": int(len(frame)),
        "volume_available": bool((frame["volume"] > 0).any()),
    }
    return frame, metadata


def backtest(
    frame: pd.DataFrame,
    initial_equity: float,
    risk_fraction: float,
    round_trip_cost_points: float,
) -> tuple[list[Trade], list[float], dict[str, int]]:
    trades: list[Trade] = []
    equity_curve = [initial_equity]
    equity = initial_equity
    diagnostics = {
        "bars_evaluated": 0,
        "buy_sweeps": 0,
        "sell_sweeps": 0,
        "buy_context_bars": 0,
        "sell_context_bars": 0,
        "buy_cluster_bars": 0,
        "sell_cluster_bars": 0,
        "buy_location_bars": 0,
        "sell_location_bars": 0,
        "buy_bb_bars": 0,
        "sell_bb_bars": 0,
        "buy_trigger_bars": 0,
        "sell_trigger_bars": 0,
        "buy_h1_permission_bars": 0,
        "sell_h1_permission_bars": 0,
        "buy_all_but_h1": 0,
        "sell_all_but_h1": 0,
        "buy_setups": 0,
        "sell_setups": 0,
    }

    structure_bias = 0
    last_h1_bucket = None
    previous_asia = None
    buy_armed = False
    sell_armed = False
    buy_sweep_low = math.nan
    sell_sweep_high = math.nan
    buy_age = 0
    sell_age = 0
    cooldown = 0
    open_trade: dict | None = None
    pending_entry: dict | None = None

    for i, (timestamp, row) in enumerate(frame.iterrows()):
        if i < 2:
            continue
        bkk = timestamp.tz_convert("Asia/Bangkok")
        market_open = bkk.weekday() < 5
        is_asia = market_open and 6 <= bkk.hour < 15
        h1_bucket = timestamp.floor("1h")

        if last_h1_bucket is None or h1_bucket != last_h1_bucket:
            if bool(row["bull_break"]):
                structure_bias = 1
            elif bool(row["bear_break"]):
                structure_bias = -1
            last_h1_bucket = h1_bucket

        if previous_asia is None:
            previous_asia = is_asia
        elif is_asia != previous_asia:
            buy_armed = sell_armed = False
            buy_sweep_low = sell_sweep_high = math.nan
            buy_age = sell_age = 0
            previous_asia = is_asia

        if cooldown > 0:
            cooldown -= 1

        # Signals are known only after the prior M15 bar closes; fill at this bar's open.
        if pending_entry is not None and open_trade is None and market_open:
            entry = float(row["open"])
            direction = pending_entry["direction"]
            if direction == "BUY":
                stop_anchor = min(pending_entry["cluster"]["lo"], pending_entry["sweep_extreme"])
                stop = stop_anchor - pending_entry["atr"] * 0.20
                risk = entry - stop
            else:
                stop_anchor = max(pending_entry["cluster"]["hi"], pending_entry["sweep_extreme"])
                stop = stop_anchor + pending_entry["atr"] * 0.20
                risk = stop - entry
            if risk > 0.01 and math.isfinite(risk):
                if direction == "BUY":
                    be_trigger = entry + risk
                    target_candidate = nearest_target(
                        entry,
                        risk * 1.25,
                        pending_entry["opposing_candidates"],
                        "ABOVE",
                    )
                    target = target_candidate if target_candidate is not None else entry + risk * 3.0
                else:
                    be_trigger = entry - risk
                    target_candidate = nearest_target(
                        entry,
                        risk * 1.25,
                        pending_entry["opposing_candidates"],
                        "BELOW",
                    )
                    target = target_candidate if target_candidate is not None else entry - risk * 3.0
                open_trade = {
                    **pending_entry,
                    "entry_time": timestamp,
                    "entry": entry,
                    "stop": stop,
                    "risk": risk,
                    "be_trigger": be_trigger,
                    "target": target,
                    "be_armed": False,
                }
                buy_armed = sell_armed = False
                buy_sweep_low = sell_sweep_high = math.nan
                buy_age = sell_age = 0
            pending_entry = None

        if open_trade is not None:
            direction = open_trade["direction"]
            be_stop = open_trade["entry"]
            active_stop = be_stop if open_trade["be_armed"] else open_trade["stop"]
            exit_price = None
            exit_reason = None
            if direction == "BUY":
                if float(row["low"]) <= active_stop:
                    exit_price = active_stop
                    exit_reason = "BREAK_EVEN" if open_trade["be_armed"] else "INITIAL_STOP"
                elif not open_trade["be_armed"] and float(row["high"]) >= open_trade["be_trigger"]:
                    open_trade["be_armed"] = True
                    if structure_bias != 1:
                        exit_price = open_trade["be_trigger"]
                        exit_reason = "TP1_NO_BULL_STRUCTURE"
                elif open_trade["be_armed"] and float(row["high"]) >= open_trade["target"]:
                    exit_price = open_trade["target"]
                    exit_reason = "OPPOSING_PRZ_OR_3R"
                elif open_trade["be_armed"] and bool(row["ha_strong_bear"]):
                    exit_price = float(row["close"])
                    exit_reason = "HA15_STRONG_BEAR"
            else:
                if float(row["high"]) >= active_stop:
                    exit_price = active_stop
                    exit_reason = "BREAK_EVEN" if open_trade["be_armed"] else "INITIAL_STOP"
                elif not open_trade["be_armed"] and float(row["low"]) <= open_trade["be_trigger"]:
                    open_trade["be_armed"] = True
                    if structure_bias != -1:
                        exit_price = open_trade["be_trigger"]
                        exit_reason = "TP1_NO_BEAR_STRUCTURE"
                elif open_trade["be_armed"] and float(row["low"]) <= open_trade["target"]:
                    exit_price = open_trade["target"]
                    exit_reason = "OPPOSING_PRZ_OR_3R"
                elif open_trade["be_armed"] and bool(row["ha_strong_bull"]):
                    exit_price = float(row["close"])
                    exit_reason = "HA15_STRONG_BULL"

            if exit_price is not None:
                signed_points = (
                    exit_price - open_trade["entry"]
                    if direction == "BUY"
                    else open_trade["entry"] - exit_price
                )
                risk_dollars = equity * risk_fraction
                lots = risk_dollars / (open_trade["risk"] * POINT_VALUE_PER_LOT)
                gross_pnl = signed_points * POINT_VALUE_PER_LOT * lots
                costs = round_trip_cost_points * POINT_VALUE_PER_LOT * lots
                net_pnl = gross_pnl - costs
                equity += net_pnl
                trade = Trade(
                    direction=direction,
                    signal_time=open_trade["signal_time"].isoformat(),
                    entry_time=open_trade["entry_time"].isoformat(),
                    exit_time=timestamp.isoformat(),
                    entry=open_trade["entry"],
                    initial_stop=open_trade["stop"],
                    be_trigger=open_trade["be_trigger"],
                    target=open_trade["target"],
                    exit=exit_price,
                    exit_reason=exit_reason,
                    cluster_score=int(open_trade["cluster"]["score"]),
                    cluster_count=int(open_trade["cluster"]["count"]),
                    risk_points=open_trade["risk"],
                    gross_points=signed_points,
                    net_points=signed_points - round_trip_cost_points,
                    gross_r=signed_points / open_trade["risk"],
                    net_r=(signed_points - round_trip_cost_points) / open_trade["risk"],
                    lots=lots,
                    gross_pnl=gross_pnl,
                    costs=costs,
                    net_pnl=net_pnl,
                    equity_after=equity,
                )
                trades.append(trade)
                equity_curve.append(equity)
                open_trade = None
                cooldown = 3

        if open_trade is not None or pending_entry is not None or not market_open:
            continue

        required = [
            row["atr14"], row["bb_upper"], row["bb_lower"],
            row["h1_context_close"], row["h1_ema200"], row["h1_rsi14"],
            row["d_range_high"], row["d_range_low"],
            row["h4_range_high"], row["h4_range_low"],
            row["h1_range_high"], row["h1_range_low"],
        ]
        if not all(math.isfinite(float(value)) for value in required):
            continue
        diagnostics["bars_evaluated"] += 1

        fib_near, fib_deep = (0.618, 0.705) if is_asia else (0.720, 0.880)
        ranges = [
            (float(row["d_range_high"]), float(row["d_range_low"])),
            (float(row["h4_range_high"]), float(row["h4_range_low"])),
            (float(row["h1_range_high"]), float(row["h1_range_low"])),
        ]
        buy_cluster = choose_cluster(float(row["close"]), ranges, fib_near, fib_deep, "BUY")
        sell_cluster = choose_cluster(float(row["close"]), ranges, fib_near, fib_deep, "SELL")

        if buy_armed:
            buy_age += 1
            buy_sweep_low = min(buy_sweep_low, float(row["low"]))
        if sell_armed:
            sell_age += 1
            sell_sweep_high = max(sell_sweep_high, float(row["high"]))

        buy_invalid = float(row["low"]) < float(buy_cluster["extreme"]) - float(row["atr14"]) * 0.15
        sell_invalid = float(row["high"]) > float(sell_cluster["extreme"]) + float(row["atr14"]) * 0.15
        if buy_invalid or buy_age > 8:
            buy_armed, buy_sweep_low, buy_age = False, math.nan, 0
        if sell_invalid or sell_age > 8:
            sell_armed, sell_sweep_high, sell_age = False, math.nan, 0

        buy_candidate = float(row["low"]) < float(buy_cluster["lo"]) and float(row["low"]) >= float(buy_cluster["extreme"]) - float(row["atr14"]) * 0.15
        sell_candidate = float(row["high"]) > float(sell_cluster["hi"]) and float(row["high"]) <= float(sell_cluster["extreme"]) + float(row["atr14"]) * 0.15
        if buy_candidate:
            buy_armed = True
            buy_sweep_low = float(row["low"]) if not math.isfinite(buy_sweep_low) else min(buy_sweep_low, float(row["low"]))
            buy_age = 0
            diagnostics["buy_sweeps"] += 1
        if sell_candidate:
            sell_armed = True
            sell_sweep_high = float(row["high"]) if not math.isfinite(sell_sweep_high) else max(sell_sweep_high, float(row["high"]))
            sell_age = 0
            diagnostics["sell_sweeps"] += 1

        buy_reclaim = buy_armed and float(row["close"]) >= float(buy_cluster["lo"]) and float(row["close"]) <= float(buy_cluster["hi"]) + float(row["atr14"]) * 0.25
        sell_reclaim = sell_armed and float(row["close"]) <= float(sell_cluster["hi"]) and float(row["close"]) >= float(sell_cluster["lo"]) - float(row["atr14"]) * 0.25
        buy_touch = float(row["low"]) <= float(buy_cluster["hi"]) and float(row["high"]) >= float(buy_cluster["lo"])
        sell_touch = float(row["high"]) >= float(sell_cluster["lo"]) and float(row["low"]) <= float(sell_cluster["hi"])
        buy_location = buy_touch if is_asia else buy_reclaim
        sell_location = sell_touch if is_asia else sell_reclaim

        candle_range = max(float(row["high"] - row["low"]), 0.01)
        body = max(abs(float(row["close"] - row["open"])), 0.01)
        lower_wick = min(float(row["open"]), float(row["close"])) - float(row["low"])
        upper_wick = float(row["high"]) - max(float(row["open"]), float(row["close"]))
        bull_pin = lower_wick >= body * 2.0 and upper_wick <= body
        bear_pin = upper_wick >= body * 2.0 and lower_wick <= body
        buy_trigger = bull_pin or bool(row["ha_bull_reversal"])
        sell_trigger = bear_pin or bool(row["ha_bear_reversal"])
        buy_bb = float(row["low"]) <= float(row["bb_lower"]) and float(row["close"]) > float(row["bb_lower"])
        sell_bb = float(row["high"]) >= float(row["bb_upper"]) and float(row["close"]) < float(row["bb_upper"])
        buy_context = float(row["h1_context_close"]) > float(row["h1_ema200"]) and float(row["h1_rsi14"]) > 50.0
        sell_context = float(row["h1_context_close"]) < float(row["h1_ema200"]) and float(row["h1_rsi14"]) < 50.0

        buy_cluster_pass = int(buy_cluster["count"]) >= 2
        sell_cluster_pass = int(sell_cluster["count"]) >= 2
        buy_h1_permission = bool(row["h1_regular_bull"])
        sell_h1_permission = bool(row["h1_ha_bear"])
        diagnostics["buy_context_bars"] += int(buy_context)
        diagnostics["sell_context_bars"] += int(sell_context)
        diagnostics["buy_cluster_bars"] += int(buy_cluster_pass)
        diagnostics["sell_cluster_bars"] += int(sell_cluster_pass)
        diagnostics["buy_location_bars"] += int(buy_location)
        diagnostics["sell_location_bars"] += int(sell_location)
        diagnostics["buy_bb_bars"] += int(buy_bb)
        diagnostics["sell_bb_bars"] += int(sell_bb)
        diagnostics["buy_trigger_bars"] += int(buy_trigger)
        diagnostics["sell_trigger_bars"] += int(sell_trigger)
        diagnostics["buy_h1_permission_bars"] += int(buy_h1_permission)
        diagnostics["sell_h1_permission_bars"] += int(sell_h1_permission)
        diagnostics["buy_all_but_h1"] += int(cooldown == 0 and buy_context and buy_cluster_pass and buy_location and buy_bb and buy_trigger)
        diagnostics["sell_all_but_h1"] += int(cooldown == 0 and sell_context and sell_cluster_pass and sell_location and sell_bb and sell_trigger)

        buy_setup = (
            cooldown == 0 and buy_context and buy_cluster_pass
            and buy_location and buy_bb and buy_trigger and buy_h1_permission
        )
        sell_setup = (
            cooldown == 0 and sell_context and sell_cluster_pass
            and sell_location and sell_bb and sell_trigger and sell_h1_permission
        )

        if buy_setup and not sell_setup:
            diagnostics["buy_setups"] += 1
            pending_entry = {
                "direction": "BUY",
                "signal_time": timestamp,
                "cluster": buy_cluster,
                "sweep_extreme": buy_sweep_low if math.isfinite(buy_sweep_low) else float(buy_cluster["lo"]),
                "atr": float(row["atr14"]),
                "opposing_candidates": [sell_cluster["d_lo"], sell_cluster["h4_lo"], sell_cluster["h1_lo"]],
            }
        elif sell_setup and not buy_setup:
            diagnostics["sell_setups"] += 1
            pending_entry = {
                "direction": "SELL",
                "signal_time": timestamp,
                "cluster": sell_cluster,
                "sweep_extreme": sell_sweep_high if math.isfinite(sell_sweep_high) else float(sell_cluster["hi"]),
                "atr": float(row["atr14"]),
                "opposing_candidates": [buy_cluster["d_hi"], buy_cluster["h4_hi"], buy_cluster["h1_hi"]],
            }

    # Mark-to-market the final open position at the last available close.
    if open_trade is not None:
        timestamp = frame.index[-1]
        final_close = float(frame["close"].iloc[-1])
        direction = open_trade["direction"]
        signed_points = final_close - open_trade["entry"] if direction == "BUY" else open_trade["entry"] - final_close
        risk_dollars = equity * risk_fraction
        lots = risk_dollars / (open_trade["risk"] * POINT_VALUE_PER_LOT)
        gross_pnl = signed_points * POINT_VALUE_PER_LOT * lots
        costs = round_trip_cost_points * POINT_VALUE_PER_LOT * lots
        net_pnl = gross_pnl - costs
        equity += net_pnl
        trades.append(
            Trade(
                direction=direction,
                signal_time=open_trade["signal_time"].isoformat(),
                entry_time=open_trade["entry_time"].isoformat(),
                exit_time=timestamp.isoformat(),
                entry=open_trade["entry"],
                initial_stop=open_trade["stop"],
                be_trigger=open_trade["be_trigger"],
                target=open_trade["target"],
                exit=final_close,
                exit_reason="END_OF_DATA",
                cluster_score=int(open_trade["cluster"]["score"]),
                cluster_count=int(open_trade["cluster"]["count"]),
                risk_points=open_trade["risk"],
                gross_points=signed_points,
                net_points=signed_points - round_trip_cost_points,
                gross_r=signed_points / open_trade["risk"],
                net_r=(signed_points - round_trip_cost_points) / open_trade["risk"],
                lots=lots,
                gross_pnl=gross_pnl,
                costs=costs,
                net_pnl=net_pnl,
                equity_after=equity,
            )
        )
        equity_curve.append(equity)

    return trades, equity_curve, diagnostics


def max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak else 0.0)
    return worst * 100.0


def summarize(
    trades: list[Trade],
    curve: list[float],
    initial_equity: float,
    metadata: dict,
    diagnostics: dict,
    risk_fraction: float,
    cost_points: float,
) -> dict:
    wins = [trade for trade in trades if trade.net_pnl > 0]
    losses = [trade for trade in trades if trade.net_pnl < 0]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    profit_factor = gross_profit / gross_loss if gross_loss else None
    final_equity = curve[-1]

    def side(direction: str) -> dict:
        selected = [trade for trade in trades if trade.direction == direction]
        side_wins = [trade for trade in selected if trade.net_pnl > 0]
        return {
            "trades": len(selected),
            "wins": len(side_wins),
            "win_rate_pct": len(side_wins) / len(selected) * 100.0 if selected else 0.0,
            "net_pnl": sum(trade.net_pnl for trade in selected),
            "average_net_r": float(np.mean([trade.net_r for trade in selected])) if selected else 0.0,
        }

    reasons: dict[str, int] = {}
    for trade in trades:
        reasons[trade.exit_reason] = reasons.get(trade.exit_reason, 0) + 1

    return {
        "strategy": STRATEGY,
        "requested_window": {"start": "2026-01-01", "end": "2026-07-11"},
        "actual_source_coverage": {
            "start_utc": metadata["source_start_utc"],
            "end_utc": metadata["source_end_utc"],
        },
        "portfolio": {
            "initial_equity": initial_equity,
            "final_equity": final_equity,
            "net_profit": final_equity - initial_equity,
            "return_pct": (final_equity / initial_equity - 1.0) * 100.0,
            "max_drawdown_pct": max_drawdown(curve),
            "risk_per_trade_pct": risk_fraction * 100.0,
            "round_trip_cost_points": cost_points,
        },
        "performance": {
            "trades": len(trades),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate_pct": len(wins) / len(trades) * 100.0 if trades else 0.0,
            "profit_factor": profit_factor,
            "average_net_r": float(np.mean([trade.net_r for trade in trades])) if trades else 0.0,
            "total_costs": sum(trade.costs for trade in trades),
            "buy": side("BUY"),
            "sell": side("SELL"),
            "exit_reasons": reasons,
        },
        "diagnostics": diagnostics,
        "data": metadata,
        "assumptions": {
            "timeframes": "H1 context/permission + M15 trigger/exit",
            "strong_ha_definition": "opposite HA body >= 1.20x prior 20-bar mean and body/range >= 60%",
            "exit_activation": "strong HA M15 exit only after 1R break-even is armed",
            "position_limit": "one position at a time",
            "position_sizing": "1% compounded equity risk at initial stop",
            "xauusd_point_value": "$100 per $1 move per standard lot",
            "vsa": "not used; the stitched proxy mixes futures volume with zero-volume spot data",
            "dxy": "not used; H1 EMA200 and RSI14 must both align",
            "execution": "signal at M15 close, entry at next M15 open; stop has priority on ambiguous bars",
        },
        "status": "PARTIAL_COVERAGE" if str(metadata["source_start_utc"])[:10] > "2026-01-01" else "FULL_COVERAGE",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--round-trip-cost-points", type=float, default=0.50)
    args = parser.parse_args()

    frame, metadata = prepare(args.data)
    trades, curve, diagnostics = backtest(
        frame,
        initial_equity=args.initial_equity,
        risk_fraction=args.risk_pct / 100.0,
        round_trip_cost_points=args.round_trip_cost_points,
    )
    report = summarize(
        trades,
        curve,
        args.initial_equity,
        metadata,
        diagnostics,
        args.risk_pct / 100.0,
        args.round_trip_cost_points,
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "h1_m15_ha15_strong_report.json"
    csv_path = args.output_dir / "h1_m15_ha15_strong_trades.csv"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    pd.DataFrame([asdict(trade) for trade in trades]).to_csv(csv_path, index=False)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"REPORT_JSON={json_path.resolve()}")
    print(f"TRADES_CSV={csv_path.resolve()}")


if __name__ == "__main__":
    main()
