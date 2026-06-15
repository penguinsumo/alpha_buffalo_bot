# ═══════════════════════════════════════════════
# 🐃 LAYER 1: DATA PROVIDER
# ═══════════════════════════════════════════════
import yfinance as yf
import pandas as pd

class DataProvider:
    """Fetch OHLCV from multiple sources"""
    
    @staticmethod
    def from_yfinance(symbol, start, end, interval='1h'):
        df = yf.download(symbol, start=start, end=end, interval=interval, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df.index = pd.to_datetime(df.index).tz_convert(None)
        return df
    
    @staticmethod
    def from_csv(filepath):
        return pd.read_csv(filepath, index_col=0, parse_dates=True)
    
    @staticmethod
    def get_multi_tf(symbol, start, end):
        """Get M15, H1, H4 data"""
        return {
            'M15': DataProvider.from_yfinance(symbol, start, end, '15m'),
            'H1': DataProvider.from_yfinance(symbol, start, end, '1h'),
            'H4': DataProvider.from_yfinance(symbol, start, end, '4h'),
        }