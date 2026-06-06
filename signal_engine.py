# signal_engine.py - Alpha Buffalo v5.2 Signal Processor
# UPDATED: V4 & V5 Unified Entry Zone Logic

import pandas as pd
from kivanc_vsaob import run_kivanc
from session_clock import get_market_session_info
from typing import Dict, Optional, Any

# ── Constants ──────────────────────────────────────────────
ENTRY_ZONE_V4_LOW  = 0.618      # Unified V4 entry: 0.618–1.000
ENTRY_ZONE_V4_HIGH = 1.000

ENTRY_ZONE_V5_LOW  = 0.728      # V5 harmonic PRZ: 0.726–1.000
ENTRY_ZONE_V5_HIGH = 1.000

V4_SCORE_THRESHOLD = 4
V5_SCORE_THRESHOLD = 8


# ── VSA Gate & Volume Confirmation ─────────────────────────
def evaluate_vsa_gate(df_m15: pd.DataFrame, df_h1: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """
    Real-time VSA Gate evaluation.
    Checks M15 BOS/MSS + Volume spike confirmation.
    
    Returns:
        dict: {
            'gate_pass': bool,
            'pressure_type': 'BUYING' | 'SELLING' | 'NEUTRAL',
            'volume_spike': bool,
            'bos_detected': bool,
            'mss_detected': bool,
            'confidence': float (0-1)
        }
    """
    if len(df_m15) < 3:
        return {
            'gate_pass': False,
            'pressure_type': 'NEUTRAL',
            'volume_spike': False,
            'bos_detected': False,
            'mss_detected': False,
            'confidence': 0.0
        }
    
    # ─── Volume Spike Detection (M15) ───
    recent_vol = df_m15["volume"].iloc[-2]
    vol_ma = df_m15["volume"].iloc[-20:-2].mean()
    volume_spike = recent_vol > (vol_ma * 1.5)  # 50% above MA
    
    # ─── BOS / MSS Detection (M15) ───
    prev_high = df_m15["high"].iloc[-3:-1].max()
    prev_low = df_m15["low"].iloc[-3:-1].min()
    curr_close = df_m15["close"].iloc[-2]
    
    bos_detected = curr_close > prev_high  # Break of Structure (upside)
    mss_detected = curr_close < prev_low   # Market Structure Shift (downside)
    
    # ─── Buying/Selling Pressure ───
    if bos_detected and volume_spike:
        pressure_type = 'BUYING'
        confidence = 0.95
    elif mss_detected and volume_spike:
        pressure_type = 'SELLING'
        confidence = 0.95
    elif bos_detected:
        pressure_type = 'BUYING'
        confidence = 0.6
    elif mss_detected:
        pressure_type = 'SELLING'
        confidence = 0.6
    else:
        pressure_type = 'NEUTRAL'
        confidence = 0.0
    
    gate_pass = (bos_detected or mss_detected) and (confidence >= 0.6)
    
    return {
        'gate_pass': gate_pass,
        'pressure_type': pressure_type,
        'volume_spike': volume_spike,
        'bos_detected': bos_detected,
        'mss_detected': mss_detected,
        'confidence': confidence
    }


# ── Unified Entry Zone Validation ──────────────────────────
def validate_unified_entry_zone(current_price: float, fibo_zone: Any, direction: str, score: int, session: str) -> Dict[str, Any]:
    """
    Validates price against unified entry zone.
    
    V4 (Score ≥4): 0.618–1.000 zone
    V5 (Score ≥8): 0.728–1.000 zone (stricter, harmonic PRZ)
    
    Args:
        current_price: Current market price
        fibo_zone: FiboZone object from Kivanc
        direction: 'BUY' or 'SELL'
        score: Confluence score (0-10, includes context)
        session: Current session ('ASIA', 'LONDON', 'NY', etc.)
    
    Returns:
        dict: {
            'is_valid': bool,
            'zone_type': 'V4' | 'V5' | 'NONE',
            'entry_low': float,
            'entry_high': float,
            'prz_activated': bool
        }
    """
    
    lvl_100 = fibo_zone.levels[1.0]
    lvl_786 = fibo_zone.levels[0.786]
    
    # FIX: Corrected malformed ternary operator
    swing_range = fibo_zone.anchor_high - fibo_zone.anchor_low
    if direction == 'SELL':
        lvl_728 = fibo_zone.levels.get(0.728, fibo_zone.anchor_low + swing_range * 0.728)
    else:
        lvl_728 = fibo_zone.levels.get(0.728, fibo_zone.anchor_high - swing_range * 0.728)
    
    lvl_618 = fibo_zone.levels[0.618]
    
    # ─── V5 Activation (Score ≥8, stricter zone) ───
    if score >= V5_SCORE_THRESHOLD:
        # FIX: Different logic for BUY vs SELL
        if direction == 'BUY':
            # BUY: Entry between 0.728 and 1.0
            entry_low = min(lvl_728, lvl_100)
            entry_high = max(lvl_728, lvl_100)
        else:  # SELL
            # SELL: Entry between 0.728 and 1.0 (but on the downside)
            entry_low = min(lvl_100, lvl_728)
            entry_high = max(lvl_100, lvl_728)
        
        is_in_v5_zone = entry_low <= current_price <= entry_high
        
        if is_in_v5_zone:
            return {
                'is_valid': True,
                'zone_type': 'V5',
                'entry_low': round(entry_low, 3),
                'entry_high': round(entry_high, 3),
                'prz_activated': True
            }
    
    # ─── V4 Activation (Score ≥4, standard zone) ───
    if score >= V4_SCORE_THRESHOLD:
        # FIX: Different logic for BUY vs SELL
        if direction == 'BUY':
            # BUY: Entry between 0.618 and 1.0
            entry_low = min(lvl_618, lvl_100)
            entry_high = max(lvl_618, lvl_100)
        else:  # SELL
            # SELL: Entry between 0.618 and 1.0 (but on the downside)
            entry_low = min(lvl_100, lvl_618)
            entry_high = max(lvl_100, lvl_618)
        
        is_in_v4_zone = entry_low <= current_price <= entry_high
        
        if is_in_v4_zone:
            return {
                'is_valid': True,
                'zone_type': 'V4',
                'entry_low': round(entry_low, 3),
                'entry_high': round(entry_high, 3),
                'prz_activated': False
            }
    
    return {
        'is_valid': False,
        'zone_type': 'NONE',
        'entry_low': 0.0,
        'entry_high': 0.0,
        'prz_activated': False
    }


# ── Exit Logic Routing (V4 vs V5) ──────────────────────────
def compute_exit_targets(sig_obj: Any, current_price: float, direction: str, score: int, session_info: Dict[str, Any]) -> Dict[str, Any]:
    """
    Routes exit targets based on V4 or V5 mode.
    
    V4: Short-range exits (recent BOS), BB Mid trailing
    V5: Extended harmonic (0.786 swing extension + 1.618 swing), no trailing
    
    Args:
        sig_obj: KivancSignal from Kivanc
        current_price: Entry price
        direction: 'BUY' or 'SELL'
        score: Total confluence score (VSA included)
        session_info: Session dict from get_market_session_info()
    
    Returns:
        dict with TP/SL/trailing logic per mode
    """
    
    swing_range = abs(sig_obj.fibo_zone.anchor_high - sig_obj.fibo_zone.anchor_low)
    
    if score >= V5_SCORE_THRESHOLD:
        # ─── V5 MODE: Extended Harmonic ───
        # TP1 = 0.786 extension of next swing
        # TP2 = 1.618 extension
        # SL = Breakout of 1.000 level
        # BE = H4 Stoch trigger (external signal expected)
        # NO TRAILING
        
        if direction == 'BUY':
            v5_tp1 = current_price + (swing_range * 0.786)
            v5_tp2 = current_price + (swing_range * 1.618)
            sl_price = sig_obj.fibo_zone.levels[1.0]  # Breakout of 1.000
        else:  # SELL
            v5_tp1 = current_price - (swing_range * 0.786)
            v5_tp2 = current_price - (swing_range * 1.618)
            sl_price = sig_obj.fibo_zone.levels[1.0]
        
        return {
            'mode': 'V5',
            'tp1': round(v5_tp1, 3),
            'tp2': round(v5_tp2, 3),
            'tp_final': round(v5_tp2, 3),
            'sl': round(sl_price, 3),
            'be_trigger': 'H4_STOCH',  # External trigger
            'trailing_stop': 0.0,  # FIX: Use 0.0 instead of None for consistency
            'trailing_mode': 'NONE'
        }
    
    else:
        # ─── V4 MODE: Short-range BOS ───
        # TP = Recent BOS level (use fibo 0.382 from Kivanc as proxy)
        # SL = Breakout of 1.000 level
        # Trailing = BB Mid (external BB calculation)
        
        if direction == 'BUY':
            sl_price = sig_obj.fibo_zone.levels[1.0]
            tp_price = sig_obj.tp1_price  # Use Kivanc TP1
        else:  # SELL
            sl_price = sig_obj.fibo_zone.levels[1.0]
            tp_price = sig_obj.tp1_price
        
        return {
            'mode': 'V4',
            'tp1': round(tp_price, 3),
            'tp2': round(sig_obj.tp2_price, 3),
            'tp_final': round(tp_price, 3),
            'sl': round(sl_price, 3),
            'be_trigger': 'IMMEDIATE',
            'trailing_stop': current_price,  # Start trailing from entry
            'trailing_mode': 'BB_MID'
        }


# ── Main Signal Computation ────────────────────────────────
def compute_signal(df_m15: pd.DataFrame, df_h1: Optional[pd.DataFrame] = None, 
                   context_score: int = 0) -> Optional[Dict[str, Any]]:
    """
    Core engine. Combines Kivanc Zone, Session Clock, VSA Gate, and V4/V5 logic.
    
    NEW: Unified entry zone with dual-cycle routing.
    
    Args:
        df_m15: 15-minute OHLCV DataFrame
        df_h1: 1-hour OHLCV DataFrame (optional, for enhanced VSA)
        context_score: Additional context-based score boost (0-3)
    
    Returns:
        dict with complete signal or None if no valid signal
    """
    
    # Validate input
    if df_m15 is None or len(df_m15) < 3:
        return None

    # 1. Run core VSA & Fibo analysis
    sig_obj = run_kivanc(df_m15)
    if not sig_obj:
        return None

    # FIX: Use iloc[-1] to get current close, not iloc[-2]
    current_price = float(df_m15["close"].iloc[-1])
    
    # 2. Get Real Session
    session_info = get_market_session_info()
    current_session = session_info['session']
    
    # 3. VSA Gate Evaluation (M15 + optional H1)
    vsa_gate = evaluate_vsa_gate(df_m15, df_h1)
    if not vsa_gate['gate_pass']:
        return None
    
    # 4. Calculate Total Score (Kivanc Base + Context + VSA Gate)
    total_score = sig_obj.confluence_score + context_score
    
    # Add VSA confidence to score (max +2)
    if vsa_gate['confidence'] >= 0.9:
        total_score += 2
    elif vsa_gate['confidence'] >= 0.6:
        total_score += 1
    
    # Cap at 10
    total_score = min(total_score, 10)
    
    # 5. Validate Unified Entry Zone (V4 or V5)
    zone_validation = validate_unified_entry_zone(
        current_price,
        sig_obj.fibo_zone,
        sig_obj.direction,
        total_score,
        current_session
    )
    
    if not zone_validation['is_valid']:
        return None
    
    # 6. Route to V4 or V5 Exit Logic
    exit_targets = compute_exit_targets(sig_obj, current_price, sig_obj.direction, total_score, session_info)
    
    # 7. Format Final Signal
    return {
        # ── Core Signal ──
        "signal": sig_obj.direction if total_score >= V4_SCORE_THRESHOLD else "NONE",
        "direction": sig_obj.direction,
        "signal_type": "KIVANC_VSA",
        
        # ── Entry & Exits ──
        "entry": round(current_price, 3),
        "sl": exit_targets['sl'],
        "sl_breakout_level": round(sig_obj.fibo_zone.levels[1.0], 3),
        "tp_final": exit_targets['tp_final'],
        "tp1": exit_targets['tp1'],
        "tp2": exit_targets['tp2'],
        "be_price": current_price,
        "be_trigger": exit_targets['be_trigger'],
        
        # ── Trailing (V4 only) ──
        "trail_from": exit_targets['trailing_stop'],
        "trailing_mode": exit_targets['trailing_mode'],
        
        # ── Zone & Validation ──
        "zone_type": zone_validation['zone_type'],
        "zone_valid": zone_validation['is_valid'],
        "prz_activated": zone_validation['prz_activated'],
        "entry_low": zone_validation['entry_low'],
        "entry_high": zone_validation['entry_high'],
        
        # ── VSA Gate ──
        "vsa_gate_pass": vsa_gate['gate_pass'],
        "pressure_type": vsa_gate['pressure_type'],
        "volume_spike": vsa_gate['volume_spike'],
        "bos_detected": vsa_gate['bos_detected'],
        "mss_detected": vsa_gate['mss_detected'],
        "vsa_confidence": round(vsa_gate['confidence'], 2),
        
        # ── Scoring ──
        "score": total_score,
        "kivanc_base_score": sig_obj.confluence_score,
        "context_score": context_score,
        "is_v5": total_score >= V5_SCORE_THRESHOLD,
        "is_v4": total_score >= V4_SCORE_THRESHOLD,
        "mode": exit_targets['mode'],
        
        # ── Pattern & Status ──
        "pattern": "VSA_CONFIRMED" if sig_obj.order_block.vsa_confirmed else "NORMAL",
        "absorption": sig_obj.order_block.absorption,
        "vsa_bias": sig_obj.order_block.direction,
        "gps_confirmed": True,
        
        # ── Session & Reference ──
        "session": current_session,
        "session_display": session_info['display_msg'],
        
        # ── Order Block Data ──
        "ob_high": round(sig_obj.order_block.ob_high, 3),
        "ob_low": round(sig_obj.order_block.ob_low, 3),
        "ob_mid": round(sig_obj.order_block.ob_mid, 3),
        
        # ── Fibo Levels ──
        "fibo_100": round(sig_obj.fibo_zone.levels[1.0], 3),
        "fibo_786": round(sig_obj.fibo_zone.levels[0.786], 3),
        "fibo_618": round(sig_obj.fibo_zone.levels[0.618], 3),
        "prz_low_zone": round(sig_obj.fibo_zone.golden_bot, 3),
        
        # ── Legacy Fallback ──
        "fallback_sl": sig_obj.sl_price,
        "fallback_tp": sig_obj.tp1_price,
        "partial": [],
        "layer": 1,
        "reentry_ok": True,
        "next_pattern": "",
        "d_point": round(current_price, 3),
        "visual_sl": round(sig_obj.sl_price, 3),
    }


def signal_to_dict(computed_signal: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Transforms computed signal into the SP model structure expected by FastAPI.
    """
    if not computed_signal:
        return {
            "signal": "NONE",
            "direction": "NONE",
            "signal_type": "NONE",
            "entry": 0.0,
            "sl": 0.0,
            "sl_breakout_level": 0.0,
            "be_price": 0.0,
            "be_trigger": "NONE",
            "trail_from": 0.0,
            "trailing_mode": "NONE",
            "tp_final": 0.0,
            "tp1": 0.0,
            "tp2": 0.0,
            "partial": [],
            "pattern": "",
            "score": 0,
            "layer": 0,
            "session": "NONE",
            "fallback_sl": 0.0,
            "fallback_tp": 0.0,
            "visual_sl": 0.0,
            "zone_valid": False,
            "zone_type": "NONE",
            "prz_activated": False,
            "entry_low": 0.0,
            "entry_high": 0.0,
            "reentry_ok": False,
            "vsa_bias": "",
            "gps_confirmed": False,
            "is_v5": False,
            "is_v4": False,
            "mode": "NONE",
            "vsa_gate_pass": False,
            "pressure_type": "NEUTRAL",
            "volume_spike": False,
            "bos_detected": False,
            "mss_detected": False,
            "vsa_confidence": 0.0,
            "next_pattern": "",
            "d_point": 0.0,
            "prz_low_zone": 0.0,
        }
    return computed_signal
