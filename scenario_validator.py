"""
scenario_validator.py — Alpha Buffalo v5 Sprint 3B
Scenario Validation Layer — กรองก่อนยิง Signal

Checks:
1. Conflict Check (open orders)
2. Spread Filter
3. Score Gate
4. VSA Gate
5. Volatility Filter (ATR)
"""

import os
import requests
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

BKK = timezone(timedelta(hours=7))

# ── Config ────────────────────────────────────────────────
MAX_SPREAD_USD   = 0.50   # XAU max spread
MAX_LAYERS_BUY   = 2
MAX_LAYERS_SELL  = 2
V5_MIN_SCORE     = 8
V4_MIN_SCORE     = 4
MAX_ATR_MULT     = 3.0    # ถ้า ATR สูงกว่าปกติ X เท่า = volatile เกิน


@dataclass
class ValidationResult:
    valid:   bool
    reason:  str
    stage:   str   # "CONFLICT" / "SPREAD" / "SCORE" / "VSA" / "VOLATILITY" / "OK"
    details: dict


def validate_scenario(
    direction:    str,
    signal_type:  str,
    score:        int,
    pattern:      str,
    df_15m,                    # DataFrame
    open_buy_layers:  int = 0,
    open_sell_layers: int = 0,
    current_spread:   float = 0.0,
) -> ValidationResult:
    """
    ตรวจสอบ scenario ก่อนยิง signal

    Returns ValidationResult
    """

    # ── 1. Conflict Check ─────────────────────────────────
    if direction == "BUY" and open_buy_layers >= MAX_LAYERS_BUY:
        return ValidationResult(
            valid=False, stage="CONFLICT",
            reason=f"🚫 BUY layers full ({open_buy_layers}/{MAX_LAYERS_BUY})",
            details={"buy_layers": open_buy_layers},
        )
    if direction == "SELL" and open_sell_layers >= MAX_LAYERS_SELL:
        return ValidationResult(
            valid=False, stage="CONFLICT",
            reason=f"🚫 SELL layers full ({open_sell_layers}/{MAX_LAYERS_SELL})",
            details={"sell_layers": open_sell_layers},
        )

    # ── 2. Spread Filter ──────────────────────────────────
    if current_spread > MAX_SPREAD_USD:
        return ValidationResult(
            valid=False, stage="SPREAD",
            reason=f"⚠️ Spread too wide: {current_spread:.2f} > {MAX_SPREAD_USD}",
            details={"spread": current_spread},
        )

    # ── 3. Score Gate ─────────────────────────────────────
    if signal_type == "V5_SNIPER" and score < V5_MIN_SCORE:
        return ValidationResult(
            valid=False, stage="SCORE",
            reason=f"📊 V5 score too low: {score} < {V5_MIN_SCORE}",
            details={"score": score, "required": V5_MIN_SCORE},
        )
    if signal_type == "V4_SESSION" and score < V4_MIN_SCORE:
        return ValidationResult(
            valid=False, stage="SCORE",
            reason=f"📊 V4 score too low: {score} < {V4_MIN_SCORE}",
            details={"score": score, "required": V4_MIN_SCORE},
        )

    # ── 4. VSA Gate ───────────────────────────────────────
    if signal_type == "V5_SNIPER" and not pattern:
        return ValidationResult(
            valid=False, stage="VSA",
            reason="🦋 V5 requires Harmonic pattern — not found",
            details={"pattern": pattern},
        )

    # ── 5. Volatility Filter ──────────────────────────────
    if df_15m is not None and len(df_15m) >= 50:
        try:
            atr_curr = float(
                (df_15m["high"] - df_15m["low"]).iloc[-1]
            )
            atr_avg  = float(
                (df_15m["high"] - df_15m["low"]).tail(14).mean()
            )
            if atr_avg > 0 and atr_curr > atr_avg * MAX_ATR_MULT:
                return ValidationResult(
                    valid=False, stage="VOLATILITY",
                    reason=f"🌪️ Volatility too high: ATR {atr_curr:.2f} > avg {atr_avg:.2f} × {MAX_ATR_MULT}",
                    details={"atr_curr": atr_curr, "atr_avg": atr_avg},
                )
        except Exception:
            pass

    # ── All checks passed ─────────────────────────────────
    return ValidationResult(
        valid=True, stage="OK",
        reason="✅ Scenario validated",
        details={
            "direction":   direction,
            "signal_type": signal_type,
            "score":       score,
            "pattern":     pattern,
        },
    )
