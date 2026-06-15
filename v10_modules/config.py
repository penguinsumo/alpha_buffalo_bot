# ═══════════════════════════════════════════════
# 🐃 v10 CONFIG — All Parameters
# ═══════════════════════════════════════════════
CONFIG = {
    # Risk
    "risk_per_trade": 1.0,
    "max_leverage": 3.0,
    "max_contracts": 10.0,
    "contract_size": 100.0,
    "daily_loss_pct": 3.0,
    "max_consec_loss": 5,
    
    # Exit Ladder
    "be_trigger_pct": 0.0015,
    "trail_pct": 0.0008,
    "sl_pct": 0.0015,
    "use_atr_sl": True,
    "atr_sl_mult": 1.5,
    
    # Indicators
    "bb_len": 20,
    "pivot_lookback": 5,
    "min_atr_pct": 0.10,
    "max_atr_pct": 1.50,
    "min_rr": 1.5,
    
    # Session
    "use_session": True,
    "session_start": 12,
    "session_end": 22,
    
    # Adaptive
    "score_threshold_trend": 4.0,
    "score_threshold_chop": 2.5,
    "score_threshold_mean_rev": 3.0,
    
    # Time
    "max_trade_bars": 30,
    "cooldown_bars": 5,
}