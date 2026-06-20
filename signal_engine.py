"""
signal_engine.py — Bridge to updated Composer v5.4
"""
import logging
from typing import Optional, Dict, Any
from signal_composer import compose_signal, ComposedSignal

logger = logging.getLogger(__name__)

def get_trade_signal(df_15m, df_1h, df_4h) -> Optional[Dict[str, Any]]:
    sig: Optional[ComposedSignal] = compose_signal(df_4h, df_1h, df_15m)
    if not sig:
        return None
    
    # Map fields to CloudSignal-compatible dict
    return {
        "direction": sig.direction,
        "entry": round(sig.entry_price, 5),
        "sl": round(sig.sl_price, 5),
        "visual_sl": round(sig.visual_sl, 5),       # นี่คือ Visual SL (สำหรับ SELL)
        "tp1": round(sig.tp1_price, 5),
        "tp2": round(sig.tp2_price, 5),
        "score": sig.confluence_score,
        "signal_type": sig.signal_type,
        "turbo_boost": sig.turbo_boost,
        "lot_multiplier": round(sig.lot_multiplier, 2),
        "basket_layer": sig.basket_layer,
        "sources": sig.sources,
        "label": sig.label,
        "timestamp": sig.timestamp,
    }
