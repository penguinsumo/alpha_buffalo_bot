"""
signal_engine.py — Alpha Buffalo v11.2 (Bridge with Blueprint support)
"""

import logging
from scenario_scanner import scanner as scenario_scanner
from signal_composer import compose_signal

logger = logging.getLogger(__name__)

def get_trade_signal(df_15m, df_1h, df_4h):
    """
    Bridge function (backward compatible)
    Now generates Blueprint automatically before calling Composer
    """
    # 🆕 Generate Blueprint
    try:
        blueprint = scenario_scanner.scan(df_4h, df_1h, df_15m)
        logger.info(f"Blueprint generated: tunnel_valid={blueprint.tunnel_valid}, market_mode={blueprint.market_mode}")
    except Exception as e:
        logger.warning(f"Blueprint generation failed, proceeding without: {e}")
        blueprint = None

    # Call new Composer with Blueprint
    signal = compose_signal(df_4h, df_1h, df_15m, blueprint=blueprint)
    
    if signal is None:
        return None
    
    # Convert to legacy dict format for any existing consumers
    return {
        "direction": signal.direction,
        "score": signal.confluence_score,
        "entry": signal.entry_price,
        "tp1": signal.tp1_price,
        "tp2": signal.tp2_price,
        "sl": signal.sl_price,
        "signal_type": signal.signal_type,
        "sources": signal.sources
    }
