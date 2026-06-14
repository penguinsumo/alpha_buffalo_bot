# ============================================================
# 🆕 JPN225 CONFIG — Mean Reversion (Pure Technical)
# ============================================================
# Performance: +5.85% PnL, PF 1.49, Max DD -1.57%, WR 53.8%
# ============================================================

JPN225_CONFIG = {
    # ━━━ Time Filter ━━━
    "BLOCKED_HOURS": [3],                     # Tokyo Lunch Break (12:00 JST)
    "TRADING_HOURS": [0, 1, 2, 4, 5, 6],     # Tokyo Session (23-06 UTC)
    "GOLDEN_HOURS": [4, 5],                   # Best PnL hours (Tokyo PM)
    
    # ━━━ Scoring ━━━
    "ATR_MULTIPLIER": 1.5,
    "SPREAD_MULTIPLIER": 1.15,
    
    # ━━━ SCALP_BE (Primary — Mean Reversion King) ━━━
    "SCALP_BE_CONFIG": {
        "BE_TRIGGER_PCT": 0.0010,             # 0.10% — Fast BE
        "TRAIL_DISTANCE_PCT": 0.0005,         # 0.05% — Tight trail
        "TP_PCT": 0.0020,                     # 0.20%
        "SL_PCT": 0.0012,                     # 0.12%
        "TIMEOUT_CANDLES": 12,                # 3 hours
    },
    
    # ━━━ V4_SCALP ━━━
    "V4_SCALP_CONFIG": {
        "TP_PCT": 0.0015,                     # 0.15%
        "SL_PCT": 0.0012,                     # 0.12%
    },
    
    # ━━━ V5_SNIPER (Tight TP — Nikkei overextends) ━━━
    "V5_SNIPER_CONFIG": {
        "TP_PCT": 0.0020,                     # 0.20% (tighter than XAUUSD)
        "SL_PCT": 0.0015,                     # 0.15%
        "MAX_SCORE": 8,                       # Block Score 8+ (overextended)
    },
    
    # ━━━ Session Scoring ━━━
    "SESSION_SCORE": {
        0: 2.0,   # Tokyo Open
        1: 2.0,
        2: 2.0,
        3: 0.0,   # Lunch — BLOCKED
        4: 2.5,   # Golden Hour (Tokyo PM)
        5: 2.5,   # Golden Hour (Tokyo PM)
        6: 2.0,   # Pre-Close
    },
    
    # ━━━ No Macro Filter (JPN225 is independent) ━━━
    "MACRO_FILTER": {
        "enabled": False,                     # DXY/US10Y not correlated
        "note": "Nikkei driven by BOJ/JPY — Pure Technical works best"
    },
}

def get_jpn225_config():
    return JPN225_CONFIG
