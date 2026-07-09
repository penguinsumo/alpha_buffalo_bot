#!/usr/bin/env python3
"""
SignalRouter — scenario/VSA gate before BOS.

Root rule:
- Zone + HA/Pinbar + VSA two-side = setup.
- Scenario/VSA gate estimates whether the setup is likely to break.
- BOS is the final confirmation that promotes V4 scalp into V5 journey.
"""
from __future__ import annotations

from typing import List
import pandas as pd
from session_clock import SessionClock
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine


class SignalRouter:
    def __init__(self, clock: SessionClock, gate: FinalGate,
                 buy_engine: BuySignalEngine, sell_engine: SellSignalEngine):
        self.clock = clock
        self.gate = gate
        self.buy_engine = buy_engine
        self.sell_engine = sell_engine

    @staticmethod
    def _b(row: pd.Series, key: str) -> bool:
        return bool(row.get(key, False))

    @staticmethod
    def _f(row: pd.Series, key: str, default: float = 0.0) -> float:
        try:
            return float(row.get(key, default) or default)
        except (TypeError, ValueError):
            return default

    def _scenario_vsa_gate(self, signal: dict, row: pd.Series) -> dict:
        """
        Add pre-BOS scenario state to an already valid V4 setup.

        BUY flow:
        BUY_SETUP -> BUY_CF_READY -> APPROACH_BREAK_ZONE
        -> BREAK_LIKELY / REJECT_LIKELY -> BOS_CONFIRMED/V5_JOURNEY or V4_EXIT/RANGE_RESET

        SELL flow mirrors BUY.
        """
        direction = str(signal.get("direction", "")).upper()
        if direction not in {"BUY", "SELL"}:
            return signal

        close = self._f(row, "close")
        bb_upper = self._f(row, "BB_Upper")
        bb_lower = self._f(row, "BB_Lower")
        atr = self._f(row, "ATR14")
        atr_buffer = atr * 0.25 if atr > 0 else 0.0

        if direction == "BUY":
            vsa_for = self._f(row, "VSA_Buy_Pressure")
            vsa_against = self._f(row, "VSA_Sell_Pressure")
            vsa_wins = self._b(row, "VSA_Buy_Wins")
            vsa_flips_against = self._b(row, "VSA_Sell_Wins")
            cf_ready = self._b(row, "HA_Green_2_CF") or self._b(row, "Bull_OB")
            pa_against = self._b(row, "Pine_PA_Bear_Confirmed") or self._b(row, "Bearish_Pinbar")
            bos_confirmed = self._b(row, "CHoCH_Bull") or self._b(row, "Micro_BOS_Up")
            checkpoint = bb_upper
            approaching_break = bool(checkpoint > 0 and close >= checkpoint * 0.98)
            setup_state = "BUY_CF_READY" if cf_ready else str(signal.get("setup_state", "BUY_SETUP"))
            v5_state = "V5_BUY_JOURNEY"
            break_state = "BOS_UP_CONFIRMED"
        else:
            vsa_for = self._f(row, "VSA_Sell_Pressure")
            vsa_against = self._f(row, "VSA_Buy_Pressure")
            vsa_wins = self._b(row, "VSA_Sell_Wins")
            vsa_flips_against = self._b(row, "VSA_Buy_Wins")
            cf_ready = self._b(row, "HA_Red_2_CF") or self._b(row, "Bear_OB")
            pa_against = self._b(row, "Pine_PA_Bull_Confirmed") or self._b(row, "Bullish_Pinbar")
            bos_confirmed = self._b(row, "CHoCH_Bear") or self._b(row, "Micro_BOS_Down")
            checkpoint = bb_lower
            approaching_break = bool(checkpoint > 0 and close <= checkpoint * 1.02)
            setup_state = "SELL_CF_READY" if cf_ready else str(signal.get("setup_state", "SELL_SETUP"))
            v5_state = "V5_SELL_JOURNEY"
            break_state = "BOS_DOWN_CONFIRMED"

        # Pressure gate before BOS: estimate break/reject at the marked checkpoint.
        pressure_delta = vsa_for - vsa_against
        if bos_confirmed and vsa_wins:
            scenario_state = break_state
            journey_state = v5_state
            trade_management = "PROMOTE_V5_JOURNEY"
            break_prediction = "BOS_CONFIRMED"
        elif approaching_break and vsa_wins and cf_ready and pressure_delta > 0:
            scenario_state = "BREAK_LIKELY"
            journey_state = "V4_SCALP_PENDING_BOS"
            trade_management = "HOLD_V4_PREPARE_V5"
            break_prediction = "BREAK_LIKELY"
        elif approaching_break and (vsa_flips_against or pa_against):
            scenario_state = "REJECT_LIKELY"
            journey_state = "V4_EXIT"
            trade_management = "CLOSE_V4_DO_NOT_PROMOTE_V5"
            break_prediction = "REJECT_LIKELY"
        elif approaching_break:
            scenario_state = "APPROACH_BREAK_ZONE"
            journey_state = "V4_SCALP_CHECKPOINT"
            trade_management = "CHECK_VSA_AT_098_OR_BB_EDGE"
            break_prediction = "WAIT_PRESSURE"
        elif vsa_flips_against and pa_against:
            scenario_state = "RANGE_RESET"
            journey_state = "V4_EXIT"
            trade_management = "EXIT_OR_RESET_SETUP"
            break_prediction = "NO_BOS"
        else:
            scenario_state = setup_state
            journey_state = "V4_SCALP"
            trade_management = "V4_RANGE_MANAGEMENT"
            break_prediction = "WAIT_BOS"

        signal["setup_state"] = setup_state
        signal["scenario_state"] = scenario_state
        signal["journey_state"] = journey_state
        signal["trade_management"] = trade_management
        signal["break_prediction"] = break_prediction
        signal["bos_confirmed"] = bool(bos_confirmed and vsa_wins)
        signal["vsa_gate"] = "PASS" if vsa_wins else "FAIL"
        signal["vsa_pressure_delta"] = float(pressure_delta)
        signal["checkpoint_price"] = float(checkpoint or 0.0)
        signal["approach_break_zone"] = bool(approaching_break)

        # Promote naming only after BOS+VSA. V4 can still scalp without BOS.
        if signal["bos_confirmed"]:
            signal["entry_mode"] = f"{direction}_V5_JOURNEY"
            signal["exit_mode"] = "V5_EXTENSION_OR_NEW_PRZ"
            signal["v5_basis"] = f"{signal.get('v5_basis', '')}|BOS_VSA".strip("|")

        return signal

    def process(self, df: pd.DataFrame, daily_dd_ok: bool = True,
                consec_loss_ok: bool = True) -> List[dict]:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("DataFrame must have DatetimeIndex")

        idx = len(df) - 1
        row = df.iloc[idx]
        ts = row.name
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        session_state = self.clock.get(ts)

        signals: List[dict] = []

        gate_buy = self.gate.evaluate(
            session_state, "BUY", df=df, idx=idx,
            daily_dd_ok=daily_dd_ok, consec_loss_ok=consec_loss_ok,
        )
        buy = self.buy_engine.evaluate(df, idx, session_state, gate_buy)
        if buy:
            signals.append(self._scenario_vsa_gate(buy, row))

        gate_sell = self.gate.evaluate(
            session_state, "SELL", df=df, idx=idx,
            daily_dd_ok=daily_dd_ok, consec_loss_ok=consec_loss_ok,
        )
        sell = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
        if sell:
            signals.append(self._scenario_vsa_gate(sell, row))

        if len(signals) <= 1:
            return signals

        def rank(sig: dict) -> tuple:
            # No SELL bias. Prefer confirmed journey, then break-likely setup, then clean V4 scalp.
            state = str(sig.get("scenario_state", ""))
            state_score = {
                "BOS_UP_CONFIRMED": 5,
                "BOS_DOWN_CONFIRMED": 5,
                "BREAK_LIKELY": 4,
                "APPROACH_BREAK_ZONE": 3,
                "BUY_CF_READY": 2,
                "SELL_CF_READY": 2,
                "BUY_SETUP": 1,
                "SELL_SETUP": 1,
                "REJECT_LIKELY": -1,
                "RANGE_RESET": -2,
            }.get(state, 0)
            pine_valid = 1 if sig.get("pine_valid") else 0
            vsa_gate = 1 if sig.get("vsa_gate") == "PASS" else 0
            quality = int(sig.get("v5_quality_score", 0) or 0)
            rr = float(sig.get("entry_rr", 0.0) or 0.0)
            return (state_score, pine_valid, vsa_gate, quality, rr)

        return [max(signals, key=rank)]
