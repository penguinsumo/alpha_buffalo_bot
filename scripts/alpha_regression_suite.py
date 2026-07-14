#!/usr/bin/env python3
"""Offline regression suite for Alpha Buffalo v12-core.

This suite protects the final project contract from drift while older branch
ideas are mined back into production.
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alpha_buffalo_signal as runtime
import early_warning as warning_runtime
import telegram_bot as telegram_bot_runtime
import telegram_guard as telegram_guard_runtime
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.final_gate import FinalGate
from engine_v4.harmonic_bias_gate import evaluate_harmonic_bias
from engine_v4.indicators import (
    _apply_deep_sweep_reclaim_state,
    _apply_zone_pinbar_break_state,
    _asia_session_mask,
    add_indicators,
)
from engine_v4.router import SignalRouter
from engine_v4.sell_engine import SellSignalEngine
from engine_v4.session_gate import GateResult
from execution_lifecycle import ExecutionLifecycleManager, closed_ha5_evidence
from scenario_scanner import (
    build_confirmed_parallel_channel,
    detect_confirmed_tunnel_sweep,
)
from session_clock import SessionClock, SessionState, market_closed_reason
from alpha_buffalo_signal import (
    API_LICENSE_KEY,
    _set_latest_signal,
    build_api_signal_response,
    build_ea_payload,
    format_telegram_signal,
    format_telegram_trend_update,
    signal_latest,
)
from signal_schema import BLOCKED, ERROR, NO_SIGNAL, SIGNAL, create_signal


NY_SESSION = SessionState(
    session="NY",
    liquidity="NORMAL",
    bkk_hour=22,
    utc_hour=15,
    timestamp="2026-07-10T15:00:00+00:00",
)
ALLOWED = GateResult(True, "test")


def assert_true(value, message: str) -> None:
    if not value:
        raise AssertionError(message)


def assert_equal(actual, expected, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r} actual={actual!r}")


def base_row() -> dict:
    return {
        "open": 100.5,
        "high": 102.0,
        "low": 98.0,
        "close": 100.0,
        "ATR14": 1.0,
        "BB_Lower": 90.0,
        "BB_Mid": 105.0,
        "BB_Upper": 110.0,
        "EMA20": 120.0,
        "EMA50": 100.0,
        "Trend_1H_Up": True,
        "VSA_Buy_Wins": False,
        "VSA_Sell_Wins": True,
        "VSA_Buy_Pressure": 0.2,
        "VSA_Sell_Pressure": 0.8,
        "Pine_PA_Bull_Confirmed": False,
        "Pine_PA_Bear_Confirmed": True,
        "HA_Bearish": True,
        "HA_Bullish": False,
        "HA_Green_2_CF": False,
        "V4_Block_Sell_At_Lower": False,
        "V4_Block_Buy_At_Upper": False,
        "V4_Buy_Setup": False,
        "V4_Sell_Setup": True,
        "Pine_Valid_Sell": False,
        "Pine_Valid_Buy": False,
        "BB_PRZ_Resistance_Confluence": True,
        "BB_PRZ_Support_Confluence": False,
        "V4_Sell_Entry_Zone": True,
        "V4_Buy_Entry_Zone": False,
        "Micro_Lot0_High": 102.0,
        "Micro_Lot0_Low": 98.0,
        "Pine_PRZ_Resistance_High": 102.0,
        "Pine_PRZ_Resistance_Low": 99.0,
        "Pine_PRZ_Support_High": 101.0,
        "Pine_PRZ_Support_Low": 98.0,
        "Fib_072": 0.0,
        "CHoCH_Bear": False,
        "CHoCH_Bull": False,
        "Bear_OB": False,
        "Bull_OB": False,
        "Sweep_Above_100": False,
        "Sell_Reclaim": False,
        "Micro_BOS_Down": False,
    }


def frame(rows: list[dict]) -> pd.DataFrame:
    idx = pd.date_range("2026-07-10 15:00", periods=len(rows), freq="15min", tz="UTC")
    return pd.DataFrame(rows, index=idx)


def test_upper_sell_not_blocked_by_bullish_context() -> None:
    row = base_row()
    df = frame([row])
    sig = SellSignalEngine().evaluate(df, 0, NY_SESSION, ALLOWED)

    assert_true(sig is not None, "upper-zone V4 SELL must not be blocked by H1/EMA bullish context")
    assert_equal(sig["direction"], "SELL", "upper-zone direction")
    assert_equal(sig["status"], SIGNAL, "SELL engine must emit canonical status")
    assert_equal(sig["tp2_price"], sig["tp"], "SELL tp2 alias")
    assert_true(sig["entry_mode"].startswith("V4_SELL"), "upper-zone SELL must stay V4")
    assert_true(sig["rr_ok"], "fixture should be executable RR")


def test_lower_buy_not_blocked_by_bearish_context() -> None:
    row = base_row()
    row.update(
        {
            "EMA20": 80.0,
            "EMA50": 100.0,
            "Trend_1H_Up": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "HA_Bullish": True,
            "HA_Bearish": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Block_Buy_At_Upper": False,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)

    assert_true(sig is not None, "lower-zone V4 BUY must not be blocked by H1/EMA bearish context")
    assert_equal(sig["direction"], "BUY", "lower-zone direction")
    assert_equal(sig["status"], SIGNAL, "BUY engine must emit canonical status")
    assert_equal(sig["tp1_price"], sig["tp1"], "BUY tp1 alias")
    assert_true(sig["entry_mode"].startswith("V4_BUY"), "lower-zone BUY must stay V4")
    assert_true(sig["rr_ok"], "fixture should be executable RR")


def test_lower_zone_blocks_fresh_sell() -> None:
    row = base_row()
    row.update(
        {
            "V4_Block_Sell_At_Lower": True,
            "V4_Sell_Setup": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Buy_Entry_Zone": True,
            "BB_PRZ_Resistance_Confluence": False,
            "BB_PRZ_Support_Confluence": True,
        }
    )
    sig = SellSignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_equal(sig, None, "lower-zone bullish setup must block fresh SELL")


def test_upper_zone_blocks_fresh_buy() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Block_Buy_At_Upper": True,
            "V4_Buy_Entry_Zone": True,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_equal(sig, None, "upper-zone bearish setup must block fresh BUY")


def test_harmonic_d_prz_is_one_direction_only() -> None:
    context = {
        "found": True,
        "pattern": "Bullish_Symmetric_XABCD",
        "direction": "BUY",
        "state": "ARMED",
        "source": "market_close_map",
        "tunnel_state": "DOWNTREND",
    }
    buy = evaluate_harmonic_bias("BUY", context, require_harmonic=True)
    sell = evaluate_harmonic_bias("SELL", context, require_harmonic=True)

    assert_true(buy.allowed, "bullish harmonic D must license only BUY setup evaluation")
    assert_true(not sell.allowed, "bullish harmonic D must hard-block fresh SELL")
    assert_equal(sell.reason, "HARMONIC_BIAS_BUY_ONLY", "opposite harmonic reason")
    assert_equal(
        buy.tunnel_alignment,
        "C_TO_D_APPROACH_ALIGNED",
        "falling parallel tunnel is the normal approach into bullish D",
    )


def test_confirmed_tunnel_sweep_arms_only_the_aligned_approach() -> None:
    sell = detect_confirmed_tunnel_sweep(
        high=100.10,
        low=98.50,
        close=99.20,
        upper=100.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="DOWNTREND",
    )
    assert_true(sell["SELL"], "upper-tunnel wick and reclaim must arm SELL")
    assert_true(not sell["BUY"], "falling tunnel must not arm BUY at its upper edge")

    no_reclaim = detect_confirmed_tunnel_sweep(
        high=100.50,
        low=99.00,
        close=100.20,
        upper=100.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="DOWNTREND",
    )
    assert_true(not no_reclaim["SELL"], "wick without close back below tunnel is not a sweep/reclaim")

    buy = detect_confirmed_tunnel_sweep(
        high=101.00,
        low=94.90,
        close=95.40,
        upper=105.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="UPTREND",
    )
    assert_true(buy["BUY"], "lower-tunnel wick and reclaim must mirror BUY")
    assert_true(not buy["SELL"], "rising tunnel must not arm SELL at its lower edge")


def test_parallel_channel_uses_confirmed_h1_pivots_and_ignores_forming_wick() -> None:
    index = pd.date_range("2026-07-01", periods=30, freq="h", tz="UTC")
    turning_points = {
        0: 105.0,
        5: 120.0,
        9: 90.0,
        13: 110.0,
        17: 80.0,
        21: 100.0,
        25: 70.0,
        29: 85.0,
    }
    center = [0.0] * len(index)
    points = sorted(turning_points)
    for left, right in zip(points, points[1:]):
        start, end = turning_points[left], turning_points[right]
        for position in range(left, right + 1):
            weight = (position - left) / (right - left)
            center[position] = start + (end - start) * weight
    frame = pd.DataFrame(
        {
            "open": center,
            "high": [value + 1.0 for value in center],
            "low": [value - 1.0 for value in center],
            "close": center,
            "volume": [1000.0] * len(index),
        },
        index=index,
    )
    baseline = build_confirmed_parallel_channel(
        frame,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )
    frame_with_news_wick = frame.copy()
    frame_with_news_wick.loc[index[-1], "high"] = 180.0
    after_wick = build_confirmed_parallel_channel(
        frame_with_news_wick,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )

    assert_true(baseline["valid"], "two lower highs/lows must form a valid channel")
    assert_equal(baseline["state"], "DOWNTREND", "falling H1 channel state")
    assert_equal(
        after_wick["anchor_version"],
        baseline["anchor_version"],
        "forming news wick must not repaint confirmed anchors",
    )
    assert_equal(after_wick["upper"], baseline["upper"], "upper channel remains frozen")
    assert_equal(after_wick["lower"], baseline["lower"], "lower channel remains parallel")

    api_frame = frame.reset_index(names="datetime")
    api_channel = build_confirmed_parallel_channel(
        api_frame,
        pivot_bars=3,
        projection_time=api_frame["datetime"].iloc[-1],
        minimum_width=1.0,
    )
    assert_equal(api_channel["upper"], baseline["upper"], "API datetime column projection")
    assert_equal(api_channel["lower"], baseline["lower"], "API channel matches Pine time axis")

    mirrored = frame.copy()
    mirrored["open"] = 250.0 - frame["open"]
    mirrored["close"] = 250.0 - frame["close"]
    mirrored["high"] = 250.0 - frame["low"]
    mirrored["low"] = 250.0 - frame["high"]
    rising = build_confirmed_parallel_channel(
        mirrored,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )
    assert_true(rising["valid"], "mirrored higher highs/lows must form a channel")
    assert_equal(rising["state"], "UPTREND", "rising H1 channel state")


def test_final_gate_combines_hours_risk_and_harmonic_bias() -> None:
    gate = FinalGate(SessionClock())
    context = {
        "found": True,
        "pattern": "Bullish_Bat",
        "direction": "BUY",
        "state": "ACTIVE",
        "source": "market_close_map",
        "tunnel_state": "FLAT",
    }
    allowed = gate.evaluate(
        NY_SESSION,
        "BUY",
        harmonic_context=context,
        require_harmonic=True,
    )
    blocked = gate.evaluate(
        NY_SESSION,
        "SELL",
        harmonic_context=context,
        require_harmonic=True,
    )
    waiting = gate.evaluate(
        NY_SESSION,
        "BUY",
        harmonic_context={**context, "state": "WAIT_LOCATION"},
        require_harmonic=True,
    )

    assert_true(allowed.allowed, "NY BUY + bullish D must pass the single entry gate")
    assert_true(not blocked.allowed, "SELL must not bypass bullish harmonic bias")
    assert_equal(blocked.reason, "HARMONIC_BIAS_BUY_ONLY", "hard bias reason")
    assert_true(not waiting.allowed, "pattern far from D must not create an entry")
    assert_equal(waiting.reason, "WAIT_HARMONIC_D_PRZ", "wait for D location")


def test_low_rr_candidate_waits_in_ea_payload() -> None:
    row = base_row()
    row.update(
        {
            "high": 101.0,
            "BB_Upper": 101.0,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_true(sig is not None, "low RR V4 setup should remain visible as candidate")
    assert_true(not sig["rr_ok"], "low RR candidate must be rr_ok=false")

    payload_signal = {
        "status": SIGNAL,
        "direction": "BUY",
        "decision": {"action": "BUY", "confidence": 0.7, "score": 6, "grade": "VALID_TRADE"},
        "timestamp": "2026-07-10T15:00:00+00:00",
        "entry_price": sig["entry_price"],
        "sl_price": sig["sl_price"],
        "tp1_price": sig["tp1_price"],
        "tp2_price": sig["tp2_price"],
        "entry": sig["entry"],
        "sl": sig["sl"],
        "tp_final": sig["tp"],
        "entry_mode": sig["entry_mode"],
        "setup_state": sig["setup_state"],
        "scenario_state": sig["setup_state"],
        "engine_v4": sig,
        "gates": {"blueprint_valid": True, "session": "NY"},
        "blueprint": {"is_valid": True, "current_price": sig["entry"]},
    }
    ea = build_ea_payload("XAUUSD", payload_signal)
    assert_equal(ea["action"], "WAIT", "RR below minimum must keep EA waiting")
    assert_true(not ea["rr_ok"], "EA rr_ok must stay false")
    assert_true(ea["entry_mode"].startswith("V4_BUY"), "EA should keep V4 entry mode for diagnostics")
    response = build_api_signal_response("XAUUSD", payload_signal, ea)
    assert_equal(response["status"], BLOCKED, "low-RR candidate must be BLOCKED at API")
    assert_equal(response["direction"], "BUY", "blocked candidate keeps market direction")


def _runtime_signal(engine_signal: dict) -> dict:
    direction = engine_signal["direction"]
    return {
        "status": SIGNAL,
        "direction": direction,
        "decision": {
            "action": direction,
            "confidence": 0.8,
            "score": engine_signal["score"],
            "grade": "VALID_TRADE",
            "reason": engine_signal["reason"],
        },
        "timestamp": "2026-07-10T15:00:00+00:00",
        "entry_price": engine_signal["entry_price"],
        "sl_price": engine_signal["sl_price"],
        "tp1_price": engine_signal["tp1_price"],
        "tp2_price": engine_signal["tp2_price"],
        "entry": engine_signal["entry"],
        "sl": engine_signal["sl"],
        "tp_final": engine_signal["tp"],
        "entry_mode": engine_signal["entry_mode"],
        "setup_state": engine_signal["setup_state"],
        "scenario_state": engine_signal["setup_state"],
        "engine_v4": engine_signal,
        "gates": {"blueprint_valid": True, "session": "NY"},
        "blueprint": {"is_valid": True, "current_price": engine_signal["entry"]},
    }


def test_buy_and_sell_share_one_api_schema() -> None:
    buy_row = base_row()
    buy_row.update(
        {
            "EMA20": 80.0,
            "EMA50": 100.0,
            "Trend_1H_Up": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
        }
    )
    candidates = [
        BuySignalEngine().evaluate(frame([buy_row]), 0, NY_SESSION, ALLOWED),
        SellSignalEngine().evaluate(frame([base_row()]), 0, NY_SESSION, ALLOWED),
    ]

    schemas = []
    for candidate in candidates:
        assert_true(candidate is not None, "fixture must create candidate")
        runtime_signal = _runtime_signal(candidate)
        ea = build_ea_payload("XAUUSD", runtime_signal)
        response = build_api_signal_response("XAUUSD", runtime_signal, ea)
        assert_equal(response["status"], SIGNAL, f"{candidate['direction']} should be executable")
        assert_equal(response["direction"], candidate["direction"], "API direction")
        schemas.append(set(response.keys()))

    assert_equal(schemas[0], schemas[1], "BUY and SELL must have identical API keys")


def test_signal_latest_preserves_canonical_contract() -> None:
    row = base_row()
    candidate = SellSignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "SELL endpoint fixture must create candidate")
    runtime_signal = _runtime_signal(candidate)
    ea = build_ea_payload("XAUUSD", runtime_signal)
    expected = build_api_signal_response("XAUUSD", runtime_signal, ea)
    original_clock_get = runtime.SessionClock.get

    try:
        runtime.SessionClock.get = lambda self, dt=None: NY_SESSION
        _set_latest_signal(expected)
        served = signal_latest(key=API_LICENSE_KEY, symbol="XAU/USD")
    finally:
        runtime.SessionClock.get = original_clock_get
        _set_latest_signal({})

    assert_equal(served["status"], SIGNAL, "endpoint status")
    assert_equal(served["direction"], "SELL", "endpoint direction")
    assert_equal(set(served.keys()), set(expected.keys()), "endpoint schema must stay canonical")


def test_no_signal_has_no_direction_and_ea_waits() -> None:
    signal = {
        "status": NO_SIGNAL,
        "direction": None,
        "decision": {"action": "BUY", "score": 9, "reason": "legacy fallback"},
        "gates": {"blueprint_valid": True, "session": "NY"},
        "blueprint": {"is_valid": True, "current_price": 100.0},
        "entry_price": 100.0,
        "sl_price": 98.0,
        "tp2_price": 110.0,
    }
    ea = build_ea_payload("XAUUSD", signal)
    response = build_api_signal_response("XAUUSD", signal, ea)

    assert_equal(ea["action"], "WAIT", "EA must ignore legacy direction without SIGNAL status")
    assert_equal(response["status"], NO_SIGNAL, "API no-signal status")
    assert_equal(response["direction"], None, "NO_SIGNAL must not claim BUY or SELL")


def test_directional_price_validator_blocks_invalid_buy() -> None:
    result = create_signal(
        status=SIGNAL,
        direction="BUY",
        entry_price=100.0,
        sl_price=101.0,
        tp1_price=105.0,
        tp2_price=110.0,
        score=8,
        reason="bad levels",
    )
    assert_equal(result["status"], BLOCKED, "invalid BUY levels must be blocked")
    assert_equal(result["direction"], "BUY", "validator keeps direction for diagnostics")
    assert_true("INVALID_BUY_LEVELS" in result["reason"], "validator reason")


def test_error_uses_same_schema_and_never_executes() -> None:
    signal = {
        "status": ERROR,
        "direction": None,
        "reason": "DATA_FETCH_ERROR",
        "decision": {"action": "NONE", "score": 0, "reason": "DATA_FETCH_ERROR"},
        "gates": {"blueprint_valid": False, "session": ""},
        "blueprint": {"is_valid": False},
    }
    ea = build_ea_payload("XAUUSD", signal)
    response = build_api_signal_response("XAUUSD", signal, ea)

    assert_equal(ea["action"], "WAIT", "ERROR must never execute")
    assert_equal(response["status"], ERROR, "API error status")
    assert_equal(response["direction"], None, "ERROR must not claim a direction")
    assert_equal(response["reason"], "DATA_FETCH_ERROR", "error reason must be preserved")


def test_choch_promotes_to_v5_journey() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "CHoCH_Bull": True,
            "Pine_Valid_Buy": True,
        }
    )
    rows = [base_row() for _ in range(5)] + [row]
    routed = SignalRouter(
        clock=SessionClock(),
        gate=FinalGate(SessionClock()),
        buy_engine=BuySignalEngine(),
        sell_engine=SellSignalEngine(),
    ).process(frame(rows))

    assert_true(routed, "router should select CHoCH BUY candidate")
    sig = routed[0]
    assert_equal(sig["direction"], "BUY", "CHoCH fixture direction")
    assert_equal(sig["journey_state"], "V5_BUY_JOURNEY", "CHoCH must promote to V5 journey")
    assert_true(sig["bos_confirmed"], "CHoCH promotion must mark BOS confirmed")


def test_no_choch_stays_v4_range() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "CHoCH_Bull": False,
            "Pine_Valid_Buy": False,
        }
    )
    routed = SignalRouter(
        clock=SessionClock(),
        gate=FinalGate(SessionClock()),
        buy_engine=BuySignalEngine(),
        sell_engine=SellSignalEngine(),
    ).process(frame([base_row() for _ in range(5)] + [row]))

    assert_true(routed, "router should select non-CHoCH BUY candidate")
    assert_equal(routed[0]["journey_state"], "V4_SCALP_RANGE", "no CHoCH must stay V4 range")
    assert_true(not routed[0]["bos_confirmed"], "no CHoCH must not mark BOS confirmed")


def test_session_kivanc_mask_uses_bangkok_asia_hours() -> None:
    index = pd.DatetimeIndex([
        pd.Timestamp("2026-07-10T00:00:00Z"),  # 07:00 BKK
        pd.Timestamp("2026-07-10T08:00:00Z"),  # 15:00 BKK
    ])
    mask = _asia_session_mask(index)
    assert_true(bool(mask.iloc[0]), "07:00 BKK must use ASIA 0.618-0.786 map")
    assert_true(not bool(mask.iloc[1]), "15:00 BKK must use deep 0.720-0.886 map")


def test_indicators_do_not_read_future_daily_or_h1_bars() -> None:
    index = pd.date_range("2026-06-01", periods=420, freq="15min", tz="UTC")
    base = pd.DataFrame(index=index)
    base["open"] = [100.0 + (i % 40) * 0.1 for i in range(len(index))]
    base["close"] = base["open"] + 0.05
    base["high"] = base[["open", "close"]].max(axis=1) + 0.3
    base["low"] = base[["open", "close"]].min(axis=1) - 0.3
    base["volume"] = 100.0
    changed = base.copy()
    changed.loc[index[360]:, "high"] += 500.0
    changed.loc[index[360]:, "low"] -= 500.0

    original_indicators = add_indicators(base)
    changed_indicators = add_indicators(changed)
    for column in ("Swing_H", "Swing_L", "Fib_072", "Fib_0886", "Trend_1H_Up"):
        left = original_indicators.loc[:index[359], column]
        right = changed_indicators.loc[:index[359], column]
        assert_true(left.equals(right), f"future candles changed historical {column}")


def _deep_state_row() -> dict:
    return {
        "open": 101.0,
        "high": 102.0,
        "low": 99.0,
        "close": 101.0,
        "ATR14": 1.0,
        "Fib_0886": 102.28,
        "Fib_072": 105.6,
        "Fib_R_072": 114.4,
        "Fib_R_0886": 117.72,
        "HA_Bullish": False,
        "HA_Bearish": False,
        "VSA_Buy_Wins": False,
        "VSA_Sell_Wins": False,
        "Deep_Buy_Wall_Candidate": False,
        "Deep_Sell_Wall_Candidate": False,
    }


def test_deep_buy_requires_wall_then_reclaim() -> None:
    wall = _deep_state_row()
    wall.update({"Deep_Buy_Wall_Candidate": True, "VSA_Buy_Wins": True})
    wait = _deep_state_row()
    wait.update({"low": 100.5, "high": 102.1, "close": 101.8})
    reclaim = _deep_state_row()
    reclaim.update({"low": 101.5, "high": 103.2, "close": 103.0, "HA_Bullish": True})

    result = _apply_deep_sweep_reclaim_state(frame([wall, wait, reclaim]))
    assert_true(not bool(result["Deep_Buy_Reclaim_Trigger"].iloc[0]), "1.00 wall candle is SETUP, not entry")
    assert_true(not bool(result["Deep_Buy_Reclaim_Trigger"].iloc[1]), "price below 0.886 must keep waiting")
    assert_true(bool(result["Deep_Buy_Reclaim_Trigger"].iloc[2]), "break of wall high inside 0.886-0.720 must trigger BUY")
    assert_equal(float(result["Deep_Buy_Wall_Low"].iloc[2]), 99.0, "BUY wall must preserve sweep wick low")


def test_deep_sell_requires_wall_then_reclaim() -> None:
    wall = _deep_state_row()
    wall.update(
        {
            "open": 119.0,
            "high": 121.0,
            "low": 118.0,
            "close": 119.0,
            "Deep_Sell_Wall_Candidate": True,
            "VSA_Sell_Wins": True,
        }
    )
    wait = _deep_state_row()
    wait.update({"open": 118.8, "high": 119.2, "low": 118.1, "close": 118.4})
    reclaim = _deep_state_row()
    reclaim.update({"open": 118.0, "high": 118.2, "low": 116.8, "close": 117.0, "HA_Bearish": True})

    result = _apply_deep_sweep_reclaim_state(frame([wall, wait, reclaim]))
    assert_true(not bool(result["Deep_Sell_Reclaim_Trigger"].iloc[0]), "1.00 wall candle is SETUP, not entry")
    assert_true(not bool(result["Deep_Sell_Reclaim_Trigger"].iloc[1]), "price above 0.886 must keep waiting")
    assert_true(bool(result["Deep_Sell_Reclaim_Trigger"].iloc[2]), "break of wall low inside 0.886-0.720 must trigger SELL")
    assert_equal(float(result["Deep_Sell_Wall_High"].iloc[2]), 121.0, "SELL wall must preserve sweep wick high")


def test_deep_reclaim_engines_use_wall_for_sl() -> None:
    buy_row = base_row()
    buy_row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Block_Buy_At_Upper": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": False,
            "BB_PRZ_Resistance_Confluence": False,
            "Deep_Buy_Reclaim_Trigger": True,
            "Deep_Buy_Wall_Low": 96.0,
            "Deep_Buy_Wall_High": 99.0,
            "Micro_Lot0_Low": 98.0,
            "Kivanc_Scenario_State": "READY_BUY_RECLAIM",
        }
    )
    buy = BuySignalEngine().evaluate(frame([buy_row]), 0, NY_SESSION, ALLOWED)
    assert_true(buy is not None, "deep BUY reclaim must create candidate")
    assert_equal(buy["entry_mode"], "V4_BUY_DEEP_100_WALL_RECLAIM", "deep BUY entry mode")
    assert_true(buy["sl"] < 96.0, "deep BUY SL must sit below VSA wall low")
    assert_equal(buy["vsa_wall_low"], 96.0, "deep BUY wall evidence")

    sell_row = base_row()
    sell_row.update(
        {
            "BB_PRZ_Resistance_Confluence": False,
            "Deep_Sell_Reclaim_Trigger": True,
            "Deep_Sell_Wall_Low": 101.0,
            "Deep_Sell_Wall_High": 104.0,
            "Micro_Lot0_High": 102.0,
            "Kivanc_Scenario_State": "READY_SELL_RECLAIM",
        }
    )
    sell = SellSignalEngine().evaluate(frame([sell_row]), 0, NY_SESSION, ALLOWED)
    assert_true(sell is not None, "deep SELL reclaim must create candidate")
    assert_equal(sell["entry_mode"], "V4_SELL_DEEP_100_WALL_RECLAIM", "deep SELL entry mode")
    assert_true(sell["sl"] > 104.0, "deep SELL SL must sit above VSA wall high")
    assert_equal(sell["vsa_wall_high"], 104.0, "deep SELL wall evidence")


def test_zone_pinbar_requires_later_break_and_mirrors() -> None:
    buy_rows = [
        {
            "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0,
            "Zone_Buy_Pinbar_Candidate": True, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": True, "In_Session_Kivanc_Sell_Zone": False,
            "HA_Bullish": True, "HA_Bearish": False,
            "VSA_Buy_Wins": True, "VSA_Sell_Wins": False,
        },
        {
            "open": 100.0, "high": 102.0, "low": 99.5, "close": 101.5,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": True, "In_Session_Kivanc_Sell_Zone": False,
            "HA_Bullish": True, "HA_Bearish": False,
            "VSA_Buy_Wins": True, "VSA_Sell_Wins": False,
        },
    ]
    buy = _apply_zone_pinbar_break_state(frame(buy_rows))
    assert_true(not bool(buy["Zone_Buy_Pinbar_Trigger"].iloc[0]), "pinbar candle is setup only")
    assert_true(bool(buy["Zone_Buy_Pinbar_Trigger"].iloc[1]), "later high break triggers BUY")
    assert_equal(float(buy["Zone_Buy_Wall_Low"].iloc[1]), 98.0, "BUY preserves wick wall")

    sell_rows = [
        {
            "open": 101.0, "high": 102.0, "low": 99.0, "close": 100.0,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": True,
            "In_Session_Kivanc_Buy_Zone": False, "In_Session_Kivanc_Sell_Zone": True,
            "HA_Bullish": False, "HA_Bearish": True,
            "VSA_Buy_Wins": False, "VSA_Sell_Wins": True,
        },
        {
            "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.5,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": False, "In_Session_Kivanc_Sell_Zone": True,
            "HA_Bullish": False, "HA_Bearish": True,
            "VSA_Buy_Wins": False, "VSA_Sell_Wins": True,
        },
    ]
    sell = _apply_zone_pinbar_break_state(frame(sell_rows))
    assert_true(bool(sell["Zone_Sell_Pinbar_Trigger"].iloc[1]), "later low break triggers SELL")
    assert_equal(float(sell["Zone_Sell_Wall_High"].iloc[1]), 102.0, "SELL preserves wick wall")


def _m5_trend(
    direction: str,
    offset: float = 0.0,
    start: str = "2026-07-10 15:00",
) -> pd.DataFrame:
    index = pd.date_range(start, periods=6, freq="5min", tz="UTC")
    if direction == "DOWN":
        opens = [106 + offset, 105 + offset, 104 + offset, 103 + offset, 102 + offset, 101 + offset]
        closes = [105 + offset, 104 + offset, 103 + offset, 102 + offset, 101 + offset, 100 + offset]
    else:
        opens = [100 + offset, 101 + offset, 102 + offset, 103 + offset, 104 + offset, 105 + offset]
        closes = [101 + offset, 102 + offset, 103 + offset, 104 + offset, 105 + offset, 106 + offset]
    return pd.DataFrame({
        "open": opens,
        "high": [max(o, c) + 0.2 for o, c in zip(opens, closes)],
        "low": [min(o, c) - 0.2 for o, c in zip(opens, closes)],
        "close": closes,
    }, index=index)


def test_ha5_uses_two_closed_bars() -> None:
    bearish = closed_ha5_evidence(_m5_trend("DOWN"))
    bullish = closed_ha5_evidence(_m5_trend("UP"))
    assert_true(bearish["two_bearish"], "two completed HA5 red bars required")
    assert_true(bullish["two_bullish"], "two completed HA5 green bars required")
    assert_equal(len(bearish["timestamps"]), 2, "HA evidence exposes exactly two closed bars")


def test_live_m5_extreme_detects_tp1_between_polls() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="m5-hit", ticket="100", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=110, filled_at="2026-07-10T14:59:00+00:00",
    )
    command = manager.evaluate("XAUUSD", 103, _m5_trend("UP"))
    assert_equal(command["action"], "PARTIAL_CLOSE_MOVE_BE", "M5 high detects missed TP1 touch")


def test_lifecycle_buy_tp1_be_then_ha5_exit_is_idempotent() -> None:
    manager = ExecutionLifecycleManager()
    first = manager.register_fill(
        symbol="XAUUSD", signal_id="buy-1", ticket="101", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=120, filled_at="2026-07-10T14:50:00+00:00",
    )
    repeated = manager.register_fill(
        symbol="XAUUSD", signal_id="buy-1", ticket="101", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=120, filled_at="2026-07-10T14:50:00+00:00",
    )
    assert_equal(repeated, first, "same fill retry must not reset position")

    tp1 = manager.evaluate("XAUUSD", 105)
    retry = manager.evaluate("XAUUSD", 105)
    assert_equal(tp1["action"], "PARTIAL_CLOSE_MOVE_BE", "TP1 command")
    assert_equal(retry["command_id"], tp1["command_id"], "poll retry keeps command id")
    state = manager.acknowledge(
        symbol="XAUUSD", command_id=tp1["command_id"], success=True, remaining_pct=50,
        acknowledged_at="2026-07-10T15:00:00+00:00",
    )
    assert_true(state["tp1_done"] and state["be_armed"], "TP1 ACK arms BE")
    assert_equal(state["sl"], 100.0, "BUY SL moves to entry")

    post_be = _m5_trend("DOWN", offset=10, start="2026-07-10 15:05")
    pre_be = pd.DataFrame(
        {"open": [101], "high": [102], "low": [95], "close": [101]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-10T14:55:00Z")]),
    )
    close = manager.evaluate(
        "XAUUSD", 104, pd.concat([pre_be, post_be]), now="2026-07-10T15:40:00+00:00"
    )
    assert_equal(close["action"], "CLOSE_ALL", "opposite HA5 closes BUY after BE")
    assert_equal(close["reason"], "HA5_OPPOSITE_2_AFTER_BE", "HA5 close reason")
    manager.acknowledge(
        symbol="XAUUSD", command_id=close["command_id"], success=True, r_multiple=1.5,
    )
    assert_true(not manager.has_active("XAUUSD"), "close ACK removes position")


def test_lifecycle_sell_mirror_and_hard_risk_gate() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="sell-1", ticket="201", direction="SELL",
        entry=100, sl=102, tp1=95, tp2=80, filled_at="2026-07-10T14:50:00+00:00",
    )
    tp1 = manager.evaluate("XAUUSD", 95)
    manager.acknowledge(
        symbol="XAUUSD", command_id=tp1["command_id"], success=True, remaining_pct=50,
        acknowledged_at="2026-07-10T15:00:00+00:00",
    )
    post_be = _m5_trend("UP", offset=-10, start="2026-07-10 15:05")
    pre_be = pd.DataFrame(
        {"open": [99], "high": [105], "low": [98], "close": [99]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-10T14:55:00Z")]),
    )
    close = manager.evaluate(
        "XAUUSD", 96, pd.concat([pre_be, post_be]), now="2026-07-10T15:40:00+00:00"
    )
    assert_equal(close["reason"], "HA5_OPPOSITE_2_AFTER_BE", "SELL uses two green HA5")
    manager.acknowledge(
        symbol="XAUUSD", command_id=close["command_id"], success=True, r_multiple=1.0,
    )

    for count in range(3):
        signal_id = f"loss-{count}"
        manager.register_fill(
            symbol="XAUUSD", signal_id=signal_id, ticket=signal_id, direction="BUY",
            entry=100, sl=98, tp1=105, tp2=110,
        )
        stop = manager.evaluate("XAUUSD", 98)
        manager.acknowledge(
            symbol="XAUUSD", command_id=stop["command_id"], success=True, r_multiple=-1.0,
        )
    assert_true(not manager.risk_permissions("XAUUSD")["daily_dd_ok"], "3R daily loss blocks entries")


def test_active_position_forces_ea_to_management_only() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="active-1", ticket="301", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=110,
    )
    original_manager = runtime.execution_lifecycle
    original_fetch_m5 = runtime.fetch_management_m5
    try:
        runtime.execution_lifecycle = manager
        runtime.fetch_management_m5 = lambda symbol: _m5_trend("UP")
        managed = runtime._attach_execution_lifecycle(
            data_symbol="XAU/USD",
            public_symbol="XAUUSD",
            df_15m=pd.DataFrame({"close": [101.0]}),
            ea={"action": "OPEN", "execution_state": "READY", "plan_lifecycle": {}},
        )
        assert_equal(managed["action"], "WAIT", "active position blocks another open")
        assert_equal(managed["execution_state"], "MANAGING", "EA becomes management-only")
    finally:
        runtime.execution_lifecycle = original_manager
        runtime.fetch_management_m5 = original_fetch_m5


def test_execution_api_fill_and_ack_round_trip() -> None:
    class JsonRequest:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    manager = ExecutionLifecycleManager()
    original_manager = runtime.execution_lifecycle
    original_cache = runtime._get_latest_signal()
    plan = {
        "signal_id": "api-buy-1",
        "action": "OPEN",
        "execution_state": "READY",
        "direction": "BUY",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 105.0,
        "tp_final": 110.0,
        "max_bars": 40,
    }
    try:
        runtime.execution_lifecycle = manager
        runtime._set_latest_signal({"symbol": "XAUUSD", "ea": plan})
        fill = asyncio.run(runtime.execution_fill(JsonRequest({
            "key": API_LICENSE_KEY,
            "symbol": "XAUUSD",
            "signal_id": "api-buy-1",
            "ticket": "9001",
            "fill_price": 100.0,
        })))
        assert_equal(fill["status"], "accepted", "fill endpoint accepts matching ready plan")
        command = manager.evaluate("XAUUSD", 105.0)
        ack = asyncio.run(runtime.execution_ack(JsonRequest({
            "key": API_LICENSE_KEY,
            "symbol": "XAUUSD",
            "command_id": command["command_id"],
            "success": True,
            "remaining_pct": 50,
        })))
        assert_true(ack["result"]["be_armed"], "ACK endpoint persists BE state")
    finally:
        runtime.execution_lifecycle = original_manager
        runtime._set_latest_signal(original_cache)


def test_telegram_public_output_hides_engine_internals() -> None:
    engine = {
        "direction": "SELL",
        "entry_mode": "V4_SELL_PINE_PRZ_VSA",
        "setup_state": "SELL_CF_READY",
        "journey_state": "V5_SELL_JOURNEY",
        "entry": 100.0,
        "sl": 102.0,
        "tp": 90.0,
        "signal_tp": 95.0,
        "bb_lower_tp": 90.0,
        "prz_resistance_low": 99.0,
        "prz_resistance_high": 103.0,
    }
    payload = {
        "symbol": "XAUUSD",
        "signal": {
            "timestamp": "2026-07-10T15:00:00+00:00",
            "entry_mode": "V4_SELL_PINE_PRZ_VSA",
            "setup_state": "SELL_CF_READY",
            "scenario_state": "SELL_CF_READY",
            "journey_state": "V5_SELL_JOURNEY",
            "engine_v4": engine,
            "blueprint": {
                "current_price": 100.0,
                "trend_h1": "PULLBACK_UP",
                "trend_h4": "PULLBACK_DOWN",
                "price_action": {"m15_phase": "PULLBACK_DOWN"},
            },
        },
        "ea": {
            "action": "WAIT",
            "execution_state": "WATCH",
            "direction": "SELL",
            "entry_mode": "V4_SELL_PINE_PRZ_VSA",
            "entry": 100.0,
            "sl": 102.0,
            "tp_final": 90.0,
            "session": "ASIA",
        },
    }

    trend_text = format_telegram_trend_update(payload)
    signal_text = format_telegram_signal(payload)
    combined = trend_text + "\n" + signal_text

    forbidden = [
        "engine_v4",
        "V4_SELL_PINE_PRZ_VSA",
        "SELL_CF_READY",
        "V5_SELL_JOURNEY",
        "PINE_PRZ",
        "VSA",
        "BOS",
    ]
    for token in forbidden:
        assert_true(token not in combined, f"public Telegram output leaked {token}")

    assert_true("WAIT SETUP" in trend_text, "trend update should use public wait setup label")
    assert_true("V4_SESSION" in signal_text, "trade alert should use public V4_SESSION type")


def test_closed_market_suppresses_all_telegram() -> None:
    payload = {
        "symbol": "XAUUSD",
        "signal": {
            "timestamp": "2026-07-10T20:00:00+00:00",
            "gates": {"session": "CLOSED"},
            "blueprint": {"current_price": 100.0},
        },
        "ea": {
            "action": "OPEN",
            "execution_state": "READY",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 98.0,
            "tp_final": 110.0,
            "rr": 5.0,
            "rr_ok": True,
            "levels_ready": True,
            "directional_levels_ok": True,
            "setup_ok": True,
            "zone_ok": True,
            "vsa_gate_ok": True,
            "session": "CLOSED",
        },
    }
    closed = SessionState(
        session="CLOSED",
        liquidity="NONE",
        bkk_hour=3,
        utc_hour=20,
        timestamp="2026-07-11T03:00:00+07:00",
    )
    sent = []
    original_clock_get = runtime.SessionClock.get
    original_enabled = runtime._telegram_enabled
    original_send = runtime.send_telegram_message
    original_notify = runtime.TELEGRAM_NOTIFY_TREND_UPDATE
    original_post = runtime.requests.post

    try:
        runtime.SessionClock.get = lambda self, dt=None: closed
        runtime._telegram_enabled = lambda: True
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = True
        runtime.send_telegram_message = lambda text: sent.append(text) or True

        runtime.maybe_broadcast_signal(payload)
        runtime.maybe_broadcast_trend_update(payload)
        assert_equal(sent, [], "closed session must block signal and trend broadcasts")

        runtime.send_telegram_message = original_send
        runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram network call must not occur while CLOSED")
        )
        assert_true(
            not runtime.send_telegram_message("closed market"),
            "direct Telegram sender must fail closed",
        )
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime._telegram_enabled = original_enabled
        runtime.send_telegram_message = original_send
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = original_notify
        runtime.requests.post = original_post


def test_weekend_is_hard_closed_before_session_resolution() -> None:
    saturday = pd.Timestamp("2026-07-11T10:00:00+07:00").to_pydatetime()
    sunday = pd.Timestamp("2026-07-12T20:00:00+07:00").to_pydatetime()
    for value in (saturday, sunday):
        assert_equal(market_closed_reason(value), "WEEKEND", "weekend close reason")
        assert_equal(SessionClock().get(value).session, "CLOSED", "weekend session")
        assert_true(
            not runtime._telegram_market_is_open(now=value),
            "weekend must block Telegram even during an intraday session hour",
        )

    summer_before_close = pd.Timestamp("2026-07-11T03:30:00+07:00").to_pydatetime()
    summer_after_close = pd.Timestamp("2026-07-11T04:30:00+07:00").to_pydatetime()
    winter_before_open = pd.Timestamp("2026-12-07T05:30:00+07:00").to_pydatetime()
    winter_after_open = pd.Timestamp("2026-12-07T06:30:00+07:00").to_pydatetime()
    assert_equal(market_closed_reason(summer_before_close), "", "Friday NY pre-close remains openable")
    assert_equal(market_closed_reason(summer_after_close), "WEEKEND", "Friday NY post-close")
    assert_equal(market_closed_reason(winter_before_open), "WEEKEND", "winter Sunday NY pre-open")
    assert_equal(market_closed_reason(winter_after_open), "", "winter Sunday NY post-open")


def test_seasonal_bangkok_sessions_survive_conflict_resolution() -> None:
    summer = pd.Timestamp("2026-07-14T04:30:00+07:00").to_pydatetime()
    winter_before = pd.Timestamp("2026-12-08T04:30:00+07:00").to_pydatetime()
    winter_after = pd.Timestamp("2026-12-08T05:30:00+07:00").to_pydatetime()
    assert_equal(SessionClock().get(summer).session, "ASIA", "summer ASIA opens at 04:00 BKK")
    assert_equal(SessionClock().get(winter_before).session, "CLOSED", "winter pre-ASIA gap")
    assert_equal(SessionClock().get(winter_after).session, "ASIA", "winter ASIA opens at 05:00 BKK")


def test_closed_market_pipeline_is_canonical_and_skips_data_fetch() -> None:
    closed = SessionState(
        session="CLOSED",
        liquidity="NONE",
        bkk_hour=10,
        utc_hour=3,
        timestamp="2026-07-12T10:00:00+07:00",
    )
    original_clock_get = runtime.SessionClock.get
    original_fetch = runtime.fetch_multi_tf
    try:
        runtime.SessionClock.get = lambda self, dt=None: closed
        runtime.fetch_multi_tf = lambda symbol: (_ for _ in ()).throw(
            AssertionError("closed-market pipeline must not fetch candles")
        )
        payload = runtime.run_pipeline()
        assert_equal(payload["status"], NO_SIGNAL, "closed market canonical status")
        assert_equal(payload["direction"], None, "closed market has no direction")
        assert_equal(payload["ea"]["action"], "WAIT", "EA waits while closed")
        assert_equal(payload["ea"]["execution_state"], "BLOCKED", "EA closed state")
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime.fetch_multi_tf = original_fetch


def test_configured_holiday_blocks_session_and_telegram() -> None:
    holiday = pd.Timestamp("2026-07-13T10:00:00+07:00").to_pydatetime()
    original = os.environ.get("ALPHA_MARKET_CLOSED_DATES")
    try:
        os.environ["ALPHA_MARKET_CLOSED_DATES"] = "2026-07-13"
        assert_equal(market_closed_reason(holiday), "CONFIGURED_HOLIDAY", "holiday reason")
        assert_equal(SessionClock().get(holiday).session, "CLOSED", "holiday session")
        assert_true(not runtime._telegram_market_is_open(now=holiday), "holiday Telegram gate")
    finally:
        if original is None:
            os.environ.pop("ALPHA_MARKET_CLOSED_DATES", None)
        else:
            os.environ["ALPHA_MARKET_CLOSED_DATES"] = original


def test_weekend_direct_sender_never_calls_telegram_network() -> None:
    weekend = pd.Timestamp("2026-07-12T20:00:00+07:00").to_pydatetime()
    original_clock_get = runtime.SessionClock.get
    original_enabled = runtime._telegram_enabled
    original_post = runtime.requests.post
    try:
        runtime.SessionClock.get = lambda self, dt=None: original_clock_get(self, weekend)
        runtime._telegram_enabled = lambda: True
        runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram network call must not occur on weekend")
        )
        assert_true(
            not runtime.send_telegram_message("weekend blocked"),
            "direct Telegram sender must fail closed on weekend",
        )
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime._telegram_enabled = original_enabled
        runtime.requests.post = original_post


def test_every_repository_telegram_sender_uses_central_closed_gate() -> None:
    original_force = os.environ.get("ALPHA_FORCE_MARKET_CLOSED")
    original_runtime_enabled = runtime._telegram_enabled
    original_bot_token = telegram_bot_runtime.TOKEN
    original_post = telegram_guard_runtime.requests.post
    try:
        os.environ["ALPHA_FORCE_MARKET_CLOSED"] = "true"
        runtime._telegram_enabled = lambda: True
        telegram_bot_runtime.TOKEN = "test-token"
        telegram_guard_runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("No repository Telegram sender may reach the network while closed")
        )

        assert_true(not runtime.send_telegram_message("blocked"), "runtime sender gate")
        assert_true(not warning_runtime.send_telegram("blocked"), "early warning sender gate")
        assert_true(not telegram_bot_runtime.send_message("1", "blocked"), "bot sender gate")
        assert_true(
            telegram_guard_runtime.guarded_telegram_post(
                "https://api.telegram.org/test",
                json={"text": "blocked"},
                timeout=1,
            ) is None,
            "network-layer sender gate",
        )

        for relative in (
            "alpha_buffalo_signal.py",
            "early_warning.py",
            "telegram_bot.py",
            "scripts/friday_sim.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert_true("guarded_telegram_post" in source, f"{relative} central sender")
            assert_true("requests.post(" not in source, f"{relative} bypasses central sender")
    finally:
        if original_force is None:
            os.environ.pop("ALPHA_FORCE_MARKET_CLOSED", None)
        else:
            os.environ["ALPHA_FORCE_MARKET_CLOSED"] = original_force
        runtime._telegram_enabled = original_runtime_enabled
        telegram_bot_runtime.TOKEN = original_bot_token
        telegram_guard_runtime.requests.post = original_post


TESTS = [
    test_upper_sell_not_blocked_by_bullish_context,
    test_lower_buy_not_blocked_by_bearish_context,
    test_lower_zone_blocks_fresh_sell,
    test_upper_zone_blocks_fresh_buy,
    test_harmonic_d_prz_is_one_direction_only,
    test_confirmed_tunnel_sweep_arms_only_the_aligned_approach,
    test_parallel_channel_uses_confirmed_h1_pivots_and_ignores_forming_wick,
    test_final_gate_combines_hours_risk_and_harmonic_bias,
    test_low_rr_candidate_waits_in_ea_payload,
    test_buy_and_sell_share_one_api_schema,
    test_signal_latest_preserves_canonical_contract,
    test_no_signal_has_no_direction_and_ea_waits,
    test_directional_price_validator_blocks_invalid_buy,
    test_error_uses_same_schema_and_never_executes,
    test_choch_promotes_to_v5_journey,
    test_no_choch_stays_v4_range,
    test_session_kivanc_mask_uses_bangkok_asia_hours,
    test_indicators_do_not_read_future_daily_or_h1_bars,
    test_deep_buy_requires_wall_then_reclaim,
    test_deep_sell_requires_wall_then_reclaim,
    test_deep_reclaim_engines_use_wall_for_sl,
    test_zone_pinbar_requires_later_break_and_mirrors,
    test_ha5_uses_two_closed_bars,
    test_live_m5_extreme_detects_tp1_between_polls,
    test_lifecycle_buy_tp1_be_then_ha5_exit_is_idempotent,
    test_lifecycle_sell_mirror_and_hard_risk_gate,
    test_active_position_forces_ea_to_management_only,
    test_execution_api_fill_and_ack_round_trip,
    test_telegram_public_output_hides_engine_internals,
    test_closed_market_suppresses_all_telegram,
    test_weekend_is_hard_closed_before_session_resolution,
    test_seasonal_bangkok_sessions_survive_conflict_resolution,
    test_closed_market_pipeline_is_canonical_and_skips_data_fetch,
    test_configured_holiday_blocks_session_and_telegram,
    test_weekend_direct_sender_never_calls_telegram_network,
    test_every_repository_telegram_sender_uses_central_closed_gate,
]


def main() -> int:
    failures = []
    for test in TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")

    print(f"\nSummary: {len(TESTS) - len(failures)} passed, {len(failures)} failed")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
