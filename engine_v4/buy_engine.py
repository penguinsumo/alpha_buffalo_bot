#!/usr/bin/env python3
"""
BuySignalEngine — V12 Final Logic
สืบทอด BaseEngine เพื่อมาตรฐาน
"""
from typing import Optional
import pandas as pd
from core.contracts.base_engine import BaseEngine
from engine_v4.session_gate import GateResult
from session_clock import SessionInfo

class BuySignalEngine(BaseEngine):
    def evaluate(self, df: pd.DataFrame, idx: int,
                 session_info: SessionInfo,
                 gate_result: GateResult) -> Optional[dict]:
        """
        คืน dictionary ของ signal หากเกิดสัญญาณซื้อ
        """
        if not gate_result.allowed:
            return None
        row = df.iloc[idx]
        # 1. Trend Filter
        if not row['Trend_1H_Up']:
            return None
        # 2. Golden Zone
        if row['Diff'] <= 0:
            return None
        gl = row['Swing_H'] - row['Diff'] * 1.0
        gh = row['Swing_H'] - row['Diff'] * 0.5
        if not (gl <= row['close'] <= gh):
            return None
        # 3. Trigger: Bull Sweep + BB Touch
        if not (row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02):
            return None

        entry = row['close']
        sl = entry - row['ATR14'] * 1.5
        tp = row['BB_Upper']

        return {
            'direction': 'BUY',
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'session': session_info.session,
            'timestamp': row.name,
            'be_trigger': entry * 1.0015,
            'trail_factor': 0.9995,
            'max_bars': 40
        }
