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
    
    def get_golden_zone_from_scanner(self, current_price, df_4h=None, df_1h=None):
        """Layer 0: Get Golden Zone from Scenario Scanner (Pre-Market)"""
        try:
            from scenario_scanner import ScenarioScanner
            scanner = ScenarioScanner()
            blueprint = scanner.scan(df_4h, df_1h, None)
            if blueprint and hasattr(blueprint, 'swing_high'):
                f_range = blueprint.swing_high - blueprint.swing_low
                fib_618 = blueprint.swing_high - f_range * 0.618
                fib_786 = blueprint.swing_high - f_range * 0.786
                in_zone = fib_786 <= current_price <= fib_618
                return {
                    'swing_high': blueprint.swing_high,
                    'swing_low': blueprint.swing_low,
                    'fib_618': fib_618, 'fib_786': fib_786,
                    'in_golden_zone': in_zone,
                }
        except ImportError: pass
        except Exception: pass
        return None
    
    def generate_with_scanner(self, df_15m, df_1h, df_4h, regime):
        """Generate signal using Scanner (Layer 0) + v10 Entry Logic"""
        row = df_15m.iloc[-1]
        current_price = row['Close']
        scanner_data = self.get_golden_zone_from_scanner(current_price, df_4h, df_1h)
        score = self.bucket_f_score(row, scanner_data)
        thresholds = {'TREND': 4.0, 'CHOP': 2.5, 'MEAN_REV': 3.0}
        threshold = thresholds.get(regime, 3.0)
        if score < threshold: return None
        ema20 = row['EMA20']; ema50 = row['EMA50']
        ha_buy = row.get('HA_Buy_Signal', False)
        ha_sell = row.get('HA_Sell_Signal', False)
        if row['Low'] <= row['BB_Low'] * 1.02 and ema20 > ema50 and ha_buy:
            direction = 'BUY'
        elif row['High'] >= row['BB_High'] * 0.98 and ema20 < ema50 and ha_sell:
            direction = 'SELL'
        else: return None
        return {
            'direction': direction, 'score': score, 'threshold': threshold,
            'regime': regime, 'entry': current_price,
            'atr': row.get('ATR14', 0),
            'bb_high': row['BB_High'], 'bb_low': row['BB_Low'],
            'scanner_data': scanner_data,
        }
