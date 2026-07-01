#!/usr/bin/env python3
"""เปรียบเทียบ BUY vs SELL รายชั่วโมง (1-24 UTC) สำหรับ 15m และ 1H"""
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

end = datetime(2026, 6, 17)
start = end - timedelta(days=60)

def fetch(symbol, interval):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol': symbol, 'interval': interval,
              'start_date': start.strftime('%Y-%m-%d'),
              'end_date': end.strftime('%Y-%m-%d'),
              'outputsize': 5000, 'apikey': API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        raise RuntimeError(data.get('message', 'API error'))
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open','high','low','close']:
        df[col] = pd.to_numeric(df[col])
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

print("Loading 15m and 1H data...")
df15 = fetch('XAU/USD', '15min')
df1h = fetch('XAU/USD', '1h')

def add_indicators(df, df_swing=None):
    df = df.copy()
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
    # Swing for Golden Zone from 1H
    if df_swing is not None:
        sw_high = df_swing['High'].rolling(5).max().reindex(df.index, method='ffill')
        sw_low = df_swing['Low'].rolling(5).min().reindex(df.index, method='ffill')
    else:
        sw_high = df['High'].rolling(100).max()
        sw_low = df['Low'].rolling(100).min()
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = df['Swing_H'] - df['Swing_L']
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    df['PRZ_Next'] = df['Swing_L']
    return df

# For 15m we use 1H resampled Swing, for 1H we use itself
df_1h_swing = df1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()
df15 = add_indicators(df15, df_1h_swing)
df1h = add_indicators(df1h, None)  # 1H will use its own swing

# ── Trade generation ─────────────────────────────
def generate_trades(df, direction, tf='15m'):
    """direction: 'BUY' or 'SELL'"""
    trades = []
    min_bars = 20; max_bars = 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]; ts = row.name; h = ts.hour
        if direction == 'BUY':
            if not (row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
                continue
            if not (row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02):
                continue
            gl = row['Swing_H'] - row['Diff']*1.0
            gh = row['Swing_H'] - row['Diff']*0.5
            if not (gl <= row['Close'] <= gh):
                continue
            entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
            be_act=False; highest=entry; exit_price=entry
            for j in range(i+1, min(i+max_bars, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if hh>highest: highest=hh
                if not be_act and highest >= entry * 1.0015: be_act=True; sl=entry
                if be_act: sl = max(sl, highest*0.9995)
                if hh >= tp: exit_price=tp; break
                if ll <= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+max_bars-1, len(df)-1)]['Close']
            pnl_pts = exit_price - entry
            trades.append({'hour':h, 'pnl':pnl_pts})
        else:  # SELL
            if not (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98):
                continue
            entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
            mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+max_bars, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
                if ll <= tp: exit_price=tp; break
                if hh >= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+max_bars-1, len(df)-1)]['Close']
            pnl_pts = entry - exit_price
            trades.append({'hour':h, 'pnl':pnl_pts})
    return trades

# ── Compute hourly stats ──────────────────────────
def hourly_stats(df, direction, tf_label):
    trades = generate_trades(df, direction, tf_label)
    if not trades: return pd.DataFrame()
    df_t = pd.DataFrame(trades)
    grouped = df_t.groupby('hour').agg(
        trades=('pnl','count'),
        pnl=('pnl','sum')
    ).reset_index()
    grouped['side'] = direction
    grouped['tf'] = tf_label
    return grouped

print("\nGenerating BUY/SELL for 15m and 1H...")
buy_15m = hourly_stats(df15, 'BUY', '15m')
sell_15m = hourly_stats(df15, 'SELL', '15m')
buy_1h = hourly_stats(df1h, 'BUY', '1H')
sell_1h = hourly_stats(df1h, 'SELL', '1H')

# ── Combine and print tables ──────────────────────
def print_table(tf_buy, tf_sell, title):
    print(f"\n📊 {title} — BUY vs SELL PnL (points)")
    print("-" * 50)
    # merge on hour
    merged = pd.merge(tf_buy[['hour','trades','pnl']], tf_sell[['hour','trades','pnl']],
                      on='hour', how='outer', suffixes=('_buy','_sell')).fillna(0)
    for _, row in merged.iterrows():
        h = int(row['hour'])
        buy_pnl = row['pnl_buy']
        sell_pnl = row['pnl_sell']
        buy_tr = int(row['trades_buy'])
        sell_tr = int(row['trades_sell'])
        best = 'BUY' if buy_pnl > sell_pnl else 'SELL' if sell_pnl > buy_pnl else 'TIE'
        print(f"UTC {h:02d}:00 | BUY trades:{buy_tr:<4} PnL:{buy_pnl:+.1f} | SELL trades:{sell_tr:<4} PnL:{sell_pnl:+.1f} → {best}")

print_table(buy_15m, sell_15m, "15-MINUTE TIMEFRAME")
print_table(buy_1h, sell_1h, "1H TIMEFRAME")
