import yfinance as yf
import pandas as pd
import logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)

def fetch_market_data(symbol: str = "GC=F", period_15m: str = "60d", period_1h: str = "60d", period_4h: str = "60d") -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    """
    ดึงข้อมูล XAUUSD ผ่าน yfinance (GC=F หรือ XAUUSD=X)
    Returns 15m, 1h, 4h DataFrames
    """
    try:
        ticker = yf.Ticker(symbol)
        
        # 15m (max 60 days)
        df_15m = ticker.history(period=period_15m, interval="15m")
        if not df_15m.empty:
            df_15m.index = df_15m.index.tz_localize(None)
        
        # 1h (max 60 days)
        df_1h = ticker.history(period=period_1h, interval="1h")
        if not df_1h.empty:
            df_1h.index = df_1h.index.tz_localize(None)
        
        # 4h (yfinance ไม่มี 4h โดยตรง จึงใช้ 1h แล้ว resample)
        df_4h = ticker.history(period=period_4h, interval="1h")
        if not df_4h.empty:
            df_4h.index = df_4h.index.tz_localize(None)
            df_4h = df_4h.resample("4h").agg({
                "Open": "first",
                "High": "max",
                "Low": "min",
                "Close": "last",
                "Volume": "sum"
            })
        
        # เปลี่ยนชื่อคอลัมน์ให้เป็นตัวเล็ก (ตามที่ engine คาดหวัง)
        for df in [df_15m, df_1h, df_4h]:
            if df is not None and not df.empty:
                df.rename(columns={"Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"}, inplace=True)
                if "volume" not in df.columns:
                    df["volume"] = 0
                # เอาแค่คอลัมน์ที่จำเป็น
                df = df[["open", "high", "low", "close", "volume"]]
        
        return df_15m if not df_15m.empty else None, \
               df_1h if not df_1h.empty else None, \
               df_4h if not df_4h.empty else None
    except Exception as e:
        logger.error(f"yfinance fetch error: {e}")
        return None, None, None
