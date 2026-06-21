import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
warnings.filterwarnings('ignore')

env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end = datetime(2026,6,17)
start = end - timedelta(days=60)

def fetch(interval):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol':'XAU/USD','interval':interval,
              'start_date':start.strftime('%Y-%m-%d'),
              'end_date':end.strftime('%Y-%m-%d'),
              'outputsize':5000,'apikey':API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c])
    return df

df15 = fetch('15min')
df1h = fetch('1h')
df4h = fetch('4h')

from signal_composer import compose_signal

results = []
for i in range(200, len(df15)-1):
    row15 = df15.iloc[:i+1]
    # find corresponding 1h/4h windows
    cur_time = row15.index[-1]
    df1h_win = df1h[df1h.index <= cur_time]
    df4h_win = df4h[df4h.index <= cur_time]
    if df1h_win.empty or df4h_win.empty:
        continue
    sig = compose_signal(df4h_win, df1h_win, row15)
    if sig:
        results.append(sig)

print(f"\n📊 Total signals found: {len(results)}")
if results:
    print("\n🔍 Last 10 signals:")
    for s in results[-10:]:
        print(f"  {s.timestamp} {s.direction} {s.signal_type} entry={s.entry_price:.2f} sl={s.sl_price:.2f} tp1={s.tp1_price:.2f} visual_sl={s.visual_sl:.2f}")
    
    buy_count = sum(1 for s in results if s.direction == "BUY")
    sell_count = sum(1 for s in results if s.direction == "SELL")
    sweep_count = sum(1 for s in results if s.signal_type == "V4_SWEEP")
    print(f"\n📊 Summary: BUY={buy_count}, SELL={sell_count}, V4_SWEEP={sweep_count}")
else:
    print("⚠️ No signals generated. Check session / data range.")
