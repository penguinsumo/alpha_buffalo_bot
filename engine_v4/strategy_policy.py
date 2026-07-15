#!/usr/bin/env python3
"""Entry-policy switch for the production V4 engines.

The default profile restores the proven feature/engine-v4 SELL filter. BUY is
kept safer by requiring that baseline filter *and* the current PRZ setup.
Harmonic remains an explicit confirmed-D reversal exception, never an
unqualified counter-trend entry. The previous location-first behavior remains
available for controlled comparison through ``ENGINE_STRATEGY_PROFILE``.
"""
from __future__ import annotations

import os
from typing import Any, Mapping


BASELINE_DEFAULT = "BASELINE_DEFAULT"
LOCATION_FIRST = "LOCATION_FIRST"
VALID_PROFILES = frozenset({BASELINE_DEFAULT, LOCATION_FIRST})


def strategy_profile() -> str:
    value = str(os.getenv("ENGINE_STRATEGY_PROFILE", BASELINE_DEFAULT) or "").upper()
    return value if value in VALID_PROFILES else BASELINE_DEFAULT


def _number(row: Mapping[str, Any], name: str, default: float = 0.0) -> float:
    try:
        return float(row.get(name, default) or default)
    except (TypeError, ValueError):
        return default


def baseline_buy_setup(row: Mapping[str, Any]) -> bool:
    """Historical baseline BUY: trend + broad swing location + lower sweep."""
    swing_high = _number(row, "Swing_H")
    diff = _number(row, "Diff")
    close = _number(row, "close")
    low = _number(row, "low")
    bb_lower = _number(row, "BB_Lower")
    if diff <= 0.0 or bb_lower <= 0.0:
        return False
    golden_low = swing_high - diff
    golden_high = swing_high - diff * 0.5
    return bool(
        row.get("Trend_1H_Up", False)
        and _number(row, "EMA20") > _number(row, "EMA50")
        and golden_low <= close <= golden_high
        and row.get("Bull_Sweep", False)
        and low <= bb_lower * 1.02
    )


def baseline_sell_setup(row: Mapping[str, Any]) -> bool:
    """Historical baseline SELL: H1/EMA down + upper sweep/rejection."""
    bb_upper = _number(row, "BB_Upper")
    return bool(
        not row.get("Trend_1H_Up", False)
        and _number(row, "EMA20") < _number(row, "EMA50")
        and row.get("Bear_Sweep", False)
        and bb_upper > 0.0
        and _number(row, "high") >= bb_upper * 0.98
    )


def confirmed_harmonic_d_override(direction: str, gate_reason: str) -> bool:
    """True only when FinalGate confirmed the requested reversal at D/PRZ."""
    side = str(direction or "").upper()
    reason = str(gate_reason or "").upper()
    return side in {"BUY", "SELL"} and f"HARMONIC_{side}_D_PRZ_ALLOWED" in reason
