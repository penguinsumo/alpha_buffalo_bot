"""
Wrapper สำหรับ signal_composer เดิม เพิ่ม VSA gate และ spike detection
"""
import pandas as pd
from signal_composer import compose_signal as original_compose_signal
from vsa_gate import check_vsa_signal
from micro_engine import detect_spike_15m

def compose_signal_with_vsa(df_4h, df_1h, df_15m, asia_mode=False):
    spike_detected, spike_type = detect_spike_15m(df_15m)
    original_signal = original_compose_signal(df_4h, df_1h, df_15m)
    if original_signal is None:
        return None
    direction = original_signal.direction
    vsa_result = check_vsa_signal(
        df=df_1h,
        direction=direction,
        asia_mode=asia_mode,
        spike_detected=spike_detected,
        lookback=20
    )
    if not vsa_result["ok"]:
        print(f"[VSA Reject] {vsa_result['reason']}")
        return None
    original_signal.vsa_bonus = vsa_result["bonus"]
    original_signal.position_multiplier = vsa_result["position_multiplier"]
    original_signal.spike_detected = spike_detected
    return original_signal
