# ═══════════════════════════════════════════════
# 🐃 LAYER 3: SIGNALS (Entry Logic)
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np

class SignalEngine:
    """Generate BUY/SELL signals from features"""
    
    def __init__(self, config):
        self.config = config
    
    def bucket_f_score(self, row, fib_data):
        """Calculate Bucket F Score (0-6)"""
        score = 0.0
        
        # BB Touch (1.0)
        if row['Low'] <= row['BB_Low'] * 1.003:
            score += 1.0
        elif row['High'] >= row['BB_High'] * 0.997:
            score += 1.0
        
        # Kivanc Golden Zone (2.0)
        if fib_data and fib_data.get('in_golden_zone', False):
            score += 2.0
        
        # VSA Stopping Volume (2.0)
        high_spread = row['Spread'] > row['Avg_Spread'] * 1.15
        long_wick_buy = row['Lower_Wick'] > row['Body'] * 2 and row['Close'] > row['Open']
        long_wick_sell = row['Upper_Wick'] > row['Body'] * 2 and row['Close'] < row['Open']
        if high_spread and (long_wick_buy or long_wick_sell):
            score += 2.0
        
        # Liquidity Sweep (1.0)
        sweep_range = (row['High'] - row['Low']) >= row['ATR14'] * 0.50
        at_bb_extreme = row['Low'] <= row['BB_Low'] * 1.003 or row['High'] >= row['BB_High'] * 0.997
        if sweep_range and at_bb_extreme:
            score += 1.0
        
        return score
    
    def generate(self, df, fib_data, regime):
        """Generate signals for the latest bar"""
        row = df.iloc[-1]
        score = self.bucket_f_score(row, fib_data)
        
        # Adaptive threshold based on regime
        thresholds = {
            'TREND': self.config.get('score_threshold_trend', 5.0),
            'CHOP': self.config.get('score_threshold_chop', 3.0),
            'MEAN_REV': self.config.get('score_threshold_mean_rev', 4.0)
        }
        threshold = thresholds.get(regime, 4.0)
        
        if score < threshold:
            return None
        
        # Direction
        ema20 = row['EMA20']; ema50 = row['EMA50']
        bb_low = row['BB_Low']; bb_high = row['BB_High']
        
        if row['Low'] <= bb_low * 1.003 and ema20 > ema50:
            direction = 'BUY'
        elif row['High'] >= bb_high * 0.997 and ema20 < ema50:
            direction = 'SELL'
        else:
            return None
        
        return {
            'direction': direction,
            'score': score,
            'threshold': threshold,
            'regime': regime,
            'entry': row['Close'],
            'atr': row['ATR14'],
            'bb_high': bb_high,
            'bb_low': bb_low,
        }