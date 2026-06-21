import sys, os
sys.path.insert(0, os.path.expanduser('~/alpha_buffalo_bot'))

import requests, pandas as pd, numpy as np, json
from datetime import datetime, timedelta
from core.config.loader import load_env_safely
load_env_safely()

API_KEY = os.getenv("TWELVEDATA_API_KEY")
if not API_KEY:
    print("❌ TWELVEDATA_API_KEY missing")
    exit()

# ── 1. Fetch data for yesterday ──
def fetch(interval, symbol='XAU/USD', date_str=None):
    url = "https://api.twelvedata.com/time_series"
    params = {
        'symbol': symbol, 'interval': interval,
        'start_date': date_str, 'end_date': date_str,
        'outputsize': 5000, 'apikey': API_KEY
    }
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        return None
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c])
    return df

# กำหนดวันที่ต้องการ (default: เมื่อวาน)
target_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
print(f"📡 Scanning market data for {target_date}...")

df1h = fetch('1h', date_str=target_date)
df15m = fetch('15min', date_str=target_date)
if df1h is None or df15m is None:
    print("❌ Failed to fetch data")
    exit()

# ── 2. Calculate MarketMap fields ──
prev_high = float(df1h['high'].max())
prev_low = float(df1h['low'].min())
asian_session = df1h[(df1h.index.hour >= 1) & (df1h.index.hour < 8)]
asian_high = float(asian_session['high'].max()) if not asian_session.empty else prev_high
asian_low = float(asian_session['low'].min()) if not asian_session.empty else prev_low

range_size = prev_high - prev_low
projected_high = prev_high + range_size * 0.2  # 20% extension
projected_low = prev_low - range_size * 0.2

# Bias จากทิศทางของแท่งสุดท้าย
last_close = float(df1h['close'].iloc[-1])
daily_bias = "BULLISH" if last_close > (prev_high + prev_low) / 2 else "BEARISH"

# Liquidity Zones (แบบง่าย: ใช้ High/Low ของเมื่อวาน)
liquidity_zones = [
    {"price": prev_high, "zone_type": "BUY_SIDE", "strength": 0.9},
    {"price": prev_low, "zone_type": "SELL_SIDE", "strength": 0.9}
]

# ── 3. Create NewdayMarketMap ──
from core.models.newday_market_map import NewdayMarketMap, LiquidityZone
market_map = NewdayMarketMap(
    symbol="XAUUSD",
    daily_bias=daily_bias,
    asian_high=asian_high,
    asian_low=asian_low,
    previous_day_high=prev_high,
    previous_day_low=prev_low,
    projected_high=projected_high,
    projected_low=projected_low,
    liquidity_zones=[LiquidityZone(**z) for z in liquidity_zones]
)

# ── 4. Save to JSON ──
output_dir = os.path.expanduser('~/alpha_buffalo_bot/data/market_maps')
os.makedirs(output_dir, exist_ok=True)
output_path = os.path.join(output_dir, f"XAUUSD_{target_date}.json")
with open(output_path, 'w') as f:
    f.write(market_map.model_dump_json(indent=2))

print(f"✅ NewdayMarketMap saved to {output_path}")
print(market_map.model_dump_json(indent=2))
