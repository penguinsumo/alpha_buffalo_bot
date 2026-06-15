# ═══════════════════════════════════════════════
# 🐃 LAYER 2: INDICATORS (Feature Engineering)
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np

class Indicators:
    """Calculate all technical indicators"""
    
    @staticmethod
    def add_all(df):
        """Add all indicators to DataFrame"""
        # EMAs
        df['EMA20'] = df['Close'].ewm(span=20).mean()
        df['EMA50'] = df['Close'].ewm(span=50).mean()
        
        # Bollinger Bands
        df['BB_Mid'] = df['Close'].rolling(20).mean()
        df['BB_Std'] = df['Close'].rolling(20).std()
        df['BB_Low'] = df['BB_Mid'] - 2 * df['BB_Std']
        df['BB_High'] = df['BB_Mid'] + 2 * df['BB_Std']
        
        # ATR
        df['TR'] = np.maximum(
            df['High'] - df['Low'],
            np.maximum(
                abs(df['High'] - df['Close'].shift()),
                abs(df['Low'] - df['Close'].shift())
            )
        )
        df['ATR14'] = df['TR'].rolling(14).mean()
        df['ATR_EMA'] = df['ATR14'].ewm(span=50).mean()
        
        # RSI
        delta = df['Close'].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = -delta.where(delta < 0, 0).rolling(14).mean()
        df['RSI'] = 100 - (100 / (1 + gain / loss))
        
        # VSA
        df['Spread'] = (df['High'] - df['Low']) / df['Low'] * 100
        df['Avg_Spread'] = df['Spread'].rolling(20).mean()
        df['Body'] = abs(df['Close'] - df['Open']) / df['Low'] * 100
        df['Lower_Wick'] = (df[['Close','Open']].min(axis=1) - df['Low']) / df['Low'] * 100
        df['Upper_Wick'] = (df['High'] - df[['Close','Open']].max(axis=1)) / df['Low'] * 100
        
        
        df["ADX"] = 20
        return df
    
    @staticmethod
    def get_regime(df):
        """Classify market regime"""
        adx = df.get('ADX', 20)
        bb_width = df['BB_High'] - df['BB_Low']
        bb_width_pct = bb_width / df['BB_Mid'] * 100
        
        ema20 = df['EMA20'].iloc[-1]
        ema50 = df['EMA50'].iloc[-1]
        
        if adx > 25 and (ema20 > ema50 or ema20 < ema50):
            return 'TREND'
        elif adx < 20 and bb_width_pct < 0.5:
            return 'CHOP'
        else:
            return 'MEAN_REV'
    
    @staticmethod
    def get_fib_levels(df, lookback=100):
        if len(df) < lookback:
            return None
        window = df.iloc[-lookback:]
        sw_high = window["High"].iloc[:-1].max() if len(window) > 1 else window["High"].max()
        sw_low = window["Low"].iloc[:-1].min() if len(window) > 1 else window["Low"].min()
        f_range = sw_high - sw_low
        if f_range <= 0:
            return None
        return {
            "swing_high": sw_high,
            "swing_low": sw_low,
            "fib_618": sw_high - f_range * 0.618,
            "fib_786": sw_high - f_range * 0.786,
            "in_golden_zone": (sw_high - f_range * 0.786) <= df["Close"].iloc[-1] <= (sw_high - f_range * 0.618)
        }
