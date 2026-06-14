"""
signal_engine.py — Alpha Buffalo v5.3 (Execution Bridge)
ส่ง DataFrame ให้ Composer ตัดสินใจ
"""
import logging
from typing import Optional, Dict, Any
from signal_composer import compose_signal, ComposedSignal

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_trade_signal(df_15m, df_1h, df_4h) -> Optional[Dict[str, Any]]:
    sig: Optional[ComposedSignal] = compose_signal(df_4h, df_1h, df_15m)
    if not sig:
        return None
    return {
        "direction": sig.direction,
        "entry": round(sig.entry_price, 5),
        "sl": round(sig.sl_price, 5),
        "tp1": round(sig.tp1_price, 5),
        "tp2": round(sig.tp2_price, 5),
        "lot_multiplier": round(sig.lot_multiplier, 2),
        "basket_layer": sig.basket_layer,
        "score": sig.confluence_score,
        "sources": sig.sources,
        "signal_type": sig.signal_type
    }


# ============================================================
# 🆕 PHASE 2: Signal Engine — Wire trade_mode through pipeline
# ============================================================

def process_signal_with_mode(signal_data: dict) -> dict:
    """
    ประมวลผล signal และเพิ่ม trade_mode
    Pipeline: score → trade_mode → state → trade_execution
    """
    score = signal_data.get('score', 0)
    
    # Get trade mode
    try:
        from signal_composer import _get_trade_mode_for_signal
        trade_mode = _get_trade_mode_for_signal(score)
    except ImportError:
        abs_score = abs(score)
        if abs_score == 3:
            trade_mode = 'SCALP_BE'
        elif 4 <= abs_score <= 5:
            trade_mode = 'V4_SCALP'
        elif abs_score >= 6:
            trade_mode = 'V5_SNIPER'
        else:
            trade_mode = 'NONE'
    
    signal_data['trade_mode'] = trade_mode
    
    # State transition
    if trade_mode == 'SCALP_BE':
        try:
            from state_manager import get_scalp_be_transition
            new_state = get_scalp_be_transition('IDLE', 'SCALP_BE_SIGNAL')
            signal_data['state'] = new_state
        except ImportError:
            signal_data['state'] = 'SCALP_BE_ENTRY'
    
    return signal_data
