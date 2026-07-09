#!/usr/bin/env python3
"""
SignalRouter — location-first conflict resolver.
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
            signals.append(buy)

        gate_sell = self.gate.evaluate(
            session_state, "SELL", df=df, idx=idx,
            daily_dd_ok=daily_dd_ok, consec_loss_ok=consec_loss_ok,
        )
        sell = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
        if sell:
            signals.append(sell)

        if len(signals) <= 1:
            return signals

        def rank(sig: dict) -> tuple:
            # No SELL bias. Pick the cleaner zone setup.
            pine_valid = 1 if sig.get("pine_valid") else 0
            quality = int(sig.get("v5_quality_score", 0) or 0)
            rr = float(sig.get("entry_rr", 0.0) or 0.0)
            state_rank = 1 if str(sig.get("setup_state", "")).endswith("CF_READY") else 0
            return (pine_valid, state_rank, quality, rr)

        return [max(signals, key=rank)]
