import pandas as pd
# ═══════════════════════════════════════════════
# 🐃 LAYER 9: ADAPTIVE ENGINE (Meta)
# ═══════════════════════════════════════════════
import numpy as np

class AdaptiveEngine:
    """Self-adjusting parameters based on performance"""
    
    def __init__(self, config):
        self.config = config
        self.regime = 'MEAN_REV'
        self.vol_expanding = False
    
    def update_regime(self, df):
        """Detect market regime from indicators"""
        adx = df.get('ADX', 20)
        if isinstance(adx, pd.Series):
            adx = adx.iloc[-1]
        
        ema20 = df['EMA20'].iloc[-1]
        ema50 = df['EMA50'].iloc[-1]
        bb_width = df['BB_High'].iloc[-1] - df['BB_Low'].iloc[-1]
        bb_width_pct = bb_width / df['BB_Mid'].iloc[-1] * 100
        
        if adx > 25 and ema20 != ema50:
            self.regime = 'TREND'
        elif adx < 20 and bb_width_pct < 0.5:
            self.regime = 'CHOP'
        else:
            self.regime = 'MEAN_REV'
        
        # Volatility clustering
        atr = df['ATR14'].iloc[-1]
        atr_ema = df['ATR_EMA'].iloc[-1]
        self.vol_expanding = atr > atr_ema
        
        return self.regime
    
    def get_score_threshold(self):
        thresholds = {
            'TREND': self.config.get('score_threshold_trend', 5.0),
            'CHOP': self.config.get('score_threshold_chop', 3.0),
            'MEAN_REV': self.config.get('score_threshold_mean_rev', 4.0)
        }
        return thresholds.get(self.regime, 4.0)
    
    def get_adaptive_leverage(self, dd_pct):
        if dd_pct < 2:
            return self.config['max_leverage']
        elif dd_pct < 5:
            return min(self.config['max_leverage'], 2.0)
        else:
            return 1.0
