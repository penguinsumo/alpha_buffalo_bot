import os, pandas as pd, requests, logging
from typing import Tuple, Optional

logger = logging.getLogger(__name__)
API_KEY = os.getenv("TWELVEDATA_API_KEY", "")

def _fetch(symbol: str, interval: str, count: int = 200) -> Optional[pd.DataFrame]:
    if not API_KEY:
        logger.error("TWELVEDATA_API_KEY not set")
        return None
    interval_map = {"15m": "15min", "1h": "1h", "4h": "4h"}
    params = {
        "symbol": symbol,
        "interval": interval_map.get(interval, "15min"),
        "outputsize": count,
        "apikey": API_KEY
    }
    try:
        resp = requests.get("https://api.twelvedata.com/time_series", params=params, timeout=10)
        data = resp.json()
        if data.get("status") == "error":
            logger.error(f"Twelve Data error: {data.get('message')}")
            return None
        values = data.get("values", [])
        if not values:
            return None
        df = pd.DataFrame(values)
        for col in ["open","high","low","close"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df["volume"] = pd.to_numeric(df.get("volume", 0), errors="coerce")
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.set_index("datetime", inplace=True)
        df.sort_index(inplace=True)
        return df[["open","high","low","close","volume"]]
    except Exception as e:
        logger.error(f"Failed to fetch Twelve Data: {e}")
        return None

def fetch_market_data(symbol: str = "XAUUSD") -> Tuple[Optional[pd.DataFrame], Optional[pd.DataFrame], Optional[pd.DataFrame]]:
    return _fetch(symbol, "15m"), _fetch(symbol, "1h"), _fetch(symbol, "4h")
