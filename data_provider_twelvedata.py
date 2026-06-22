import os, requests, pandas as pd
from datetime import datetime, timedelta

def _get_api_key():
    env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
    with open(env_path) as f:
        for line in f:
            if line.startswith('TWELVEDATA_API_KEY='):
                return line.strip().split('=', 1)[1]
    return None

def fetch_twelvedata(symbol='XAU/USD', interval='15min', days=60):
    """ดึงข้อมูล OHLCV จาก Twelve Data กลับเป็น DataFrame"""
    apikey = _get_api_key()
    if not apikey:
        raise ValueError("TWELVEDATA_API_KEY not found in .env")

    end = datetime.utcnow()
    start = end - timedelta(days=days)
    url = "https://api.twelvedata.com/time_series"
    params = {
        'symbol': symbol, 'interval': interval,
        'start_date': start.strftime('%Y-%m-%d'),
        'end_date': end.strftime('%Y-%m-%d'),
        'outputsize': 5000, 'apikey': apikey
    }
    resp = requests.get(url, params=params)
    data = resp.json()
    if 'values' not in data:
        raise RuntimeError(f"Twelve Data error: {data.get('message','')}")

    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open','high','low','close']:
        df[col] = pd.to_numeric(df[col])
    df = df.rename(columns={'open':'open','high':'high','low':'low','close':'close'})
    if 'volume' in df.columns:
        df['volume'] = pd.to_numeric(df['volume'], errors='coerce')
    else:
        df['volume'] = 0.0
    return df[['open','high','low','close','volume']]

def fetch_market_data(symbol="XAU/USD", outputsize=100):
    """ดึงข้อมูล 15m, 1h, 4h พร้อมกัน ใช้ fetch_twelvedata ที่มีอยู่"""
    try:
        df_15m = fetch_twelvedata(symbol, '15min', outputsize)
    except Exception as e:
        print(f"Failed to fetch 15m: {e}")
        df_15m = None
    try:
        df_1h = fetch_twelvedata(symbol, '1h', outputsize)
    except Exception as e:
        print(f"Failed to fetch 1h: {e}")
        df_1h = None
    try:
        df_4h = fetch_twelvedata(symbol, '4h', outputsize)
    except Exception as e:
        print(f"Failed to fetch 4h: {e}")
        df_4h = None
    return df_15m, df_1h, df_4h
