#!/usr/bin/env python3
"""Visual TP – แยก Buy/Sell per Session (Twelve Data 15m)"""
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

end_date = datetime(2026, 6, 17)
start_date = end_date - timedelta(days=60)
url = "https://api.twelvedata.com/time_series"
params = {
    'symbol': 'XAU/USD', 'interval': '15min',
    'start_date': start_date.strftime('%Y-%m-%d'),
    'end_date': end_date.strftime('%Y-%m-%d'),
    'outputsize': 5000, 'apikey': API_KEY
}
r = requests.get(url, params=params)
data = r.json()
if 'values' not in data:
    print(f"❌ API error: {data.get('message', data)}")
    exit()
df = pd.DataFrame(data['values'])
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.set_index('datetime').sort_index()
for col in ['open','high','low','close']:
    df[col] = pd.to_numeric(df[col])
df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
print(f"✅ Twelve Data 15m: {len(df)} candles")

# Indicators
df['BB_Mid'] = df['Close'].rolling(20).mean()
df['BB_Std'] = df['Close'].rolling(20).std()
df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
h,l,c = df['High'], df['Low'], df['Close'].shift(1)
tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
df['ATR14'] = tr.rolling(14).mean()
df['EMA20'] = df['Close'].ewm(span=20).mean()
df['EMA50'] = df['Close'].ewm(span=50).mean()
df['Low_Prev'] = df['Low'].shift(1); df['High_Prev'] = df['High'].shift(1)
df['Bull_Sweep'] = (df['Low'] < df['Low_Prev']) & (df['Close'] > df['Low_Prev'])
df['Bear_Sweep'] = (df['High'] > df['High_Prev']) & (df['Close'] < df['High_Prev'])
df1h = df.resample('1h').agg({'High':'max','Low':'min'}).dropna()
if len(df1h) >= 5:
    sw_high = df1h['High'].rolling(5).max()
    sw_low = df1h['Low'].rolling(5).min()
    sw_high = sw_high.reindex(df.index, method='ffill')
    sw_low = sw_low.reindex(df.index, method='ffill')
else:
    sw_high = df['High'].rolling(100).max()
    sw_low = df['Low'].rolling(100).min()
df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
df['Diff'] = df['Swing_H'] - df['Swing_L']
df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
df['PRZ_Next'] = df['Swing_L']
df = df.dropna()

def get_session(ts):
    hour = ts.hour
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    return 'OTHER'

# Generate trades (Visual TP for Sell, New V4 for Buy)
trades = []
for i in range(20, len(df)-40):
    row = df.iloc[i]; ts = row.name
    if not (1 <= ts.hour < 19): continue
    # BUY
    if (row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
        gl = row['Swing_H'] - row['Diff']*1.0
        gh = row['Swing_H'] - row['Diff']*0.5
        if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
            entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
            be_act=False; highest=entry; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; h, l = r['High'], r['Low']
                if h>highest: highest=h
                if not be_act and highest >= entry * 1.0015: be_act=True; sl=entry
                if be_act: sl = max(sl, highest*0.9995)
                if h >= tp: exit_price=tp; break
                if l <= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            trades.append({'session':get_session(ts),'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    # SELL (Visual TP)
    if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98):
        entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
        mid_crossed=False
        tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; h, l = r['High'], r['Low']
            if not mid_crossed and l <= r['BB_Mid']: mid_crossed=True; sl=entry
            if l <= tp: exit_price=tp; break
            if h >= sl: exit_price=sl; break
        else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
        trades.append({'session':get_session(ts),'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':ts})

# Simulation per group
def simulate_group(trds, initial=10000):
    trds = sorted(trds, key=lambda x: x['time'])
    equity=initial; curve=[initial]; result=[]
    daily_start=initial; cur_day=None; consec_loss=0; stop_day=False; stopped=0
    for t in trds:
        day = t['time'].date()
        if day != cur_day:
            cur_day=day; daily_start=equity; consec_loss=0; stop_day=False
        if stop_day: continue
        sl_dist = abs(t['entry']-t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = equity * 0.01
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, 10.0); contracts = max(contracts, 0.01)
        pnl_pts = (t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        equity += pnl_dollar
        if pnl_dollar <= 0: consec_loss += 1
        else: consec_loss = 0
        daily_dd = (daily_start - equity) / daily_start
        if daily_dd >= 0.03 or consec_loss >= 5:
            stop_day=True; stopped+=1
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t, 'pnl_$':pnl_dollar})
    final_eq = curve[-1]; ret = (final_eq/initial - 1)*100
    peak=initial; max_dd=0
    for eq in curve:
        if eq>peak: peak=eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        if dd>max_dd: max_dd=dd
    wins = [x for x in result if x['pnl_$']>0]
    total = len(result); wr = len(wins)/total*100 if total else 0
    gross_profit = sum(x['pnl_$'] for x in wins)
    gross_loss = abs(sum(x['pnl_$'] for x in result if x['pnl_$']<0))
    pf = gross_profit/gross_loss if gross_loss>0 else float('inf')
    return {'trades':total,'wr':wr,'return':ret,'dd':max_dd,'pf':pf,'stopped':stopped,'final':final_eq}

# Group by session and direction
sessions = ['ASIA','LONDON','NY']
print("\n📊 VISUAL TP — BUY vs SELL (Twelve Data 15m, 60d)")
print("="*90)
print(f"{'Session':<8} {'Dir':<6} {'Trades':<8} {'Win Rate':<10} {'Return':<12} {'Max DD':<10} {'PF':<8}")
print("-"*90)
for ses in sessions:
    for d in ['BUY','SELL']:
        subset = [t for t in trades if t['session']==ses and t['dir']==d]
        if not subset: continue
        st = simulate_group(subset)
        print(f"{ses:<8} {d:<6} {st['trades']:<8} {st['wr']:<10.2f}% {st['return']:<12.2f}% {st['dd']:<10.2f}% {st['pf']:<8.2f}")
print("="*90)
