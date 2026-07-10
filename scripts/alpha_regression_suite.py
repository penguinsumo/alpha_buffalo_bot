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

from engine_v4.buy_engine import BuySignalEngine
from engine_v4.final_gate import FinalGate
from engine_v4.router import SignalRouter
from engine_v4.sell_engine import SellSignalEngine
from engine_v4.session_gate import GateResult
from session_clock import SessionClock, SessionState
from alpha_buffalo_signal import (
    build_ea_payload,
    format_telegram_signal,
    format_telegram_trend_update,
)


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
    assert_true(sig["entry_mode"].startswith("V4_SELL"), "upper-zone SELL must stay V4")
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
        "decision": {"action": "BUY", "confidence": 0.7, "score": 6, "grade": "VALID_TRADE"},
        "timestamp": "2026-07-10T15:00:00+00:00",
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


TESTS = [
    test_upper_sell_not_blocked_by_bullish_context,
    test_lower_zone_blocks_fresh_sell,
    test_upper_zone_blocks_fresh_buy,
    test_low_rr_candidate_waits_in_ea_payload,
    test_choch_promotes_to_v5_journey,
    test_no_choch_stays_v4_range,
    test_telegram_public_output_hides_engine_internals,
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

