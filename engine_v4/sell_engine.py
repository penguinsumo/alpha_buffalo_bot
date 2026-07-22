#!/usr/bin/env python3
"""
SellSignalEngine — Pine PRZ/PA/VSA adapted V4 SELL setup.

SELL cannot override a lower-zone BUY setup. Location comes before bias.
"""
from __future__ import annotations

import os
from typing import Optional
import pandas as pd
from core.contracts.base_engine import BaseEngine
from engine_v4.session_gate import GateResult
from engine_v4.strategy_policy import (
    BASELINE_DEFAULT,
    baseline_sell_setup,
    confirmed_harmonic_d_override,
    strategy_profile,
)
from session_clock import SessionState


class SellSignalEngine(BaseEngine):
    def run(self, *args, **kwargs):
        return self.evaluate(*args, **kwargs)

    def evaluate(
        self,
        df: pd.DataFrame,
        idx: int,
        session_state: SessionState,
        gate_result: GateResult,
    ) -> Optional[dict]:
        if not gate_result.allowed:
            return None

        row = df.iloc[idx]
        min_rr = float(os.getenv("TRADE_MIN_RR", "1.5"))

        # Critical veto from current Pine chart logic:
        # lower PRZ/BB + bullish PA/VSA = setup, not a place to open fresh SELL.
        if bool(row.get("V4_Block_Sell_At_Lower", False)):
            return None

        lookback = df.iloc[max(0, idx - 5):idx + 1]
        recent_sweep_above_100 = bool(lookback.get("Sweep_Above_100", False).any())
        recent_sell_reclaim = bool(lookback.get("Sell_Reclaim", False).any())
        recent_micro_bos_down = bool(lookback.get("Micro_BOS_Down", False).any())
        ha_bearish = bool(row.get("HA_Bearish", False))

        upper_setup = bool(row.get("V4_Sell_Setup", False))
        pine_valid = bool(row.get("Pine_Valid_Sell", False))
        sell_continuation = bool(recent_micro_bos_down and row.get("VSA_Sell_Wins", False))
        profile = strategy_profile()
        baseline_route = profile == BASELINE_DEFAULT and baseline_sell_setup(row)
        harmonic_reversal = bool(
            profile == BASELINE_DEFAULT
            and (upper_setup or pine_valid)
            and confirmed_harmonic_d_override("SELL", gate_result.reason)
        )

        if profile == BASELINE_DEFAULT:
            if not (baseline_route or harmonic_reversal):
                return None
        else:
            # Previous location-first behavior remains available for A/B tests.
            if not (upper_setup or pine_valid):
                if bool(row.get("Trend_1H_Up", False)):
                    return None
                if not (row.get("EMA20", 0) < row.get("EMA50", 0)):
                    return None
            if not (upper_setup or pine_valid or sell_continuation):
                return None

        entry = float(row["close"])
        atr = float(row.get("ATR14", 0.0) or 0.0)
        if entry <= 0 or atr <= 0:
            return None

        micro_high = float(row.get("Micro_Lot0_High", row.get("high", entry)) or row.get("high", entry))
        prz_high = float(row.get("Pine_PRZ_Resistance_High", micro_high) or micro_high)
        bb_prz_confluence = bool(row.get("BB_PRZ_Resistance_Confluence", False))
        deep_reclaim = bool(row.get("Deep_Sell_Reclaim_Trigger", False))
        deep_wall_high = float(row.get("Deep_Sell_Wall_High", 0.0) or 0.0)
        pinbar_break = bool(row.get("Zone_Sell_Pinbar_Trigger", False))
        pinbar_wall_high = float(row.get("Zone_Sell_Wall_High", 0.0) or 0.0)

        if baseline_route and not harmonic_reversal:
            sl_anchor = entry + atr * 1.5
            sl = sl_anchor
        elif deep_reclaim and deep_wall_high > 0:
            sl_anchor = deep_wall_high
            sl = sl_anchor + max(atr * 0.12, entry * 0.00015)
        elif pinbar_break and pinbar_wall_high > 0:
            sl_anchor = pinbar_wall_high
            sl = sl_anchor + max(atr * 0.12, entry * 0.00015)
        elif bb_prz_confluence or bool(row.get("V4_Sell_Entry_Zone", False)):
            sl_anchor = max(float(row["high"]), micro_high)
            sl = sl_anchor + max(atr * 0.12, entry * 0.00015)
        else:
            sl_anchor = max(float(row["high"]), micro_high, prz_high if upper_setup or pine_valid else float(row["high"]))
            sl = sl_anchor + atr * 0.25

        signal_tp = float(row.get("Fib_072", 0.0) or 0.0)
        bb_lower_tp = float(row.get("BB_Lower", 0.0) or 0.0)
        baseline_prz_tp = float(row.get("PRZ_Next", 0.0) or 0.0)
        if baseline_route and not harmonic_reversal:
            if 0 < signal_tp < entry:
                tp = signal_tp
                exit_mode = "BASELINE_FIB_072"
            elif 0 < baseline_prz_tp < entry:
                tp = baseline_prz_tp
                exit_mode = "BASELINE_PRZ_NEXT"
            else:
                tp = bb_lower_tp
                exit_mode = "BASELINE_BB_LOWER_FALLBACK"
        elif signal_tp > 0 and signal_tp < entry and (recent_micro_bos_down or recent_sweep_above_100 or recent_sell_reclaim):
            tp = signal_tp
            exit_mode = "V5_SIGNAL_TP"
        else:
            tp = bb_lower_tp
            exit_mode = "V4_BB_LOWER"

        if not (tp < entry < sl):
            return None

        risk = sl - entry
        reward = entry - tp
        rr = reward / risk if risk > 0 else 0.0
        rr_ok = rr >= min_rr

        close_below_ema20 = bool(row["close"] < row["EMA20"])
        close_below_bb_mid = bool(row["close"] < row["BB_Mid"])
        ema20_below_ema50 = bool(row["EMA20"] < row["EMA50"])

        quality_score = 2
        if pine_valid:
            quality_score += 2
        if recent_micro_bos_down:
            quality_score += 2
        if recent_sell_reclaim:
            quality_score += 1
        if recent_sweep_above_100 and ha_bearish:
            quality_score += 1
        if close_below_ema20:
            quality_score += 1
        if close_below_bb_mid:
            quality_score += 1
        if deep_reclaim:
            quality_score += 2
        if pinbar_break:
            quality_score += 1
        if baseline_route:
            quality_score += 1
        if harmonic_reversal:
            quality_score += 2

        if quality_score >= 5:
            quality_grade = "PREMIUM"
        elif quality_score >= 3:
            quality_grade = "GOOD"
        else:
            quality_grade = "BASE"

        basis_parts = []
        if pine_valid:
            basis_parts.append("PINE_PRZ_PA_VSA")
        if recent_micro_bos_down:
            basis_parts.append("MICRO_BOS")
        if recent_sell_reclaim:
            basis_parts.append("RECLAIM")
        if recent_sweep_above_100 and ha_bearish:
            basis_parts.append("SWEEP_HA")
        if deep_reclaim:
            basis_parts.append("DEEP_100_RECLAIM")
        if pinbar_break:
            basis_parts.append("PINBAR_LOW_BREAK")
        if baseline_route:
            basis_parts.append("BASELINE_TREND_SWEEP")
        if harmonic_reversal:
            basis_parts.append("HARMONIC_D_REVERSAL")
        v5_basis = "|".join(basis_parts) if basis_parts else "UPPER_REJECTION"

        if harmonic_reversal:
            entry_mode = "HARMONIC_D_PRZ_SELL_OVERRIDE"
        elif baseline_route:
            entry_mode = "BASELINE_SELL_TREND_SWEEP"
        elif deep_reclaim:
            entry_mode = "V4_SELL_DEEP_100_WALL_RECLAIM"
        elif pinbar_break:
            entry_mode = "V4_SELL_KIVANC_PINBAR_BREAK"
        elif bb_prz_confluence:
            entry_mode = "V4_SELL_BB_PRZ_CONFLUENCE"
        elif upper_setup or pine_valid:
            entry_mode = "V4_SELL_PINE_PRZ_VSA"
        else:
            entry_mode = "V5_SELL_CONTINUATION"

        tp1 = float(row.get("BB_Mid", 0.0) or 0.0)
        if not (tp < tp1 < entry):
            tp1 = tp

        candle_range = float(row["high"] - row["low"])
        sell_rejection_wick_ratio = (
            float((row["high"] - max(row["open"], row["close"])) / candle_range)
            if candle_range > 0 else 0.0
        )
        candle_body_ratio = (
            float(abs(row["close"] - row["open"]) / candle_range)
            if candle_range > 0 else 0.0
        )

        return {
            "status": "SIGNAL",
            "direction": "SELL",
            "entry_price": entry,
            "sl_price": sl,
            "tp1_price": tp1,
            "tp2_price": tp,
            "score": quality_score,
            "reason": f"{profile}: {session_state.session} SELL",
            "zone_confluence": bool(bb_prz_confluence or deep_reclaim or pinbar_break),
            "bb_prz_confluence": bb_prz_confluence,
            "v4_entry_zone": bool(row.get("V4_Sell_Entry_Zone", False)),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "session": session_state.session,
            "timestamp": row.name,
            "visual_sl_mid": row["BB_Mid"],
            "entry_mode": entry_mode,
            "strategy_profile": profile,
            "baseline_default": baseline_route,
            "harmonic_reversal_override": harmonic_reversal,
            "exit_mode": exit_mode,
            "setup_state": "SELL_SETUP" if not recent_micro_bos_down else "SELL_CF_READY",
            "signal_tp": signal_tp,
            "bb_lower_tp": bb_lower_tp,
            "recent_sell_reclaim": recent_sell_reclaim,
            "recent_sweep_above_100": recent_sweep_above_100,
            "recent_micro_bos_down": recent_micro_bos_down,
            "v5_exit_qualified": bool(recent_micro_bos_down or recent_sweep_above_100 or recent_sell_reclaim),
            "v4_session_confirmed": True,
            "ha_bearish": ha_bearish,
            "sell_dot_proxy": bool(ha_bearish and close_below_ema20),
            "be_policy": "BASELINE_1R" if baseline_route and not harmonic_reversal else "CURRENT_BBMID_LOW",
            "trail_policy": "NONE",
            "v5_quality_score": quality_score,
            "v5_quality_grade": quality_grade,
            "v5_basis": v5_basis,
            "v5_premium_any": bool(quality_score >= 5),
            "v5_premium_micro_bos": bool(recent_micro_bos_down),
            "v5_premium_reclaim": bool(recent_sell_reclaim),
            "v5_premium_sweep_ha": bool(recent_sweep_above_100 and ha_bearish),
            "close_below_ema20": close_below_ema20,
            "close_below_bb_mid": close_below_bb_mid,
            "ema20_below_ema50": ema20_below_ema50,
            "bb_upper_touch_strength": float(row["high"] / row["BB_Upper"]) if row.get("BB_Upper", 0) else 0.0,
            "sell_rejection_wick_ratio": sell_rejection_wick_ratio,
            "candle_body_ratio": candle_body_ratio,
            "atr14_at_entry": atr,
            "entry_to_sl_points": risk,
            "entry_to_tp_points": reward,
            "entry_rr": rr,
            "rr_ok": rr_ok,
            "min_rr": min_rr,
            "session_quality_gate": "HARMONIC_D_PRZ_OVERRIDE" if harmonic_reversal else "BASELINE_H1_EMA_SWEEP" if baseline_route else "DEEP_100_WALL_RECLAIM" if deep_reclaim else "KIVANC_PINBAR_BREAK" if pinbar_break else "PINE_PRZ_RESISTANCE_PA_VSA" if upper_setup or pine_valid else "BOS_CONTINUATION",
            "sell_dot_reason": "PINE_PRZ_PA_VSA" if pine_valid else "MICRO_BOS_CONTINUATION",
            "pine_valid": pine_valid,
            "pa_bear_confirmed": bool(row.get("Pine_PA_Bear_Confirmed", False)),
            "vsa_buy_pressure": float(row.get("VSA_Buy_Pressure", 0.0) or 0.0),
            "vsa_sell_pressure": float(row.get("VSA_Sell_Pressure", 0.0) or 0.0),
            "micro_lot0_high": micro_high,
            "deep_reclaim": deep_reclaim,
            "pinbar_break": pinbar_break,
            "vsa_wall_low": float(row.get("Deep_Sell_Wall_Low", 0.0) or 0.0) if deep_reclaim else float(row.get("Zone_Sell_Wall_Low", 0.0) or 0.0) if pinbar_break else 0.0,
            "vsa_wall_high": deep_wall_high if deep_reclaim else pinbar_wall_high if pinbar_break else micro_high,
            "kivanc_scenario_state": str(row.get("Kivanc_Scenario_State", "OUTSIDE")),
            "kivanc_zone_low": float(row.get("Kivanc_Sell_Zone_Low", 0.0) or 0.0),
            "kivanc_zone_high": float(row.get("Kivanc_Sell_Zone_High", 0.0) or 0.0),
            "prz_resistance_low": float(row.get("Pine_PRZ_Resistance_Low", 0.0) or 0.0),
            "prz_resistance_high": float(row.get("Pine_PRZ_Resistance_High", 0.0) or 0.0),
            "max_bars": 40,
        }
