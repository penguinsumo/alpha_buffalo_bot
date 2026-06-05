"""
signal_engine.py - Alpha Buffalo v5.2 Signal Processor
Validates real sessions and formats final output for API consumption.
"""

import pandas as pd
from kivanc_vsaob import run_kivanc
from session_clock import get_market_session_info

def validate_session_constraints(current_price, fibo_zone, direction, session):
    """
    Applies Asian (0.618-1.000) and London/NY (0.786-1.000) filtration logic.
    """
    lvl_100 = fibo_zone.levels[1.0]
    lvl_618 = fibo_zone.levels[0.618]
    lvl_786 = fibo_zone.levels[0.786]

    if direction == 'BUY':
        if session == 'ASIA':
            return min(lvl_100, lvl_618) <= current_price <= max(lvl_100, lvl_618)
        elif session in ['LONDON', 'NY', 'LONDON_NY_OVERLAP']:
            return min(lvl_100, lvl_786) <= current_price <= max(lvl_100, lvl_786)
            
    elif direction == 'SELL':
        return min(lvl_100, lvl_786) <= current_price <= max(lvl_100, lvl_786)

    return False

def compute_signal(df_m15, cascade_trend='BUY', has_score_or_pattern=True, context_score=0):
    """
    Core engine. Combines Kivanc Zone, Session Clock, and V4/V5 evaluations.
    Maintains original function name for alpha_buffalo_signal.py dependencies.
    """
    if not has_score_or_pattern:
        return None

    # 1. Run core VSA & Fibo analysis
    sig_obj = run_kivanc(df_m15)
    if not sig_obj:
        return None

    current_price = float(df_m15["close"].iloc[-2])
    
    # 2. Get Real Session
    session_info = get_market_session_info()
    current_session = session_info['session']

    # 3. Validate Entry Depth against current Session
    is_session_valid = validate_session_constraints(
        current_price, 
        sig_obj.fibo_zone, 
        sig_obj.direction, 
        current_session
    )

    if not is_session_valid:
        return None

    # 4. Calculate Final Score (Base VSA Score + Context Score)
    total_score = sig_obj.confluence_score + context_score
    is_v5 = total_score >= 8
    
    # 5. V5 TP Projections (1.618 extension of the swing)
    swing_range = abs(sig_obj.fibo_zone.anchor_high - sig_obj.fibo_zone.anchor_low)
    v5_tp1 = current_price + (swing_range * 0.786) if sig_obj.direction == 'BUY' else current_price - (swing_range * 0.786)
    v5_tp2 = current_price + (swing_range * 1.618) if sig_obj.direction == 'BUY' else current_price - (swing_range * 1.618)

    # 6. Format Raw Signal Data
    return {
        "signal": sig_obj.direction if total_score >= 4 else "NONE",
        "direction": sig_obj.direction,
        "signal_type": "KIVANC_VSA",
        "entry": current_price,
        "sl": sig_obj.sl_price,
        "be_price": current_price,
        "trail_from": current_price,
        "tp_final": v5_tp2 if is_v5 else sig_obj.tp1_price,
        "partial": [],
        "pattern": "VSA_CONFIRMED" if sig_obj.order_block.vsa_confirmed else "NORMAL",
        "score": total_score,
        "layer": 1,
        "session": current_session,
        "session_display": session_info['display_msg'],
        "fallback_sl": sig_obj.sl_price,
        "fallback_tp": sig_obj.tp1_price,
        "visual_sl": round(sig_obj.sl_price, 3),
        "zone_valid": is_session_valid,
        "reentry_ok": True,
        "vsa_bias": sig_obj.order_block.direction,
        "gps_confirmed": True,
        "is_v5": is_v5,
        "v5_tp1": round(v5_tp1, 3),
        "v5_tp2": round(v5_tp2, 3),
        "next_pattern": "",
        "d_point": current_price,
        "prz_low_zone": round(sig_obj.fibo_zone.golden_bot, 3)
    }

def signal_to_dict(computed_signal):
    """
    Transforms computed signal strictly into the SP model structure expected by FastAPI.
    """
    if not computed_signal:
        return {
            "signal": "NONE",
            "direction": "NONE",
            "signal_type": "NONE",
            "entry": 0.0, "sl": 0.0, "be_price": 0.0, 
            "trail_from": 0.0, "tp_final": 0.0,
            "partial": [], "pattern": "", "score": 0, "layer": 0,
            "session": "NONE", "fallback_sl": 0.0, "fallback_tp": 0.0,
            "visual_sl": 0.0, "zone_valid": False, "reentry_ok": False,
            "vsa_bias": "", "gps_confirmed": False,
            "is_v5": False, "v5_tp1": 0.0, "v5_tp2": 0.0,
            "next_pattern": "", "d_point": 0.0, "prz_low_zone": 0.0
        }
    return computed_signal
