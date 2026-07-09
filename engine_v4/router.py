#!/usr/bin/env python3
"""
SignalRouter — V4 location-first router.

Core rule:
- PRZ Support + BB Lower + HA/Pinbar Bullish + VSA_BUY wins + RR >= min = BUY V4 entry
- PRZ Resistance + BB Upper + HA/Pinbar Bearish + VSA_SELL wins + RR >= min = SELL V4 entry
- CHoCH/BOS promotes V4 scalp to V5 journey; it is not required for V4 entry.
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd

from session_clock import SessionClock
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine


class SignalRouter:
    def __init__(
        self,
        clock: SessionClock,
        gate: FinalGate,
        buy_engine: BuySignalEngine,
        sell_engine: SellSignalEngine,
    ):
        self.clock = clock
        self.gate = gate
        self.buy_engine = buy_engine
        self.sell_engine = sell_engine

    def process(
        self,
        df: pd.DataFrame,
        daily_dd_ok: bool = True,
        consec_loss_ok: bool = True,
    ) -> List[dict]:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("DataFrame must have DatetimeIndex")
        if df.empty:
            return []

        # Scan recent closed bars. A setup can form on the BB/PRZ touch candle
        # and still be valid for execution on the next polling cycle.
        lookback_bars = max(1, int(os.getenv("ENGINE_V4_LOOKBACK_BARS", "6")))
        start_idx = max(0, len(df) - lookback_bars)

        signals: List[dict] = []
        latest_idx = len(df) - 1

        for idx in range(start_idx, len(df)):
            row = df.iloc[idx]
            ts = row.name
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.tz_localize("UTC")
            session_state = self.clock.get(ts)
            age_bars = latest_idx - idx

            gate_buy = self.gate.evaluate(
                session_state,
                "BUY",
                df=df,
                idx=idx,
                daily_dd_ok=daily_dd_ok,
                consec_loss_ok=consec_loss_ok,
            )
            buy = self.buy_engine.evaluate(df, idx, session_state, gate_buy)
            if buy:
                self._enrich_signal(buy, idx, age_bars, row)
                signals.append(buy)

            gate_sell = self.gate.evaluate(
                session_state,
                "SELL",
                df=df,
                idx=idx,
                daily_dd_ok=daily_dd_ok,
                consec_loss_ok=consec_loss_ok,
            )
            sell = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
            if sell:
                self._enrich_signal(sell, idx, age_bars, row)
                signals.append(sell)

        if not signals:
            return []

        return [max(signals, key=self._rank)]

    def _enrich_signal(self, sig: dict, idx: int, age_bars: int, row) -> None:
        direction = str(sig.get("direction", "")).upper()
        setup_state = str(sig.get("setup_state") or f"{direction}_SETUP").upper()
        is_cf_ready = setup_state.endswith("CF_READY")

        sig["selected_idx"] = idx
        sig["selected_age_bars"] = age_bars
        sig["scenario_state"] = setup_state
        sig["journey_state"] = (
            f"V5_{direction}_JOURNEY" if is_cf_ready else "V4_SCALP_RANGE"
        )
        sig["vsa_gate"] = (
            "BUY_PRESSURE" if direction == "BUY" else "SELL_PRESSURE"
        )
        sig["bos_confirmed"] = bool(is_cf_ready)
        sig["bb_prz_confluence"] = bool(
            sig.get("bb_prz_confluence") or sig.get("zone_confluence")
        )
        sig["zone_confluence"] = bool(sig.get("bb_prz_confluence"))
        sig["close_at_signal"] = float(row.get("close", 0.0) or 0.0)

    def _rank(self, sig: dict) -> tuple:
        # Location first. Do not add SELL/BUY bias here.
        confluence = 1 if sig.get("bb_prz_confluence") or sig.get("zone_confluence") else 0
        pine_valid = 1 if sig.get("pine_valid") else 0
        cf_ready = 1 if str(sig.get("setup_state", "")).upper().endswith("CF_READY") else 0
        quality = int(sig.get("v5_quality_score", 0) or 0)
        rr = float(sig.get("entry_rr", 0.0) or 0.0)
        age_score = -int(sig.get("selected_age_bars", 99) or 99)
        return (confluence, pine_valid, cf_ready, quality, rr, age_score)
