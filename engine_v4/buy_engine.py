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
        deep_reclaim = bool(row.get("Deep_Buy_Reclaim_Trigger", False))
        deep_wall_low = float(row.get("Deep_Buy_Wall_Low", 0.0) or 0.0)
        pinbar_break = bool(row.get("Zone_Buy_Pinbar_Trigger", False))
        pinbar_wall_low = float(row.get("Zone_Buy_Wall_Low", 0.0) or 0.0)
        memory_trigger = bool(row.get("V4_Buy_Memory_Trigger", False))
        memory_wall_low = float(row.get("V4_Buy_Location_Wall", 0.0) or 0.0)
        trigger_source = str(
            row.get("V4_Buy_Trigger_Source", "NONE") or "NONE"
        ).upper()
        sniper_window = df.iloc[max(0, idx - 4): idx + 1]
        sniper_rows = (
            sniper_window.loc[
                sniper_window["V4_Buy_M5_Sniper_Evidence"]
                .fillna(False)
                .astype(bool)
            ]
            if "V4_Buy_M5_Sniper_Evidence" in sniper_window
            else sniper_window.iloc[0:0]
        )
        m5_sniper = bool(
            trigger_source in {
                "M5_SNIPER_RECLAIM",
                "M5_TWO_POINT_RECLAIM",
            }
            or (
                trigger_source == "NONE"
                and memory_trigger
                and not sniper_rows.empty
            )
        )
        sniper_row = sniper_rows.iloc[-1] if m5_sniper else None

        # V4 BB+PRZ confluence uses local sweep/reaction low for SL.
        # Do not use the far side of the whole PRZ for scalp SL; it destroys RR.
        if deep_reclaim and deep_wall_low > 0:
            sl_anchor = deep_wall_low
            sl = sl_anchor - max(atr * 0.12, entry * 0.00015)
        elif pinbar_break and pinbar_wall_low > 0:
            sl_anchor = pinbar_wall_low
            sl = sl_anchor - max(atr * 0.12, entry * 0.00015)
        elif memory_trigger and memory_wall_low > 0:
            sl_anchor = memory_wall_low
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
        if memory_trigger:
            quality_score += min(
                3,
                int(row.get("V4_Buy_Evidence_Score", 0) or 0),
            )
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
        if deep_reclaim:
            basis_parts.append("DEEP_100_RECLAIM")
        if pinbar_break:
            basis_parts.append("PINBAR_HIGH_BREAK")
        if memory_trigger:
            basis_parts.append(
                trigger_source
                if trigger_source != "NONE"
                else "PRZ_MEMORY_TRIGGER"
            )
        if m5_sniper:
            basis_parts.append("M5_SNIPER_KIVANC_BB")
        v5_basis = "|".join(basis_parts) if basis_parts else "LOWER_REACTION"

        if trigger_source == "M15_PRZ_GREEN_DOT":
            entry_mode = "V4_BUY_M15_PRZ_GREEN_DOT"
        elif trigger_source == "M5_TWO_POINT_RECLAIM":
            entry_mode = "V4_BUY_M5_TWO_POINT_RECLAIM"
        elif trigger_source == "M5_SNIPER_RECLAIM":
            entry_mode = "V4_BUY_M5_SNIPER_RECLAIM"
        elif trigger_source == "BULL_PINBAR_HIGH_BREAK":
            entry_mode = "V4_BUY_PINBAR_HIGH_BREAK"
        elif trigger_source == "M15_HA_BULL_FLIP":
            entry_mode = "V4_BUY_M15_HA_FLIP"
        elif deep_reclaim:
            entry_mode = "V4_BUY_DEEP_100_WALL_RECLAIM"
        elif pinbar_break:
            entry_mode = "V4_BUY_KIVANC_PINBAR_BREAK"
        elif memory_trigger and m5_sniper:
            entry_mode = "V4_BUY_M5_SNIPER_PRZ_HA_FLIP"
        elif memory_trigger:
            entry_mode = "V4_BUY_PRZ_MEMORY_HA_FLIP"
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
            "reason": f"V4 Engine: {session_state.session} BUY",
            "zone_confluence": bool(bb_prz_confluence or deep_reclaim or pinbar_break or memory_trigger),
            "bb_prz_confluence": bb_prz_confluence,
            "v4_entry_zone": bool(row.get("V4_Buy_Entry_Zone", False)),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "tp1": tp1,
            "session": session_state.session,
            "timestamp": row.name,
            "entry_mode": entry_mode,
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
            "session_quality_gate": trigger_source if trigger_source != "NONE" else "DEEP_100_WALL_RECLAIM" if deep_reclaim else "KIVANC_PINBAR_BREAK" if pinbar_break else "M5_SNIPER_KIVANC_BB_HA_FLIP" if m5_sniper else "PRZ_MEMORY_EVIDENCE_TRIGGER" if memory_trigger else "PINE_PRZ_SUPPORT_PA_VSA",
            "pine_valid": pine_valid,
            "pa_bull_confirmed": bool(row.get("Pine_PA_Bull_Confirmed", False)),
            "vsa_buy_pressure": float(row.get("VSA_Buy_Pressure", 0.0) or 0.0),
            "vsa_sell_pressure": float(row.get("VSA_Sell_Pressure", 0.0) or 0.0),
            "micro_lot0_low": micro_low,
            "deep_reclaim": deep_reclaim,
            "pinbar_break": pinbar_break,
            "prz_memory_trigger": memory_trigger,
            "trigger_source": trigger_source,
            "m5_sniper": m5_sniper,
            "m5_sniper_move": float(sniper_row.get("V4_Buy_M5_Sniper_Move", 0.0) or 0.0) if sniper_row is not None else 0.0,
            "m5_sniper_kivanc": float(sniper_row.get("V4_Buy_M5_Sniper_Kivanc", 0.0) or 0.0) if sniper_row is not None else 0.0,
            "m5_sniper_bb": float(sniper_row.get("V4_Buy_M5_Sniper_BB", 0.0) or 0.0) if sniper_row is not None else 0.0,
            "m5_sniper_bb_timeframe": str(sniper_row.get("V4_Buy_M5_Sniper_BB_TF", "NONE")) if sniper_row is not None else "NONE",
            "m5_sniper_mode": str(sniper_row.get("V4_Buy_M5_Sniper_Mode", "NONE")) if sniper_row is not None else "NONE",
            "m5_sniper_point_count": int(sniper_row.get("V4_Buy_M5_Sniper_Point_Count", 0) or 0) if sniper_row is not None else 0,
            "prz_evidence_score": int(row.get("V4_Buy_Evidence_Score", 0) or 0),
            "prz_location_age_bars": int(row.get("V4_Buy_Location_Age_Bars", -1) or 0),
            "vsa_wall_low": deep_wall_low if deep_reclaim else pinbar_wall_low if pinbar_break else memory_wall_low if memory_trigger else micro_low,
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
