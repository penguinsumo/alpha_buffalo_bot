#!/usr/bin/env python3
"""
SignalRouter — Production Version (HA-Filtered Buy Gate)
"""
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
        # ต้องใช้ DatetimeIndex
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("DataFrame must have DatetimeIndex")
        idx = len(df) - 1
        row = df.iloc[idx]
        ts = row.name
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        session_state = self.clock.get(ts)

        signals = []
        # BUY
        gate_buy = self.gate.evaluate(session_state, 'BUY', df=df, idx=idx,
                                      daily_dd_ok=daily_dd_ok, consec_loss_ok=consec_loss_ok)
        signal = self.buy_engine.evaluate(df, idx, session_state, gate_buy)
        if signal:
            signals.append(signal)

        # SELL
        gate_sell = self.gate.evaluate(session_state, 'SELL',
                                        daily_dd_ok=daily_dd_ok, consec_loss_ok=consec_loss_ok)
        signal = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
        if signal:
            signals.append(signal)

        return signals
