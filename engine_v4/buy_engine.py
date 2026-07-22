#!/usr/bin/env python3
"""
BuySignalEngine — Pine PRZ/PA/VSA adapted V4 BUY setup.

BUY does not require H1/H4 full bullish trend for scalp.
Small-trend reaction comes first; BOS later promotes the setup to V5 journey.
"""
from __future__ import annotations

import os
from typing import Optional
import pandas as pd
from core.contracts.base_engine import BaseEngine
from engine_v4.session_gate import GateResult
from engine_v4.strategy_policy import (
    BASELINE_DEFAULT,
    baseline_buy_setup,
    confirmed_harmonic_d_override,
    strategy_profile,
)
from session_clock import SessionState


class BuySignalEngine(BaseEngine):
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
        profile = strategy_profile()
        location_setup = bool(row.get("V4_Buy_Setup", False))
        # The historical BUY baseline was weak in the frozen replay. Require
        # its trend/sweep permission and the current Pine PRZ setup together.
        baseline_route = (
            profile == BASELINE_DEFAULT
            and baseline_buy_setup(row)
            and location_setup
        )
        harmonic_reversal = bool(
            profile == BASELINE_DEFAULT
            and location_setup
            and confirmed_harmonic_d_override("BUY", gate_result.reason)
        )

        if profile == BASELINE_DEFAULT:
            if not (baseline_route or harmonic_reversal):
                return None
        # Explicit compatibility profile for controlled A/B comparison.
        elif not location_setup:
            return None

        # Do not buy into active upper rejection.
        if bool(row.get("V4_Block_Buy_At_Upper", False)):
            return None

        entry = float(row["close"])
        atr = float(row.get("ATR14", 0.0) or 0.0)
        if entry <= 0 or atr <= 0:
            return None

        micro_low = float(row.get("Micro_Lot0_Low", row.get("low", entry)) or row.get("low", entry))
        prz_low = float(row.get("Pine_PRZ_Support_Low", micro_low) or micro_low)
        bb_prz_confluence = bool(row.get("BB_PRZ_Support_Confluence", False))
        deep_reclaim = bool(row.get("Deep_Buy_Reclaim_Trigger", False))
        deep_wall_low = float(row.get("Deep_Buy_Wall_Low", 0.0) or 0.0)
        pinbar_break = bool(row.get("Zone_Buy_Pinbar_Trigger", False))
        pinbar_wall_low = float(row.get("Zone_Buy_Wall_Low", 0.0) or 0.0)

        # The default route keeps the historical 1.5 ATR stop that produced the
        # stable SELL baseline. Confirmed harmonic-D reversals retain the local
        # PRZ wall stop because the pattern invalidation level is explicit.
        if baseline_route and not harmonic_reversal:
            sl_anchor = entry - atr * 1.5
            sl = sl_anchor
        elif deep_reclaim and deep_wall_low > 0:
            sl_anchor = deep_wall_low
            sl = sl_anchor - max(atr * 0.12, entry * 0.00015)
        elif pinbar_break and pinbar_wall_low > 0:
            sl_anchor = pinbar_wall_low
            sl = sl_anchor - max(atr * 0.12, entry * 0.00015)
        elif bb_prz_confluence or bool(row.get("V4_Buy_Entry_Zone", False)):
            sl_anchor = min(float(row["low"]), micro_low)
            sl = sl_anchor - max(atr * 0.12, entry * 0.00015)
        else:
            sl_anchor = min(float(row["low"]), micro_low, prz_low)
            sl = sl_anchor - atr * 0.25

        # V4 cashflow path: lower → mid → upper. EA gets final TP.
        tp1 = float(row.get("BB_Mid", 0.0) or 0.0)
        tp = float(row.get("BB_Upper", 0.0) or 0.0)
        if tp <= entry and tp1 > entry:
            tp = tp1
        if not (sl < entry < tp):
            return None

        risk = entry - sl
        reward = tp - entry
        rr = reward / risk if risk > 0 else 0.0
        rr_ok = rr >= min_rr

        pine_valid = bool(row.get("Pine_Valid_Buy", False))
        choch = bool(row.get("CHoCH_Bull", False))
        bull_ob = bool(row.get("Bull_OB", False))
        ha_cf = bool(row.get("HA_Green_2_CF", False))

        quality_score = 2
        if pine_valid:
            quality_score += 2
        if choch:
            quality_score += 1
        if bull_ob:
            quality_score += 1
        if ha_cf:
            quality_score += 1
        if deep_reclaim:
            quality_score += 2
        if pinbar_break:
            quality_score += 1
        if bool(row.get("Trend_1H_Up", False)):
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
        if choch:
            basis_parts.append("CHOCH")
        if bull_ob:
            basis_parts.append("BULL_OB")
        if ha_cf:
            basis_parts.append("HA_CF")
        if deep_reclaim:
            basis_parts.append("DEEP_100_RECLAIM")
        if pinbar_break:
            basis_parts.append("PINBAR_HIGH_BREAK")
        if baseline_route:
            basis_parts.append("BASELINE_TREND_SWEEP")
        if harmonic_reversal:
            basis_parts.append("HARMONIC_D_REVERSAL")
        v5_basis = "|".join(basis_parts) if basis_parts else "LOWER_REACTION"

        if harmonic_reversal:
            entry_mode = "HARMONIC_D_PRZ_BUY_OVERRIDE"
        elif baseline_route:
            entry_mode = "BASELINE_BUY_TREND_SWEEP"
        elif deep_reclaim:
            entry_mode = "V4_BUY_DEEP_100_WALL_RECLAIM"
        elif pinbar_break:
            entry_mode = "V4_BUY_KIVANC_PINBAR_BREAK"
        elif bb_prz_confluence:
            entry_mode = "V4_BUY_BB_PRZ_CONFLUENCE"
        else:
            entry_mode = "V4_BUY_PINE_PRZ_VSA"

        return {
            "status": "SIGNAL",
            "direction": "BUY",
            "entry_price": entry,
            "sl_price": sl,
            "tp1_price": tp1,
            "tp2_price": tp,
            "score": quality_score,
            "reason": f"{profile}: {session_state.session} BUY",
            "zone_confluence": bool(bb_prz_confluence or deep_reclaim or pinbar_break),
            "bb_prz_confluence": bb_prz_confluence,
            "v4_entry_zone": bool(row.get("V4_Buy_Entry_Zone", False)),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "tp1": tp1,
            "session": session_state.session,
            "timestamp": row.name,
            "entry_mode": entry_mode,
            "strategy_profile": profile,
            "baseline_default": baseline_route,
            "harmonic_reversal_override": harmonic_reversal,
            "exit_mode": "V4_BB_UPPER",
            "setup_state": "BUY_SETUP" if not choch else "BUY_CF_READY",
            "be_trigger": entry * 1.0015 if baseline_route and not harmonic_reversal else tp1 if tp1 > entry else entry * 1.0015,
            "trail_factor": 0.9995,
            "be_policy": "BASELINE_PROFIT_0_15" if baseline_route and not harmonic_reversal else "BB_MID_OR_PROFIT_0_15",
            "trail_policy": "WIDE_TRAIL_AFTER_BE",
            "max_bars": 40,
            "v5_quality_score": quality_score,
            "v5_quality_grade": quality_grade,
            "v5_basis": v5_basis,
            "session_quality_gate": "HARMONIC_D_PRZ_OVERRIDE" if harmonic_reversal else "BASELINE_H1_EMA_SWEEP" if baseline_route else "DEEP_100_WALL_RECLAIM" if deep_reclaim else "KIVANC_PINBAR_BREAK" if pinbar_break else "PINE_PRZ_SUPPORT_PA_VSA",
            "pine_valid": pine_valid,
            "pa_bull_confirmed": bool(row.get("Pine_PA_Bull_Confirmed", False)),
            "vsa_buy_pressure": float(row.get("VSA_Buy_Pressure", 0.0) or 0.0),
            "vsa_sell_pressure": float(row.get("VSA_Sell_Pressure", 0.0) or 0.0),
            "micro_lot0_low": micro_low,
            "deep_reclaim": deep_reclaim,
            "pinbar_break": pinbar_break,
            "vsa_wall_low": deep_wall_low if deep_reclaim else pinbar_wall_low if pinbar_break else micro_low,
            "vsa_wall_high": float(row.get("Deep_Buy_Wall_High", 0.0) or 0.0) if deep_reclaim else float(row.get("Zone_Buy_Wall_High", 0.0) or 0.0) if pinbar_break else 0.0,
            "kivanc_scenario_state": str(row.get("Kivanc_Scenario_State", "OUTSIDE")),
            "kivanc_zone_low": float(row.get("Kivanc_Buy_Zone_Low", 0.0) or 0.0),
            "kivanc_zone_high": float(row.get("Kivanc_Buy_Zone_High", 0.0) or 0.0),
            "prz_support_low": float(row.get("Pine_PRZ_Support_Low", 0.0) or 0.0),
            "prz_support_high": float(row.get("Pine_PRZ_Support_High", 0.0) or 0.0),
            "entry_rr": rr,
            "entry_to_sl_points": risk,
            "entry_to_tp_points": reward,
            "rr_ok": rr_ok,
            "min_rr": min_rr,
        }
