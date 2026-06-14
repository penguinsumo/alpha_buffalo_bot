# ============================================================
# 🆕 KOSPI CONFIG — Dynamic Sizing (WR 100% Combos)
# ============================================================
# Performance: +14.68% PnL, Max DD -1.20%, WR 59.0%
# ============================================================

KOSPI_CONFIG = {
    # ━━━ Time Filter ━━━
    "TRADING_HOURS": [0, 1, 2, 3, 4, 5],     # KOSPI 09:00-15:30 KST
    "BLOCKED_HOURS": [],                       # No killer hours!
    
    # ━━━ Scoring ━━━
    "ATR_MULTIPLIER": 1.5,
    "SPREAD_MULTIPLIER": 1.15,
    
    # ━━━ SCALP_BE (Mean Reversion King) ━━━
    "SCALP_BE_CONFIG": {
        "BE_TRIGGER_PCT": 0.0008,             # 0.08% — Fast BE
        "TRAIL_DISTANCE_PCT": 0.0005,         # 0.05% — Tight trail
        "TP_PCT": 0.0025,                     # 0.25% — Wider (WR high)
        "SL_PCT": 0.0012,                     # 0.12%
        "TIMEOUT_CANDLES": 12,                # 3 hours
    },
    
    # ━━━ V4_SCALP ━━━
    "V4_SCALP_CONFIG": {
        "TP_PCT": 0.0015,
        "SL_PCT": 0.0012,
    },
    
    # ━━━ V5_SNIPER ━━━
    "V5_SNIPER_CONFIG": {
        "TP_PCT": 0.0020,
        "SL_PCT": 0.0015,
        "MAX_SCORE": 8,                       # Block Score 8+
    },
    
    # ━━━ DYNAMIC SIZING (The Alpha) ━━━
    "DYNAMIC_SIZE": {
        "enabled": True,
        "rules": [
            {"condition": "hour==4 AND score==3", "multiplier": 3.0, "reason": "WR 100% @ 04:00 UTC"},
            {"condition": "hour==3 AND score==3", "multiplier": 2.0, "reason": "WR 83% @ 03:00 UTC"},
            {"condition": "score==3 AND dow in [0,1,2]", "multiplier": 1.5, "reason": "Mon-Wed high WR"},
            {"condition": "dow==4", "multiplier": 0.5, "reason": "Friday lower WR 47%"},
        ],
        "default": 1.0,
    },
    
    # ━━━ Session Scoring ━━━
    "SESSION_SCORE": {
        0: 2.0,   # Open
        1: 2.0,   # Active
        2: 2.0,   # Active
        3: 2.0,   # Post-Lunch (WR 83%!)
        4: 2.5,   # ⭐ Golden Hour (WR 100% for Score 3)
        5: 2.0,   # Close
    },
    
    # ━━━ No Macro Filter ━━━
    "MACRO_FILTER": {"enabled": False},
}

def get_kospi_config():
    return KOSPI_CONFIG
