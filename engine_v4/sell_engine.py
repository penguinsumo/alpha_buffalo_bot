#!/usr/bin/env python3
"""
SellSignalEngine — V12 Final Logic
"""
from typing import Optional
import pandas as pd
from core.contracts.base_engine import BaseEngine
from engine_v4.session_gate import GateResult
from session_clock import SessionState

class SellSignalEngine(BaseEngine):
    def evaluate(self, df: pd.DataFrame, idx: int,
                 session_state: SessionState,
                 gate_result: GateResult) -> Optional[dict]:
        if not gate_result.allowed:
            return None
        row = df.iloc[idx]
        if row['Trend_1H_Up']:   # ต้องเป็น False
            return None
        if not (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and
                row['high'] >= row['BB_Upper'] * 0.98):
            return None
        entry = row['close']
        sl = entry + row['ATR14'] * 1.5
        tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
        return {
            'direction': 'SELL',
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'session': session_state.session,
            'timestamp': row.name,
            'visual_sl_mid': row['BB_Mid'],
            'max_bars': 40
        }
