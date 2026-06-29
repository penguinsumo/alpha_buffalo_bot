#!/usr/bin/env python3
"""
SignalRouter — จัดการ loop แท่ง เรียก gate + engine
"""
from typing import List
import pandas as pd
from session_clock import SessionClock, SessionInfo
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
        """
        รับ DataFrame 15m ที่มี indicator พร้อมแล้ว
        ตรวจสอบเฉพาะแท่งล่าสุด (หรือตามที่เราต้องการ) โดย loop จากท้าย
        """
        signals = []
        # ตรวจสอบเฉพาะแท่งล่าสุด (production) หรือทั้ง history (backtest)
        # สำหรับ production: เริ่มจากแท่งสุดท้าย
        last_idx = len(df) - 1
        idx = last_idx
        row = df.iloc[idx]
        ts = row.name
        utc_hour = ts.hour
        session_info = self.clock.get(ts)

        # BUY
        gate_buy = self.gate.evaluate(session_info, 'BUY', utc_hour,
                                       daily_dd_ok, consec_loss_ok)
        signal = self.buy_engine.evaluate(df, idx, session_info, gate_buy)
        if signal:
            signals.append(signal)

        # SELL
        gate_sell = self.gate.evaluate(session_info, 'SELL', utc_hour,
                                        daily_dd_ok, consec_loss_ok)
        signal = self.sell_engine.evaluate(df, idx, session_info, gate_sell)
        if signal:
            signals.append(signal)

        return signals
