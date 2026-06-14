
US100_CONFIG = {
    'BLOCKED_HOURS': [20],
    'TRADING_HOURS': [13, 14, 15, 16, 17, 18, 19],
    'ATR_MULTIPLIER': 1.5,
    'SPREAD_MULTIPLIER': 1.15,
    'SCALP_BE_CONFIG': {
        'BE_TRIGGER_PCT': 0.0012,
        'TRAIL_DISTANCE_PCT': 0.0006,
        'TP_PCT': 0.0025,
        'SL_PCT': 0.0012,
        'TIMEOUT_CANDLES': 12,
    },
    'V4_SCALP_CONFIG': {'TP_PCT': 0.0018, 'SL_PCT': 0.0012},
    'V5_SNIPER_CONFIG': {'TP_PCT': 0.0030, 'SL_PCT': 0.0015},
    'VIX_FILTER': {
        'enabled': True,
        'symbol': '^VIX',
        'thresholds': {'BLOCK_BUY': 25.0, 'SELL_BIAS': 22.0, 'BUY_BIAS': 16.0},
        'size_multipliers': {'BLOCK_BUY': 0.0, 'SELL_BIAS': 0.8, 'NEUTRAL': 1.0, 'BUY_BIAS': 1.3}
    }
}
