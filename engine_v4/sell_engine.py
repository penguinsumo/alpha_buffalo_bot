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
    def run(self, *args, **kwargs):
        """Concrete implementation required by BaseEngine."""
        return self.evaluate(*args, **kwargs)

    def evaluate(self, df: pd.DataFrame, idx: int,
                 session_state: SessionState,
                 gate_result: GateResult) -> Optional[dict]:
        if not gate_result.allowed:
            return None
        row = df.iloc[idx]
        if row['Trend_1H_Up']:   # ต้องเป็น False
            return None

        # Base sell context
        if not (row['EMA20'] < row['EMA50']):
            return None

        # ต้องแตะ/ทะลุ Upper BB ก่อน ถือเป็น setup ไม่ใช่ entry ทันที
        if not (row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98):
            return None

        # รอ sweep/reclaim ล่าสุด แล้วค่อยให้ micro BOS close ลงเป็น trigger
        lookback = df.iloc[max(0, idx - 5):idx + 1]
        recent_sell_reclaim = bool(lookback.get('Sell_Reclaim', False).any())
        recent_sweep_above_100 = bool(lookback.get('Sweep_Above_100', False).any())

        if not recent_sweep_above_100:
            return None

        if not recent_sell_reclaim:
            return None

        if not bool(row.get('HA_Bearish', False)):
            return None

        # BOS ต้องเป็น close break ไม่ใช่ wick
        if not bool(row.get('Micro_BOS_Down', False)):
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
            'entry_mode': 'SELL_MICRO_BOS',
            'recent_sell_reclaim': recent_sell_reclaim,
            'recent_sweep_above_100': recent_sweep_above_100,
            'ha_bearish': bool(row.get('HA_Bearish', False)),
            'micro_bos_down': bool(row.get('Micro_BOS_Down', False)),
            'max_bars': 40
        }
