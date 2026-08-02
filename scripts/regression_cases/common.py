#!/usr/bin/env python3
"""Offline regression suite for Alpha Buffalo v12-core.

This suite protects the final project contract from drift while older branch
ideas are mined back into production.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import alpha_buffalo_signal as runtime
import early_warning as warning_runtime
import telegram_bot as telegram_bot_runtime
import telegram_guard as telegram_guard_runtime
from decision_engine import DecisionEngine
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
    ScenarioScanner,
    build_confirmed_parallel_channel,
    confirmed_channel_boundary_broken,
    detect_confirmed_tunnel_sweep,
)
from scenario_blueprint import ScenarioBlueprint
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

__all__ = [name for name in globals() if not name.startswith('__')]
