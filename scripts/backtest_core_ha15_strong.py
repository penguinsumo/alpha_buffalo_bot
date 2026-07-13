#!/usr/bin/env python3
"""Sequential Core V4 signal backtest with a strong M15 HA mirror exit."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from engine_v4.buy_engine import BuySignalEngine
from engine_v4.final_gate import FinalGate
from engine_v4.indicators import add_indicators
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock


POINT_VALUE = 100.0


def finite(value, default=0.0) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result if math.isfinite(result) else default


def rr(signal: dict, entry: float | None = None) -> float:
    direction = str(signal.get("direction", "")).upper()
    entry = finite(signal.get("entry")) if entry is None else entry
    stop = finite(signal.get("sl"))
    target = finite(signal.get("tp"))
    if direction == "BUY" and stop < entry < target:
        return (target - entry) / (entry - stop)
    if direction == "SELL" and target < entry < stop:
        return (entry - target) / (stop - entry)
    return 0.0


def rank(signal: dict) -> tuple[int, int, int, int, float]:
    return (
        int(bool(signal.get("bb_prz_confluence") or signal.get("zone_confluence"))),
        int(bool(signal.get("pine_valid"))),
        int(str(signal.get("setup_state", "")).upper().endswith("CF_READY")),
        int(finite(signal.get("v5_quality_score"))),
        rr(signal),
    )


def load_data(path: Path, clock: SessionClock) -> tuple[pd.DataFrame, dict]:
    raw = pd.read_csv(path)
    raw["datetime"] = pd.to_datetime(raw["datetime"], utc=True, errors="coerce")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in raw:
            raw[column] = 0.0
        raw[column] = pd.to_numeric(raw[column], errors="coerce")
    raw = (
        raw.dropna(subset=["datetime", "open", "high", "low", "close"])
        .drop_duplicates("datetime", keep="last")
        .set_index("datetime")
        .sort_index()
    )
    source_start, source_end = raw.index.min(), raw.index.max()
    open_mask = [clock.get(timestamp).session != "CLOSED" for timestamp in raw.index]
    market = raw.loc[open_mask].copy()
    frame = add_indicators(market)
    required = ["ATR14", "BB_Mid", "BB_Lower", "BB_Upper", "EMA20", "EMA50"]
    frame = frame.dropna(subset=required).copy()

    ha_body = (frame["HA_Close"] - frame["HA_Open"]).abs()
    # True HA range includes the synthetic open/close as well as real extremes.
    ha_high = pd.concat([frame["high"], frame["HA_Open"], frame["HA_Close"]], axis=1).max(axis=1)
    ha_low = pd.concat([frame["low"], frame["HA_Open"], frame["HA_Close"]], axis=1).min(axis=1)
    ha_range = (ha_high - ha_low).clip(lower=0.01)
    prior_mean = ha_body.rolling(20, min_periods=20).mean().shift(1)
    frame["HA_Strong_Bear"] = (
        frame["HA_Bearish"] & (ha_body >= prior_mean * 1.20) & (ha_body / ha_range >= 0.60)
    )
    frame["HA_Strong_Bull"] = (
        frame["HA_Bullish"] & (ha_body >= prior_mean * 1.20) & (ha_body / ha_range >= 0.60)
    )

    # Confirmed-H1 permissions for experimental BUY routing.  Shifting the H1
    # state by one bucket prevents an M15 bar from seeing its unfinished H1 bar.
    h1 = (
        market.resample("1h", label="left", closed="left")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
        )
        .dropna(subset=["open", "high", "low", "close"])
    )
    h1["EMA20"] = h1["close"].ewm(span=20, adjust=False, min_periods=20).mean()
    h1["EMA50"] = h1["close"].ewm(span=50, adjust=False, min_periods=50).mean()
    h1["EMA20_Slope"] = h1["EMA20"].diff()
    prior_h1_high = h1["high"].shift(1).rolling(5, min_periods=5).max()
    h1["Bull_Structure_Break"] = h1["close"] > prior_h1_high
    h1["Buy_Trend_Permission"] = (
        (h1["EMA20"] > h1["EMA50"])
        & (h1["EMA20_Slope"] > 0.0)
        & (h1["close"] > h1["EMA20"])
    )
    confirmed_h1 = h1[
        ["close", "EMA20", "EMA50", "EMA20_Slope", "Bull_Structure_Break", "Buy_Trend_Permission"]
    ].shift(1)
    confirmed_h1 = confirmed_h1.reindex(frame.index, method="ffill")
    frame["H1_Confirmed_Close"] = confirmed_h1["close"]
    frame["H1_EMA20"] = confirmed_h1["EMA20"]
    frame["H1_EMA50"] = confirmed_h1["EMA50"]
    frame["H1_EMA20_Slope"] = confirmed_h1["EMA20_Slope"]
    frame["H1_Bull_Structure_Break"] = confirmed_h1["Bull_Structure_Break"].eq(True)
    frame["H1_Buy_Trend_Permission"] = confirmed_h1["Buy_Trend_Permission"].eq(True)
    metadata = {
        "file": str(path.resolve()),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "source_rows": len(raw),
        "market_rows": len(market),
        "indicator_ready_rows": len(frame),
        "source_start_utc": source_start.isoformat(),
        "source_end_utc": source_end.isoformat(),
        "market_start_utc": market.index.min().isoformat(),
        "market_end_utc": market.index.max().isoformat(),
        "market_end_bangkok": market.index.max().tz_convert("Asia/Bangkok").isoformat(),
        "volume_available": bool((raw["volume"] > 0).any()),
    }
    return frame, metadata


def max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        worst = max(worst, (peak - value) / peak if peak else 0.0)
    return worst * 100.0


def run(
    frame: pd.DataFrame,
    initial_equity: float,
    risk_pct: float,
    min_rr: float,
    cost_points: float,
    allowed_directions: tuple[str, ...] = ("BUY", "SELL"),
    buy_policy: str = "LEGACY",
) -> tuple[list[dict], dict]:
    clock = SessionClock()
    gate = FinalGate(clock)
    all_engines = (("BUY", BuySignalEngine()), ("SELL", SellSignalEngine()))
    engines = tuple(
        (direction, engine)
        for direction, engine in all_engines
        if direction in allowed_directions
    )
    if not engines:
        raise ValueError("allowed_directions must contain BUY and/or SELL")
    equity = initial_equity
    curve = [equity]
    trades: list[dict] = []
    position: dict | None = None
    cooldown = 0
    diagnostics = {
        "candidate_signals": 0,
        "rejected_rr": 0,
        "dual_signal_bars": 0,
        "skipped_while_position_open": 0,
        "rejected_buy_policy": 0,
        "accepted_buy_trend": 0,
        "accepted_buy_reversal": 0,
    }

    for index in range(20, len(frame)):
        timestamp = frame.index[index]
        row = frame.iloc[index]
        if cooldown > 0:
            cooldown -= 1

        if position is not None:
            direction = position["direction"]
            active_stop = position["entry"] if position["be_armed"] else position["stop"]
            exit_price = None
            reason = None
            be_was_armed = position["be_armed"]
            if direction == "BUY":
                if finite(row["low"]) <= active_stop:
                    exit_price = active_stop
                    reason = "BREAK_EVEN" if be_was_armed else "INITIAL_STOP"
                elif finite(row["high"]) >= position["target"]:
                    exit_price = position["target"]
                    reason = "TP2"
                elif not be_was_armed and finite(row["high"]) >= position["be_trigger"]:
                    position["be_armed"] = True
                elif be_was_armed and bool(row["HA_Strong_Bear"]):
                    exit_price = finite(row["close"])
                    reason = "HA15_STRONG_BEAR"
            else:
                if finite(row["high"]) >= active_stop:
                    exit_price = active_stop
                    reason = "BREAK_EVEN" if be_was_armed else "INITIAL_STOP"
                elif finite(row["low"]) <= position["target"]:
                    exit_price = position["target"]
                    reason = "TP2"
                elif not be_was_armed and finite(row["low"]) <= position["be_trigger"]:
                    position["be_armed"] = True
                elif be_was_armed and bool(row["HA_Strong_Bull"]):
                    exit_price = finite(row["close"])
                    reason = "HA15_STRONG_BULL"

            held = index - position["entry_index"]
            if exit_price is None and held >= position["max_bars"]:
                exit_price = finite(row["close"])
                reason = "TIMEOUT"

            if exit_price is not None:
                signed_points = exit_price - position["entry"] if direction == "BUY" else position["entry"] - exit_price
                risk_points = abs(position["entry"] - position["stop"])
                risk_dollars = equity * risk_pct / 100.0
                lots = risk_dollars / (risk_points * POINT_VALUE)
                gross_pnl = signed_points * POINT_VALUE * lots
                costs = cost_points * POINT_VALUE * lots
                net_pnl = gross_pnl - costs
                equity += net_pnl
                trades.append(
                    {
                        "direction": direction,
                        "session": position["session"],
                        "entry_mode": position["entry_mode"],
                        "signal_time": position["signal_time"].isoformat(),
                        "entry_time": position["entry_time"].isoformat(),
                        "exit_time": timestamp.isoformat(),
                        "entry": position["entry"],
                        "initial_sl": position["stop"],
                        "be_trigger": position["be_trigger"],
                        "tp2": position["target"],
                        "exit": exit_price,
                        "exit_reason": reason,
                        "bars_held": held,
                        "risk_points": risk_points,
                        "gross_points": signed_points,
                        "net_points": signed_points - cost_points,
                        "gross_r": signed_points / risk_points,
                        "net_r": (signed_points - cost_points) / risk_points,
                        "lots": lots,
                        "gross_pnl": gross_pnl,
                        "costs": costs,
                        "net_pnl": net_pnl,
                        "equity_after": equity,
                    }
                )
                curve.append(equity)
                position = None
                cooldown = 3

        if position is not None:
            diagnostics["skipped_while_position_open"] += 1
            continue
        if cooldown > 0:
            continue

        state = clock.get(timestamp)
        candidates = []
        for direction, engine in engines:
            gate_result = gate.evaluate(state, direction, df=frame, idx=index)
            signal = engine.evaluate(frame, index, state, gate_result)
            if not signal:
                continue
            signal = dict(signal)
            signal["direction"] = str(signal.get("direction") or direction).upper()
            diagnostics["candidate_signals"] += 1
            if signal["direction"] == "BUY" and buy_policy != "LEGACY":
                trend_permission = bool(row.get("H1_Buy_Trend_Permission", False))
                reversal_permission = bool(
                    signal.get("deep_reclaim", False)
                    and row.get("H1_Bull_Structure_Break", False)
                )
                if buy_policy == "H1_TREND":
                    allowed_by_policy = trend_permission
                elif buy_policy == "H1_TREND_OR_DEEP_REVERSAL":
                    allowed_by_policy = trend_permission or reversal_permission
                else:
                    raise ValueError(f"Unknown buy_policy: {buy_policy}")
                if not allowed_by_policy:
                    diagnostics["rejected_buy_policy"] += 1
                    continue
                if reversal_permission and not trend_permission:
                    diagnostics["accepted_buy_reversal"] += 1
                    signal["entry_mode"] = "H1_DEEP_PRZ_REVERSAL_BUY"
                else:
                    diagnostics["accepted_buy_trend"] += 1
                    signal["entry_mode"] = "H1_EMA_TREND_BUY"
            if rr(signal) < min_rr:
                diagnostics["rejected_rr"] += 1
                continue
            candidates.append(signal)
        if len(candidates) > 1:
            diagnostics["dual_signal_bars"] += 1
        if not candidates:
            continue

        signal = max(candidates, key=rank)
        direction = signal["direction"]
        entry = finite(signal["entry"])
        stop = finite(signal["sl"])
        target = finite(signal["tp"])
        risk_points = abs(entry - stop)
        if risk_points <= 0 or rr(signal) < min_rr:
            continue
        be_trigger = finite(signal.get("be_trigger"))
        if direction == "BUY" and not (entry < be_trigger <= target):
            be_trigger = entry + risk_points
        if direction == "SELL" and not (target <= be_trigger < entry):
            be_trigger = entry - risk_points
        position = {
            "direction": direction,
            "session": str(signal.get("session", state.session)),
            "entry_mode": str(signal.get("entry_mode", "UNKNOWN")),
            "signal_time": timestamp,
            "entry_time": timestamp,
            "entry_index": index,
            "entry": entry,
            "stop": stop,
            "be_trigger": be_trigger,
            "target": target,
            "be_armed": False,
            "max_bars": max(1, int(finite(signal.get("max_bars"), 40))),
        }

    if position is not None:
        row = frame.iloc[-1]
        timestamp = frame.index[-1]
        direction = position["direction"]
        exit_price = finite(row["close"])
        signed_points = exit_price - position["entry"] if direction == "BUY" else position["entry"] - exit_price
        risk_points = abs(position["entry"] - position["stop"])
        risk_dollars = equity * risk_pct / 100.0
        lots = risk_dollars / (risk_points * POINT_VALUE)
        gross_pnl = signed_points * POINT_VALUE * lots
        costs = cost_points * POINT_VALUE * lots
        net_pnl = gross_pnl - costs
        equity += net_pnl
        trades.append(
            {
                "direction": direction,
                "session": position["session"],
                "entry_mode": position["entry_mode"],
                "signal_time": position["signal_time"].isoformat(),
                "entry_time": position["entry_time"].isoformat(),
                "exit_time": timestamp.isoformat(),
                "entry": position["entry"],
                "initial_sl": position["stop"],
                "be_trigger": position["be_trigger"],
                "tp2": position["target"],
                "exit": exit_price,
                "exit_reason": "END_OF_DATA",
                "bars_held": len(frame) - 1 - position["entry_index"],
                "risk_points": risk_points,
                "gross_points": signed_points,
                "net_points": signed_points - cost_points,
                "gross_r": signed_points / risk_points,
                "net_r": (signed_points - cost_points) / risk_points,
                "lots": lots,
                "gross_pnl": gross_pnl,
                "costs": costs,
                "net_pnl": net_pnl,
                "equity_after": equity,
            }
        )
        curve.append(equity)

    return trades, {"equity_curve": curve, "diagnostics": diagnostics}


def stats(trades: list[dict], initial: float, curve: list[float]) -> dict:
    wins = [trade for trade in trades if trade["net_pnl"] > 0]
    losses = [trade for trade in trades if trade["net_pnl"] < 0]
    profit = sum(trade["net_pnl"] for trade in wins)
    loss = abs(sum(trade["net_pnl"] for trade in losses))
    by_direction = {}
    for direction in ("BUY", "SELL"):
        selected = [trade for trade in trades if trade["direction"] == direction]
        selected_wins = [trade for trade in selected if trade["net_pnl"] > 0]
        by_direction[direction] = {
            "trades": len(selected),
            "win_rate_pct": len(selected_wins) / len(selected) * 100.0 if selected else 0.0,
            "net_pnl": sum(trade["net_pnl"] for trade in selected),
            "average_net_r": float(np.mean([trade["net_r"] for trade in selected])) if selected else 0.0,
        }
    reasons = {}
    for trade in trades:
        reasons[trade["exit_reason"]] = reasons.get(trade["exit_reason"], 0) + 1
    final = curve[-1]
    return {
        "trades": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": len(wins) / len(trades) * 100.0 if trades else 0.0,
        "profit_factor": profit / loss if loss else None,
        "average_net_r": float(np.mean([trade["net_r"] for trade in trades])) if trades else 0.0,
        "initial_equity": initial,
        "final_equity": final,
        "net_profit": final - initial,
        "return_pct": (final / initial - 1.0) * 100.0,
        "max_drawdown_pct": max_drawdown(curve),
        "total_costs": sum(trade["costs"] for trade in trades),
        "by_direction": by_direction,
        "exit_reasons": reasons,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--min-rr", type=float, default=1.5)
    parser.add_argument("--cost-points", type=float, default=0.50)
    parser.add_argument(
        "--direction",
        choices=("BOTH", "BUY", "SELL"),
        default="BOTH",
        help="Run both engines together or isolate one direction in its own portfolio.",
    )
    parser.add_argument(
        "--buy-policy",
        choices=("LEGACY", "H1_TREND", "H1_TREND_OR_DEEP_REVERSAL"),
        default="LEGACY",
        help="Optional confirmed-H1 permission applied only to BUY candidates.",
    )
    args = parser.parse_args()

    allowed_directions = (
        ("BUY", "SELL") if args.direction == "BOTH" else (args.direction,)
    )

    clock = SessionClock()
    frame, metadata = load_data(args.data, clock)
    trades, state = run(
        frame,
        initial_equity=args.initial_equity,
        risk_pct=args.risk_pct,
        min_rr=args.min_rr,
        cost_points=args.cost_points,
        allowed_directions=allowed_directions,
        buy_policy=args.buy_policy,
    )
    report = {
        "strategy": f"CURRENT_CORE_V4_{args.direction}_{args.buy_policy}_WITH_HA15_STRONG_EXIT",
        "portfolio_scope": {
            "direction": args.direction,
            "isolated": args.direction != "BOTH",
            "allowed_directions": list(allowed_directions),
            "buy_policy": args.buy_policy,
        },
        "requested_window": {"start": "2026-01-01", "end": "2026-07-11"},
        "actual_coverage": {
            "market_start_utc": metadata["market_start_utc"],
            "market_end_utc": metadata["market_end_utc"],
            "market_end_bangkok": metadata["market_end_bangkok"],
        },
        "coverage_status": "PARTIAL",
        "portfolio_assumptions": {
            "initial_equity": args.initial_equity,
            "risk_per_trade_pct": args.risk_pct,
            "minimum_rr": args.min_rr,
            "round_trip_cost_points": args.cost_points,
            "one_position_at_a_time": True,
            "strong_ha": "opposite M15 HA body >= 1.20x prior 20-bar average and body/range >= 60%",
            "ha_exit_only_after_be": True,
            "ambiguous_bar_order": "stop first, then TP, then BE arm",
            "h1_trend_buy": "confirmed H1 EMA20 > EMA50, EMA20 slope > 0, and H1 close > EMA20",
            "h1_deep_reversal_buy": "deep PRZ 1.00 reclaim trigger plus confirmed H1 break above the prior five H1 highs",
        },
        "performance": stats(trades, args.initial_equity, state["equity_curve"]),
        "diagnostics": state["diagnostics"],
        "data": metadata,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if args.direction == "BOTH" else f"_{args.direction.lower()}"
    if args.buy_policy != "LEGACY":
        suffix += f"_{args.buy_policy.lower()}"
    report_path = args.output_dir / f"core_ha15_strong{suffix}_report.json"
    trades_path = args.output_dir / f"core_ha15_strong{suffix}_trades.csv"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(trades).to_csv(trades_path, index=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT_JSON={report_path.resolve()}")
    print(f"TRADES_CSV={trades_path.resolve()}")


if __name__ == "__main__":
    main()
