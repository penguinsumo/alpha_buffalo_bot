#!/usr/bin/env python3
"""Offline regression suite for Alpha Buffalo v12-core.

This suite protects the final project contract from drift while older branch
ideas are mined back into production.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import alpha_buffalo_signal as runtime
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.final_gate import FinalGate
from engine_v4.router import SignalRouter
from engine_v4.sell_engine import SellSignalEngine
from engine_v4.session_gate import GateResult
from session_clock import SessionClock, SessionState
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

    try:
        _set_latest_signal(expected)
        served = signal_latest(key=API_LICENSE_KEY, symbol="XAU/USD")
    finally:
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


TESTS = [
    test_upper_sell_not_blocked_by_bullish_context,
    test_lower_buy_not_blocked_by_bearish_context,
    test_lower_zone_blocks_fresh_sell,
    test_upper_zone_blocks_fresh_buy,
    test_low_rr_candidate_waits_in_ea_payload,
    test_buy_and_sell_share_one_api_schema,
    test_signal_latest_preserves_canonical_contract,
    test_no_signal_has_no_direction_and_ea_waits,
    test_directional_price_validator_blocks_invalid_buy,
    test_error_uses_same_schema_and_never_executes,
    test_choch_promotes_to_v5_journey,
    test_no_choch_stays_v4_range,
    test_telegram_public_output_hides_engine_internals,
    test_closed_market_suppresses_all_telegram,
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
