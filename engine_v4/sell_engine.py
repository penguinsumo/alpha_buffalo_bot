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

        # V4 SELL BASELINE:
        # - 1H trend down
        # - EMA20 < EMA50
        # - Bear sweep
        # - แตะ Upper BB 0.98-1.00 zone
        #
        # ห้ามบังคับ Sweep_Above_100 ที่นี่
        # เพราะ Sweep_Above_100 เป็น V5 / deep resistance candidate
        bb_touch_factor = 0.98

        if not (
            row['Bear_Sweep']
            and row['high'] >= row['BB_Upper'] * bb_touch_factor
        ):
            return None

        lookback = df.iloc[max(0, idx - 5):idx + 1]

        recent_sweep_above_100 = bool(
            lookback.get('Sweep_Above_100', False).any()
        )
        recent_sell_reclaim = bool(
            lookback.get('Sell_Reclaim', False).any()
        )
        recent_micro_bos_down = bool(
            lookback.get('Micro_BOS_Down', False).any()
        )

        ha_bearish = bool(row.get('HA_Bearish', False))

        # Classify only, do not hard gate.
        # V4 = scalp sell
        # V5 candidate = มี sweep เหนือ 1.00 / structure high
        sell_class = "V5_SELL_CANDIDATE" if recent_sweep_above_100 else "V4_SELL_BASE"

        entry = row['close']
        sl = entry + row['ATR14'] * 1.5

        # SIGNAL_TP เดิม: Fib_072 หรือ PRZ_Next
        signal_tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']

        # V5 / quality short มีสิทธิ์ถือไกล
        v5_exit_qualified = (
            recent_micro_bos_down
            or recent_sweep_above_100
            or recent_sell_reclaim
        )

        if v5_exit_qualified:
            tp = signal_tp
            exit_mode = "V5_SIGNAL_TP"
        else:
            # V4 SELL ใช้ BB Lower เป็น TP หลัก
            tp = row['BB_Lower']
            exit_mode = "V4_BB_LOWER"

        # ASIA/LONDON V4 SELL ต้องมี micro sell confirmation เพิ่ม
        # กันไม้ที่เป็นแค่ BB upper touch แต่ยังไม่มีแรงขายจริง
        if session_state.session in {"ASIA", "LONDON"} and exit_mode == "V4_BB_LOWER":
            v4_session_confirmed = (
                (ha_bearish and row['close'] < row['EMA20'])
                or recent_micro_bos_down
            )

            if not v4_session_confirmed:
                return None
        else:
            v4_session_confirmed = True

        return {
            'direction': 'SELL',
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'session': session_state.session,
            'timestamp': row.name,
            'visual_sl_mid': row['BB_Mid'],
            'entry_mode': sell_class,
            'exit_mode': exit_mode,
            'buy_obstacle_policy': 'BE_ON_BB_MID_CLOSE_ON_HA_BULL_AT_BB_LOWER',
            'signal_tp': signal_tp,
            'bb_lower_tp': row['BB_Lower'],
            'bb_touch_factor': bb_touch_factor,
            'recent_sell_reclaim': recent_sell_reclaim,
            'recent_sweep_above_100': recent_sweep_above_100,
            'recent_micro_bos_down': recent_micro_bos_down,
            'v5_exit_qualified': v5_exit_qualified,
            'v4_session_confirmed': v4_session_confirmed,
            'ha_bearish': ha_bearish,
            'sell_dot_proxy': bool(ha_bearish and row['close'] < row['EMA20']),
            'max_bars': 40
        }
