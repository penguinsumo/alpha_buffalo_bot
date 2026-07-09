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

        # Keep bearish context, but do not let it beat location.
        if bool(row.get("Trend_1H_Up", False)):
            return None
        if not (row.get("EMA20", 0) < row.get("EMA50", 0)):
            return None

        lookback = df.iloc[max(0, idx - 5):idx + 1]
        recent_sweep_above_100 = bool(lookback.get("Sweep_Above_100", False).any())
        recent_sell_reclaim = bool(lookback.get("Sell_Reclaim", False).any())
        recent_micro_bos_down = bool(lookback.get("Micro_BOS_Down", False).any())
        ha_bearish = bool(row.get("HA_Bearish", False))

        upper_setup = bool(row.get("V4_Sell_Setup", False))
        pine_valid = bool(row.get("Pine_Valid_Sell", False))
        sell_continuation = bool(recent_micro_bos_down and row.get("VSA_Sell_Wins", False))

        if not (upper_setup or pine_valid or sell_continuation):
            return None

        entry = float(row["close"])
        atr = float(row.get("ATR14", 0.0) or 0.0)
        if entry <= 0 or atr <= 0:
            return None

        micro_high = float(row.get("Micro_Lot0_High", row.get("high", entry)) or row.get("high", entry))
        prz_high = float(row.get("Pine_PRZ_Resistance_High", micro_high) or micro_high)
        bb_prz_confluence = bool(row.get("BB_PRZ_Resistance_Confluence", False))

        # V4 BB+PRZ confluence uses local sweep/reaction high for SL.
        # Do not use the far side of the whole PRZ for scalp SL; it destroys RR.
        if bb_prz_confluence or bool(row.get("V4_Sell_Entry_Zone", False)):
            sl_anchor = max(float(row["high"]), micro_high)
            sl = sl_anchor + max(atr * 0.12, entry * 0.00015)
        else:
            sl_anchor = max(float(row["high"]), micro_high, prz_high if upper_setup or pine_valid else float(row["high"]))
            sl = sl_anchor + atr * 0.25

        signal_tp = float(row.get("Fib_072", 0.0) or 0.0)
        bb_lower_tp = float(row.get("BB_Lower", 0.0) or 0.0)
        if signal_tp > 0 and signal_tp < entry and (recent_micro_bos_down or recent_sweep_above_100 or recent_sell_reclaim):
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
        if rr < min_rr:
            return None

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
        v5_basis = "|".join(basis_parts) if basis_parts else "UPPER_REJECTION"

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
            "direction": "SELL",
            "zone_confluence": bb_prz_confluence,
            "bb_prz_confluence": bb_prz_confluence,
            "v4_entry_zone": bool(row.get("V4_Sell_Entry_Zone", False)),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "session": session_state.session,
            "timestamp": row.name,
            "visual_sl_mid": row["BB_Mid"],
            "entry_mode": "V4_SELL_BB_PRZ_CONFLUENCE" if bb_prz_confluence else "V4_SELL_PINE_PRZ_VSA" if upper_setup or pine_valid else "V5_SELL_CONTINUATION",
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
            "be_policy": "CURRENT_BBMID_LOW",
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
            "rr_ok": True,
            "min_rr": min_rr,
            "session_quality_gate": "PINE_PRZ_RESISTANCE_PA_VSA" if upper_setup or pine_valid else "BOS_CONTINUATION",
            "sell_dot_reason": "PINE_PRZ_PA_VSA" if pine_valid else "MICRO_BOS_CONTINUATION",
            "pine_valid": pine_valid,
            "pa_bear_confirmed": bool(row.get("Pine_PA_Bear_Confirmed", False)),
            "vsa_buy_pressure": float(row.get("VSA_Buy_Pressure", 0.0) or 0.0),
            "vsa_sell_pressure": float(row.get("VSA_Sell_Pressure", 0.0) or 0.0),
            "micro_lot0_high": micro_high,
            "prz_resistance_low": float(row.get("Pine_PRZ_Resistance_Low", 0.0) or 0.0),
            "prz_resistance_high": float(row.get("Pine_PRZ_Resistance_High", 0.0) or 0.0),
            "max_bars": 40,
        }
