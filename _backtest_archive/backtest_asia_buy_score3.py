#!/usr/bin/env python3
"""เปรียบเทียบ ASIA BUY Score≥3 กับ Logic เดิม (ปิด BUY)"""
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
    df['Volume'] = np.random.randint(100, 500, len(df))  # Mock volume
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

df_15m = fetch('XAU/USD','15min')
df_1h = fetch('XAU/USD','1h')
df_1h_swing = df_1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()

# Indicators
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
# Simplified Score (0–5) for BUY: BB Touch(1) + Golden Zone(2) + Sweep(1) + VSA mock(1)
df['Score_Buy'] = ((df['Low'] <= df['BB_Lower']*1.02).astype(int) +
                   ((df['Close'] <= df['Swing_H'] - df['Diff']*0.5) & 
                    (df['Close'] >= df['Swing_L'])).astype(int)*2 +
                   df['Bull_Sweep'].astype(int) +
                   (df['Volume'] > df['Vol_MA']).astype(int))
df = df.dropna()

def run_backtest(allow_buy_asia=False):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name; h = ts.hour
        if h < 1 or h >= 19: continue
        session = 'ASIA' if h<8 else 'LONDON' if h<13 else 'NY'
        
        # BUY (Golden Zone + Sweep + BB Touch)
        if row['EMA20'] > row['EMA50'] and row['Diff'] > 0:
            gl = row['Swing_H'] - row['Diff']*1.0; gh = row['Swing_H'] - row['Diff']*0.5
            if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                # BUY gate
                if session == 'NY' or (allow_buy_asia and session == 'ASIA' and row['Score_Buy'] >= 3):
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
                    trades.append({'session':session, 'hour':h, 'dir':'BUY', 'pnl':exit_price-entry})
        
        # SELL (Visual TP)
        if row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98:
            entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
            mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
                if ll <= tp: exit_price=tp; break
                if hh >= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            trades.append({'session':session, 'hour':h, 'dir':'SELL', 'pnl':entry-exit_price})
    return pd.DataFrame(trades)

# ── Run both ──
print("Running original (no ASIA BUY)...")
trades_orig = run_backtest(allow_buy_asia=False)
print("Running with ASIA BUY (Score≥3)...")
trades_new = run_backtest(allow_buy_asia=True)

def summary(df):
    asia = df[df['session']=='ASIA']
    return {
        'total_trades': len(asia),
        'buy_trades': len(asia[asia['dir']=='BUY']),
        'sell_trades': len(asia[asia['dir']=='SELL']),
        'pnl_sum': asia['pnl'].sum(),
        'wr': (asia['pnl']>0).mean()*100 if len(asia) else 0
    }

s_orig = summary(trades_orig)
s_new = summary(trades_new)

print("\n📊 ASIA COMPARISON")
print("="*50)
print(f"{'Metric':<20} {'No BUY':<15} {'BUY (Score≥3)':<15}")
print(f"{'Total Trades':<20} {s_orig['total_trades']:<15} {s_new['total_trades']:<15}")
print(f"{'BUY Trades':<20} {s_orig['buy_trades']:<15} {s_new['buy_trades']:<15}")
print(f"{'SELL Trades':<20} {s_orig['sell_trades']:<15} {s_new['sell_trades']:<15}")
print(f"{'PnL Sum (pts)':<20} {s_orig['pnl_sum']:<15.1f} {s_new['pnl_sum']:<15.1f}")
print(f"{'Win Rate':<20} {s_orig['wr']:<15.1f}% {s_new['wr']:<15.1f}%")
