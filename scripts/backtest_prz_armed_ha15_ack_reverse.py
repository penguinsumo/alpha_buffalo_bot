#!/usr/bin/env python3
"""Replay Core PRZ candidates with the PRZ-armed HA15 ACK-reverse lifecycle.

This is a local-data proxy for the Pine v2.4 workflow. Core BUY/SELL engines
provide the PRZ/sweep/BB/PA candidate that arms a direction. Entry or reversal
then requires a later pair of confirmed M15 Heikin-Ashi candles whose second
close clears the full body of the first. A reversal closes the current side
before opening the opposite side at the same confirmed M15 close, modelling a
successful CLOSE ACK with no additional network or broker latency.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backtest_core_ha15_strong import POINT_VALUE, finite, load_data, rank, rr, stats
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.final_gate import FinalGate
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock


def prepare_patterns(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.copy()
    previous_bear = result["HA_Bearish"].shift(1).eq(True)
    previous_bull = result["HA_Bullish"].shift(1).eq(True)
    prior_body_low = pd.concat(
        [result["HA_Open"].shift(1), result["HA_Close"].shift(1)], axis=1
    ).min(axis=1)
    prior_body_high = pd.concat(
        [result["HA_Open"].shift(1), result["HA_Close"].shift(1)], axis=1
    ).max(axis=1)
    result["HA15_Two_Bear_Lower"] = (
        result["HA_Bearish"] & previous_bear & (result["HA_Close"] < prior_body_low)
    )
    result["HA15_Two_Bull_Higher"] = (
        result["HA_Bullish"] & previous_bull & (result["HA_Close"] > prior_body_high)
    )
    return result


def executable_plan(
    signal: dict, direction: str, entry: float, min_rr: float
) -> dict | None:
    """Reprice the armed target as Pine does at the confirmation candle."""
    stop = finite(signal.get("sl"))
    target = finite(signal.get("tp"))
    if direction == "BUY" and not stop < entry:
        return None
    if direction == "SELL" and not entry < stop:
        return None
    risk = abs(entry - stop)
    if risk <= 0:
        return None
    repriced = dict(signal)
    if direction == "BUY" and target < entry + risk * min_rr:
        repriced["tp"] = entry + risk * 3.0
    elif direction == "SELL" and target > entry - risk * min_rr:
        repriced["tp"] = entry - risk * 3.0
    return repriced


def open_position(signal: dict, direction: str, entry: float, index: int, timestamp) -> dict:
    stop = finite(signal["sl"])
    target = finite(signal["tp"])
    risk = abs(entry - stop)
    return {
        "direction": direction,
        "session": str(signal.get("session", "UNKNOWN")),
        "entry_mode": "PRZ_ARMED_HA15_TWO_BODY",
        "signal_time": signal["armed_at"],
        "entry_time": timestamp,
        "entry_index": index,
        "entry": entry,
        "stop": stop,
        "be_trigger": entry + risk if direction == "BUY" else entry - risk,
        "target": target,
        "be_armed": False,
        "max_bars": max(1, int(finite(signal.get("max_bars"), 40))),
    }


def close_position(
    position: dict,
    *,
    timestamp,
    exit_price: float,
    reason: str,
    equity: float,
    risk_pct: float,
    cost_points: float,
    index: int,
) -> tuple[dict, float]:
    direction = position["direction"]
    signed_points = (
        exit_price - position["entry"]
        if direction == "BUY"
        else position["entry"] - exit_price
    )
    risk_points = abs(position["entry"] - position["stop"])
    risk_dollars = equity * risk_pct / 100.0
    lots = risk_dollars / (risk_points * POINT_VALUE)
    gross_pnl = signed_points * POINT_VALUE * lots
    costs = cost_points * POINT_VALUE * lots
    net_pnl = gross_pnl - costs
    new_equity = equity + net_pnl
    return (
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
            "bars_held": index - position["entry_index"],
            "risk_points": risk_points,
            "gross_points": signed_points,
            "net_points": signed_points - cost_points,
            "gross_r": signed_points / risk_points,
            "net_r": (signed_points - cost_points) / risk_points,
            "lots": lots,
            "gross_pnl": gross_pnl,
            "costs": costs,
            "net_pnl": net_pnl,
            "equity_after": new_equity,
        },
        new_equity,
    )


def run(
    frame: pd.DataFrame,
    *,
    initial_equity: float,
    risk_pct: float,
    min_rr: float,
    cost_points: float,
    arm_ttl: int,
) -> tuple[list[dict], dict]:
    clock = SessionClock()
    gate = FinalGate(clock)
    engines = {"BUY": BuySignalEngine(), "SELL": SellSignalEngine()}
    arms: dict[str, dict | None] = {"BUY": None, "SELL": None}
    equity = initial_equity
    curve = [equity]
    trades: list[dict] = []
    position: dict | None = None
    cooldown = 0
    diagnostics = {
        "candidate_signals": 0,
        "buy_arms": 0,
        "sell_arms": 0,
        "expired_arms": 0,
        "buy_confirmations": 0,
        "sell_confirmations": 0,
        "invalid_confirmation_levels": 0,
        "ack_reversals": 0,
    }

    for index in range(20, len(frame)):
        timestamp = frame.index[index]
        row = frame.iloc[index]
        if cooldown > 0:
            cooldown -= 1

        for direction in ("BUY", "SELL"):
            armed = arms[direction]
            if armed is not None:
                armed["age"] += 1
                if armed["age"] > arm_ttl:
                    arms[direction] = None
                    diagnostics["expired_arms"] += 1

        confirmed = None
        if arms["BUY"] is not None and bool(row["HA15_Two_Bull_Higher"]):
            confirmed = "BUY"
        if arms["SELL"] is not None and bool(row["HA15_Two_Bear_Lower"]):
            confirmed = "SELL" if confirmed is None else None
        if confirmed:
            diagnostics[f"{confirmed.lower()}_confirmations"] += 1

        closed_this_bar = False
        if position is not None:
            direction = position["direction"]
            active_stop = position["entry"] if position["be_armed"] else position["stop"]
            exit_price = None
            reason = None
            if direction == "BUY":
                if finite(row["low"]) <= active_stop:
                    exit_price = active_stop
                    reason = "BREAK_EVEN" if position["be_armed"] else "INITIAL_STOP"
                elif finite(row["high"]) >= position["target"]:
                    exit_price = position["target"]
                    reason = "TP2"
                else:
                    if not position["be_armed"] and finite(row["high"]) >= position["be_trigger"]:
                        position["be_armed"] = True
                    if confirmed == "SELL":
                        exit_price = finite(row["close"])
                        reason = "HA15_TWO_BEAR_LOWER_ACK_REVERSE"
            else:
                if finite(row["high"]) >= active_stop:
                    exit_price = active_stop
                    reason = "BREAK_EVEN" if position["be_armed"] else "INITIAL_STOP"
                elif finite(row["low"]) <= position["target"]:
                    exit_price = position["target"]
                    reason = "TP2"
                else:
                    if not position["be_armed"] and finite(row["low"]) <= position["be_trigger"]:
                        position["be_armed"] = True
                    if confirmed == "BUY":
                        exit_price = finite(row["close"])
                        reason = "HA15_TWO_BULL_HIGHER_ACK_REVERSE"

            if exit_price is None and index - position["entry_index"] >= position["max_bars"]:
                exit_price = finite(row["close"])
                reason = "TIMEOUT"

            if exit_price is not None:
                trade, equity = close_position(
                    position,
                    timestamp=timestamp,
                    exit_price=exit_price,
                    reason=str(reason),
                    equity=equity,
                    risk_pct=risk_pct,
                    cost_points=cost_points,
                    index=index,
                )
                trades.append(trade)
                curve.append(equity)
                was_reverse = str(reason).endswith("ACK_REVERSE")
                position = None
                closed_this_bar = True
                if was_reverse and confirmed:
                    signal = arms[confirmed]
                    plan = (
                        executable_plan(signal, confirmed, exit_price, min_rr)
                        if signal is not None
                        else None
                    )
                    if plan is not None:
                        position = open_position(plan, confirmed, exit_price, index, timestamp)
                        arms[confirmed] = None
                        arms["SELL" if confirmed == "BUY" else "BUY"] = None
                        diagnostics["ack_reversals"] += 1
                    else:
                        diagnostics["invalid_confirmation_levels"] += 1
                        cooldown = 3
                else:
                    cooldown = 3

        if (
            position is None
            and not closed_this_bar
            and cooldown == 0
            and confirmed is not None
        ):
            signal = arms[confirmed]
            entry = finite(row["close"])
            plan = (
                executable_plan(signal, confirmed, entry, min_rr)
                if signal is not None
                else None
            )
            if plan is not None:
                position = open_position(plan, confirmed, entry, index, timestamp)
                arms[confirmed] = None
                arms["SELL" if confirmed == "BUY" else "BUY"] = None
            else:
                diagnostics["invalid_confirmation_levels"] += 1

        state = clock.get(timestamp)
        for direction, engine in engines.items():
            if position is not None and position["direction"] == direction:
                continue
            gate_result = gate.evaluate(state, direction, df=frame, idx=index)
            signal = engine.evaluate(frame, index, state, gate_result)
            if not signal:
                continue
            signal = dict(signal)
            signal["direction"] = str(signal.get("direction") or direction).upper()
            diagnostics["candidate_signals"] += 1
            if rr(signal) < min_rr:
                continue
            signal["age"] = 0
            signal["armed_at"] = timestamp
            current = arms[direction]
            if current is None or rank(signal) >= rank(current):
                arms[direction] = signal
            diagnostics[f"{direction.lower()}_arms"] += 1

    if position is not None:
        timestamp = frame.index[-1]
        trade, equity = close_position(
            position,
            timestamp=timestamp,
            exit_price=finite(frame.iloc[-1]["close"]),
            reason="END_OF_DATA",
            equity=equity,
            risk_pct=risk_pct,
            cost_points=cost_points,
            index=len(frame) - 1,
        )
        trades.append(trade)
        curve.append(equity)

    return trades, {"equity_curve": curve, "diagnostics": diagnostics}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--initial-equity", type=float, default=10_000.0)
    parser.add_argument("--risk-pct", type=float, default=1.0)
    parser.add_argument("--min-rr", type=float, default=1.5)
    parser.add_argument("--cost-points", type=float, default=0.50)
    parser.add_argument("--arm-ttl", type=int, default=8)
    args = parser.parse_args()

    frame, metadata = load_data(args.data, SessionClock())
    frame = prepare_patterns(frame)
    trades, state = run(
        frame,
        initial_equity=args.initial_equity,
        risk_pct=args.risk_pct,
        min_rr=args.min_rr,
        cost_points=args.cost_points,
        arm_ttl=args.arm_ttl,
    )
    report = {
        "strategy": "CORE_PRZ_ARMED_HA15_TWO_BODY_ACK_REVERSE_PROXY",
        "requested_window": {"start": "2026-01-01", "end": "2026-07-11"},
        "actual_coverage": {
            "market_start_utc": metadata["market_start_utc"],
            "market_end_utc": metadata["market_end_utc"],
            "market_end_bangkok": metadata["market_end_bangkok"],
        },
        "coverage_status": "PARTIAL",
        "assumptions": {
            "initial_equity": args.initial_equity,
            "risk_per_trade_pct": args.risk_pct,
            "minimum_rr_at_confirmation": args.min_rr,
            "round_trip_cost_points": args.cost_points,
            "prz_arm_ttl_m15_bars": args.arm_ttl,
            "ha_confirmation": "two confirmed M15 HA candles; second close clears the full first body",
            "reverse_execution": "close current side, successful ACK, then open opposite at the same M15 close",
            "ack_latency": "zero additional bars; optimistic infrastructure assumption",
            "minute_10": "not used because it would violate the confirmed/non-repaint M15 contract",
        },
        "performance": stats(trades, args.initial_equity, state["equity_curve"]),
        "diagnostics": state["diagnostics"],
        "data": metadata,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "prz_armed_ha15_ack_reverse_report.json"
    trades_path = args.output_dir / "prz_armed_ha15_ack_reverse_trades.csv"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    pd.DataFrame(trades).to_csv(trades_path, index=False)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"REPORT_JSON={report_path.resolve()}")
    print(f"TRADES_CSV={trades_path.resolve()}")


if __name__ == "__main__":
    main()
