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

        # Location-first: lower BB / Pine PRZ support / killzone + PA + VSA.
        if not bool(row.get("V4_Buy_Setup", False)):
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

        # V4 BB+PRZ confluence uses local sweep/reaction low for SL.
        # Do not use the far side of the whole PRZ for scalp SL; it destroys RR.
        if bb_prz_confluence or bool(row.get("V4_Buy_Entry_Zone", False)):
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
        if bool(row.get("Trend_1H_Up", False)):
            quality_score += 1

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
        v5_basis = "|".join(basis_parts) if basis_parts else "LOWER_REACTION"

        return {
            "status": "SIGNAL",
            "direction": "BUY",
            "entry_price": entry,
            "sl_price": sl,
            "tp1_price": tp1,
            "tp2_price": tp,
            "score": quality_score,
            "reason": f"V4 Engine: {session_state.session} BUY",
            "zone_confluence": bb_prz_confluence,
            "bb_prz_confluence": bb_prz_confluence,
            "v4_entry_zone": bool(row.get("V4_Buy_Entry_Zone", False)),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "tp1": tp1,
            "session": session_state.session,
            "timestamp": row.name,
            "entry_mode": "V4_BUY_BB_PRZ_CONFLUENCE" if bb_prz_confluence else "V4_BUY_PINE_PRZ_VSA",
            "exit_mode": "V4_BB_UPPER",
            "setup_state": "BUY_SETUP" if not choch else "BUY_CF_READY",
            "be_trigger": tp1 if tp1 > entry else entry * 1.0015,
            "trail_factor": 0.9995,
            "be_policy": "BB_MID_OR_PROFIT_0_15",
            "trail_policy": "WIDE_TRAIL_AFTER_BE",
            "max_bars": 40,
            "v5_quality_score": quality_score,
            "v5_quality_grade": quality_grade,
            "v5_basis": v5_basis,
            "session_quality_gate": "PINE_PRZ_SUPPORT_PA_VSA",
            "pine_valid": pine_valid,
            "pa_bull_confirmed": bool(row.get("Pine_PA_Bull_Confirmed", False)),
            "vsa_buy_pressure": float(row.get("VSA_Buy_Pressure", 0.0) or 0.0),
            "vsa_sell_pressure": float(row.get("VSA_Sell_Pressure", 0.0) or 0.0),
            "micro_lot0_low": micro_low,
            "prz_support_low": float(row.get("Pine_PRZ_Support_Low", 0.0) or 0.0),
            "prz_support_high": float(row.get("Pine_PRZ_Support_High", 0.0) or 0.0),
            "entry_rr": rr,
            "entry_to_sl_points": risk,
            "entry_to_tp_points": reward,
            "rr_ok": rr_ok,
            "min_rr": min_rr,
        }
