#!/usr/bin/env python3
"""
SignalRouter — จัดการ loop แท่ง เรียก gate + engine
"""
from typing import List
import pandas as pd
from session_clock import SessionClock, SessionState
from engine_v4.session_gate import SessionGate, GateResult
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine

class SignalRouter:
    def __init__(self, clock: SessionClock, gate: SessionGate,
                 buy_engine: BuySignalEngine, sell_engine: SellSignalEngine):
        self.clock = clock
        self.gate = gate
        self.buy_engine = buy_engine
        self.sell_engine = sell_engine

    def process(self, df: pd.DataFrame, daily_dd_ok: bool = True,
                consec_loss_ok: bool = True) -> List[dict]:
        # ดูเฉพาะแท่งล่าสุด
        idx = len(df) - 1
        row = df.iloc[idx]
        ts = row.name
        # แก้ timezone ให้เป็น UTC ก่อนส่งเข้า SessionClock
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        session_state = self.clock.get(ts)

        signals = []
        # BUY
        gate_buy = self.gate.evaluate(session_state, 'BUY', daily_dd_ok, consec_loss_ok)
        signal = self.buy_engine.evaluate(df, idx, session_state, gate_buy)
        if signal:
            signals.append(signal)

        # SELL
        gate_sell = self.gate.evaluate(session_state, 'SELL', daily_dd_ok, consec_loss_ok)
        signal = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
        if signal:
            signals.append(signal)

        return signals
