"""
ASIA_TUNING_v5p3.py — Alpha Buffalo v5.3
รับลูกจาก Orchestrator เพื่อตรวจสอบ Gate การเทรดช่วงตลาดเอเชีย 
และคำนวณ Dynamic Exit (ATR-based)
"""

from datetime import datetime
from typing import Optional

class ASIAScalpTriggerGate:
    @staticmethod
    def verify_sweep(sweep_valid: bool, sweep_is_pdh_pdl: bool, bos_detected: bool) -> bool:
        if sweep_is_pdh_pdl:
            return True
        return sweep_valid and bos_detected

class ASIASessionVSAGate:
    @staticmethod
    def evaluate(vsa_ok: bool, recent_volume: float, volume_ma: float) -> bool:
        return vsa_ok or (recent_volume > (volume_ma * 1.2))

class ASIATuningManager:
    def __init__(self):
        pass

    @staticmethod
    def is_within_safe_time(current_time_utc: datetime) -> bool:
        if current_time_utc.hour > 13:
            return False
        if current_time_utc.hour == 13 and current_time_utc.minute >= 30:
            return False
        return True

    @staticmethod
    def calculate_dynamic_exits(direction: str, entry_price: float, atr_value: float) -> tuple:
        if atr_value <= 0:
            atr_value = entry_price * 0.008
        sl_distance = atr_value * 1.0
        tp_distance = atr_value * 1.5
        if direction == "BUY":
            sl = entry_price - sl_distance
            tp = entry_price + tp_distance
        elif direction == "SELL":
            sl = entry_price + sl_distance
            tp = entry_price - tp_distance
        else:
            raise ValueError(f"Invalid direction: {direction}")
        return round(sl, 5), round(tp, 5)

    def evaluate_asia_entry(
        self,
        direction: str,
        sweep_valid: bool,
        sweep_is_pdh_pdl: bool,
        bos_detected: bool,
        vsa_ok: bool,
        h1_spike: bool,
        recent_volume: float,
        volume_ma: float,
        entry_price: float,
        atr_value: float,
        current_time: datetime,
        session: str,
    ) -> dict:
        result = {'entry_valid': False, 'sl': 0.0, 'tp': 0.0, 'reason': ''}
        if session != "ASIA":
            result['reason'] = 'Not ASIA session'
            return result
        if not self.is_within_safe_time(current_time):
            result['reason'] = 'Outside safe time (Blocked after 13:30 UTC)'
            return result
        if not ASIAScalpTriggerGate.verify_sweep(sweep_valid, sweep_is_pdh_pdl, bos_detected):
            result['reason'] = 'No valid ASIA trigger (Sweep/BOS missing)'
            return result
        if not ASIASessionVSAGate.evaluate(vsa_ok, recent_volume, volume_ma):
            result['reason'] = 'VSA volume too low for ASIA zone'
            return result
        result['entry_valid'] = True
        sl, tp = self.calculate_dynamic_exits(direction, entry_price, atr_value)
        result['sl'] = sl
        result['tp'] = tp
        return result
