#!/usr/bin/env python3
"""Advanced Hourly Breakdown v3 — Fixed Consistency, Sorted by PnL & Stability"""
import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
from collections import defaultdict
warnings.filterwarnings('ignore')

env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end = datetime(2026, 6, 17); start = end - timedelta(days=60)
def fetch(symbol, interval):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol':symbol,'interval':interval,'start_date':start.strftime('%Y-%m-%d'),
              'end_date':end.strftime('%Y-%m-%d'),'outputsize':5000,'apikey':API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data: raise RuntimeError(data.get('message',''))
    df = pd.DataFrame(data['values']); df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']: df[c] = pd.to_numeric(df[c])
    if 'volume' in df.columns: df['Volume'] = pd.to_numeric(df['volume'])
    else: df['Volume'] = np.random.randint(100, 500, len(df))
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

df_15m = fetch('XAU/USD', '15min')
df_1h  = fetch('XAU/USD', '1h')
df_1h_swing = df_1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()

df = df_15m.copy()
df['BB_Mid'] = df['Close'].rolling(20).mean()
df['BB_Std'] = df['Close'].rolling(20).std()
df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
h,l,c = df['High'], df['Low'], df['Close'].shift(1)
tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
df['ATR14'] = tr.rolling(14).mean()
df['EMA20'] = df['Close'].ewm(span=20).mean(); df['EMA50'] = df['Close'].ewm(span=50).mean()
df['Low_Prev'] = df['Low'].shift(1); df['High_Prev'] = df['High'].shift(1)
df['Bull_Sweep'] = (df['Low'] < df['Low_Prev']) & (df['Close'] > df['Low_Prev'])
df['Bear_Sweep'] = (df['High'] > df['High_Prev']) & (df['Close'] < df['High_Prev'])
sw_high = df_1h_swing['High'].rolling(5).max().reindex(df.index, method='ffill')
sw_low = df_1h_swing['Low'].rolling(5).min().reindex(df.index, method='ffill')
df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
df['Diff'] = df['Swing_H'] - df['Swing_L']
df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
df['PRZ_Next'] = df['Swing_L']
df['Vol_MA'] = df['Volume'].rolling(20).mean()
df = df.dropna()

trades = []
for i in range(20, len(df)-40):
    row = df.iloc[i]; ts = row.name; h = ts.hour
    if h < 1 or h >= 19: continue
    session = 'ASIA' if h<8 else 'LONDON' if h<13 else 'NY'
    # BUY (NY only)
    if session == 'NY' and row['EMA20']>row['EMA50'] and row['Diff']>0:
        gl = row['Swing_H'] - row['Diff']*1.0; gh = row['Swing_H'] - row['Diff']*0.5
        if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
            entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
            be_act=False; highest=entry; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if hh>highest: highest=hh
                if not be_act and highest >= entry * 1.0015: be_act=True; sl=entry
                if be_act: sl = max(sl, highest*0.9995)
                if hh >= tp: exit_price=tp; break
                if ll <= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            trades.append({'time':ts, 'hour':h, 'pnl':exit_price-entry, 'dir':'BUY', 'session':session})
    # SELL
    if row['EMA20']<row['EMA50'] and row['Bear_Sweep'] and row['High']>=row['BB_Upper']*0.98:
        entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
        mid_crossed=False
        tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hh, ll = r['High'], r['Low']
            if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
            if ll <= tp: exit_price=tp; break
            if hh >= sl: exit_price=sl; break
        else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
        trades.append({'time':ts, 'hour':h, 'pnl':entry-exit_price, 'dir':'SELL', 'session':session})

df_t = pd.DataFrame(trades).sort_values('time')
print(f"Total Trades: {len(df_t)}")

def rolling_consistency_fixed(pnl_series, window=15, min_samples=5):
    if len(pnl_series) < min_samples:
        return 0.0, 0.0
    rolling_wr = []
    for i in range(window, len(pnl_series)+1):
        sub = pnl_series.iloc[i-window:i]
        wr = (sub > 0).mean()
        rolling_wr.append(wr)
    if not rolling_wr:
        return 0.0, 0.0
    avg_wr = np.mean(rolling_wr)
    std_wr = np.std(rolling_wr)
    cv = std_wr / avg_wr if avg_wr > 0 else 1.0
    consistency = max(0, 1 - cv)
    return avg_wr, consistency

results = []
for h in range(1,19):
    sub = df_t[df_t['hour']==h]
    if len(sub) < 3: continue
    for d in ['BUY','SELL']:
        sub_d = sub[sub['dir']==d]
        if len(sub_d) < 5: continue
        pnl_series = sub_d['pnl']
        avg_wr, cons = rolling_consistency_fixed(pnl_series, window=15, min_samples=5)
        wr = (pnl_series > 0).mean() * 100
        pnl_sum = pnl_series.sum()
        results.append({'hour':h, 'dir':d, 'trades':len(sub_d), 'wr':wr, 'pnl_sum':pnl_sum,
                        'avg_rolling_wr':avg_wr*100, 'consistency':cons, 'session':sub_d['session'].iloc[0]})

print("\n📊 FIXED CONSISTENCY TABLE")
print(f"{'Hour':<6} {'Dir':<6} {'Trades':<8} {'WR':<8} {'PnL':<10} {'Roll WR':<10} {'Cons':<10} {'Session':<10}")
for r in sorted(results, key=lambda x: x['consistency'], reverse=True):
    print(f"{r['hour']:02d}:00  {r['dir']:<6} {r['trades']:<8} {r['wr']:<8.1f}% {r['pnl_sum']:<10.1f} {r['avg_rolling_wr']:<10.1f}% {r['consistency']:<10.3f} {r['session']:<10}")

print("\n📊 TOP 2 CONSISTENT HOURS PER SESSION")
for ses in ['ASIA','LONDON','NY']:
    print(f"\n--- {ses} ---")
    for d in ['BUY','SELL']:
        sub = [r for r in results if r['session']==ses and r['dir']==d]
        if not sub: continue
        top2 = sorted(sub, key=lambda x: x['consistency'], reverse=True)[:2]
        for rank, r in enumerate(top2, 1):
            print(f"  {d} #{rank}: UTC {r['hour']:02d}:00 | Trades:{r['trades']} WR:{r['wr']:.1f}% PnL:{r['pnl_sum']:+.1f} RollWR:{r['avg_rolling_wr']:.1f}% Cons:{r['consistency']:.3f}")
