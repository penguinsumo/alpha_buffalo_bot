"""ASIA_TUNING_v5p3 Mock - Temporary"""

class ASIAScalpTriggerGate:
    @staticmethod
    def verify_sweep(micro_sigs):
        return True

class ASITuningManager:
    @staticmethod
    def is_within_safe_time(current_hour_utc):
        return True

    @staticmethod
    def calculate_dynamic_exits(best_dir, current_price, df_15m):
        if best_dir == "buy":
            sl = current_price * 0.998
            tp1 = current_price * 1.005
            tp2 = current_price * 1.008
        else:
            sl = current_price * 1.002
            tp1 = current_price * 0.995
            tp2 = current_price * 0.992
        return sl, tp1, tp2

    def evaluate_asia_entry(self, direction, sweep_valid, sweep_is_pdh_pdl,
                           bos_detected, vsa_ok, h1_spike, recent_volume,
                           volume_ma, entry_price, atr_value, current_time,
                           session="ASIA"):
        """Mock: อนุญาตให้เข้าได้เสมอ"""
        return {
            'entry_valid': True,
            'sl': entry_price - atr_value * 0.5 if direction == 'BUY' else entry_price + atr_value * 0.5,
            'tp': entry_price + atr_value * 1.0 if direction == 'BUY' else entry_price - atr_value * 1.0,
        }

class ASIASessionVSAGate:
    pass

class ASIAScalpLevel:
    pass

class ASIAScalpLevelCalculator:
    pass

class TimeStopMode:
    pass


    def evaluate_asia_entry(self, direction, sweep_valid, sweep_is_pdh_pdl,
                           bos_detected, vsa_ok, h1_spike, recent_volume,
                           volume_ma, entry_price, atr_value, current_time,
                           session="ASIA"):
        """Mock: อนุญาตให้เข้าได้เสมอ"""
        return {
            'entry_valid': True,
            'sl': entry_price - atr_value * 0.5 if direction == "BUY" else entry_price + atr_value * 0.5,
            'tp': entry_price + atr_value * 1.0 if direction == "BUY" else entry_price - atr_value * 1.0,
        }


class ASIASessionTimeStop:
    pass


# ━━━ PHASE 1 PATCH: Session Filter → Scoring ━━━
# Applied: 2026-06-14T09:42:34.975205

def get_session_score(timestamp):
    """🆕 คืนค่าคะแนน session แทนการ block"""
    import pandas as pd
    hour = pd.Timestamp(timestamp).hour
    
    if 7 <= hour <= 10:
        return 2.0  # London Open
    elif 12 <= hour <= 16:
        return 1.5  # NY Open + Overlap
    elif 0 <= hour <= 6:
        return 1.0  # Asia
    return 0.5  # Other

# 🔧 Override: is_in_session() → always True + return score
_is_in_session_original = None
try:
    _is_in_session_original = is_in_session
except NameError:
    pass

def is_in_session(timestamp):
    """🔧 Changed: Always True (no blocking), use get_session_score() for scoring"""
    return True
