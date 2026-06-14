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
