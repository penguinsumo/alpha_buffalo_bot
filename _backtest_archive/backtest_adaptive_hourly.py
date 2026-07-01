import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
from collections import defaultdict, deque
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
    params = {'symbol':symbol,'interval':interval,'start_date':start.strftime('%Y-%m-%d'),'end_date':end.strftime('%Y-%m-%d'),'outputsize':5000,'apikey':API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data: raise RuntimeError(data.get('message',''))
    df = pd.DataFrame(data['values']); df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']: df[c] = pd.to_numeric(df[c])
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

df15 = fetch('XAU/USD','15min'); df1h = fetch('XAU/USD','1h')
df1h_swing = df1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()

def add_indicators(df, swing_df):
    df = df.copy()
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
    if swing_df is not None:
        sw_high = swing_df['High'].rolling(5).max().reindex(df.index, method='ffill')
        sw_low = swing_df['Low'].rolling(5).min().reindex(df.index, method='ffill')
    else:
        sw_high = df['High'].rolling(100).max(); sw_low = df['Low'].rolling(100).min()
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = df['Swing_H'] - df['Swing_L']
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    df['PRZ_Next'] = df['Swing_L']
    return df

df15 = add_indicators(df15, df1h_swing).dropna()

def generate_signals(df, adaptive=False, stats_obj=None):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name; h = ts.hour
        if h < 1 or h >= 19: continue
        # BUY (only NY)
        if (13 <= h < 19) and (row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
            gl = row['Swing_H'] - row['Diff']*1.0; gh = row['Swing_H'] - row['Diff']*0.5
            if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                if adaptive and stats_obj is not None:
                    wr = stats_obj.wr(h, min_samples=5)
                    th = 0.35 if h < 8 else 0.40
                    if wr < th: continue
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
                pnl = exit_price - entry
                trades.append({'hour':h,'pnl':pnl,'dir':'BUY'})
                if stats_obj is not None: stats_obj.record(h, pnl)
        # SELL (all sessions)
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98):
            if adaptive and stats_obj is not None:
                wr = stats_obj.wr(h, min_samples=5)
                th = 0.35 if h < 8 else 0.40
                if wr < th: continue
            entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
            mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
                if ll <= tp: exit_price=tp; break
                if hh >= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            pnl = entry - exit_price
            trades.append({'hour':h,'pnl':pnl,'dir':'SELL'})
            if stats_obj is not None: stats_obj.record(h, pnl)
    return trades

class HourlyStats:
    def __init__(self, maxlen=60): self.pnls = defaultdict(lambda: deque(maxlen=maxlen))
    def record(self, h, pnl): self.pnls[h].append(pnl)
    def wr(self, h, min_samples=5):
        vals = self.pnls[h]
        if len(vals) < min_samples: return 0.5
        return sum(1 for v in vals if v > 0)/len(vals)

def simulate(trades):
    equity=10000; curve=[10000]; dd=0; peak=10000
    for t in trades:
        equity += t['pnl'] * 0.1  # simplified PnL scaling
        if equity > peak: peak = equity
        dd = max(dd, peak - equity)
        curve.append(equity)
    final = curve[-1]; ret = (final/10000-1)*100
    total_pnl = sum(t['pnl'] for t in trades)
    wins = sum(1 for t in trades if t['pnl']>0)
    wr = wins/len(trades)*100 if trades else 0
    return {'trades':len(trades), 'wr':wr, 'pnl_sum':total_pnl, 'return':ret, 'dd':dd}

print("Running Non-Adaptive...")
trades_non = generate_signals(df15, adaptive=False)
stats_non = simulate(trades_non)

print("Running Adaptive...")
stats_adaptive_obj = HourlyStats()
trades_adapt = generate_signals(df15, adaptive=True, stats_obj=stats_adaptive_obj)
stats_adapt = simulate(trades_adapt)

print("\n📊 COMPARISON (60 days, simplified PnL)")
print(f"{'Metric':<20} {'Non-Adaptive':<15} {'Adaptive':<15}")
print(f"{'Trades':<20} {stats_non['trades']:<15} {stats_adapt['trades']:<15}")
print(f"{'Win Rate':<20} {stats_non['wr']:.1f}%{'':<10} {stats_adapt['wr']:.1f}%")
print(f"{'PnL (pts)':<20} {stats_non['pnl_sum']:<15.1f} {stats_adapt['pnl_sum']:<15.1f}")
print(f"{'Return %':<20} {stats_non['return']:<15.2f} {stats_adapt['return']:<15.2f}")
print(f"{'Max DD %':<20} {stats_non['dd']/100:.2f}%{'':<10} {stats_adapt['dd']/100:.2f}%")
