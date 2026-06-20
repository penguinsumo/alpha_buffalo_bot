#!/usr/bin/env python3
"""
analyze_v2_fibo_bins.py — NEW_V2 Entry Distribution by Golden Fibo Level
ใช้ข้อมูลสังเคราะห์ 90 วัน
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ════════════════════════════════════════════════════════════
# 1. Data (same synthetic as before)
# ════════════════════════════════════════════════════════════
def generate_5min_data(days=90, seed=42):
    np.random.seed(seed)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    dates = pd.date_range(start, end, freq='5min')
    n = len(dates)
    r = np.random.randn(n) * 0.3
    close = 2400 + np.cumsum(r)
    df5 = pd.DataFrame({
        'open': close + np.random.randn(n)*0.1,
        'high': close + abs(np.random.randn(n)*0.6),
        'low': close - abs(np.random.randn(n)*0.6),
        'close': close,
        'volume': np.random.randint(30,150,n)
    }, index=dates)
    df5['high'] = df5[['open','high','close']].max(axis=1)
    df5['low'] = df5[['open','low','close']].min(axis=1)
    return df5

def resample_ohlc(df5):
    df15 = df5.resample('15min').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    df1h = df5.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    return df15, df1h

# ════════════════════════════════════════════════════════════
# 2. Indicators
# ════════════════════════════════════════════════════════════
def add_indicators(df15, df1h):
    df15 = df15.copy()
    df15['BB_Mid'] = df15['close'].rolling(20).mean()
    df15['BB_Std'] = df15['close'].rolling(20).std()
    df15['BB_Lower'] = df15['BB_Mid'] - 2*df15['BB_Std']
    df15['BB_Upper'] = df15['BB_Mid'] + 2*df15['BB_Std']
    h,l,c = df15['high'], df15['low'], df15['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df15['ATR14'] = tr.rolling(14).mean()
    df15['EMA20'] = df15['close'].ewm(span=20).mean()
    df15['EMA50'] = df15['close'].ewm(span=50).mean()
    # Golden Fibo 1H (swing high/low)
    df1h = df1h.copy()
    highs1h = df1h['high'].rolling(5).max()
    lows1h = df1h['low'].rolling(5).min()
    sw_high = highs1h.max()
    sw_low = lows1h.min()
    if sw_high > sw_low:
        diff = sw_high - sw_low
        golden_low = sw_high - diff*0.786
        golden_high = sw_high - diff*0.5
    else:
        golden_low = golden_high = None
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Golden_Low_1H'] = golden_low
    df15['Golden_High_1H'] = golden_high
    # Sweep
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ════════════════════════════════════════════════════════════
# 3. Backtest with Fibo tracking
# ════════════════════════════════════════════════════════════
def backtest_with_fibo(df15):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        # Session filter (NEW_V2 logic)
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
            # Asia must pass Golden Zone + BB touch
            if not (row['Golden_Low_1H'] and row['Golden_High_1H']):
                continue
            if not (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H']):
                continue
            if row['EMA20'] > row['EMA50']:
                if not (row['low'] <= row['BB_Lower']*1.02):
                    continue
            else:
                if not (row['high'] >= row['BB_Upper']*0.98):
                    continue
        # Entry
        direction = entry = sl = tp = None
        fibo_level = None
        if row['EMA20'] > row['EMA50']:   # BUY
            if row['Bull_Sweep']:
                direction = 'BUY'
                entry = row['close']
        else:   # SELL
            if row['Bear_Sweep']:
                direction = 'SELL'
                entry = row['close']
        if direction is None:
            continue

        # Compute fibo retracement level (from swing high)
        if row['Swing_H'] is not None and row['Swing_L'] is not None and row['Swing_H'] != row['Swing_L']:
            fibo_level = (row['Swing_H'] - entry) / (row['Swing_H'] - row['Swing_L'])
            # Clamp to 0.5-0.786 roughly
        else:
            fibo_level = np.nan

        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act = False; hi = entry; lo = entry; pnl = 0.0; exit_reason = 'TIMEOUT'
        first_sl_hit = False
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp:
                    pnl = (tp-entry)/entry*100; exit_reason='TP'; break
                elif l <= sl:
                    if not be_act: first_sl_hit = True
                    pnl = (sl-entry)/entry*100; exit_reason='SL'; break
                if not be_act and h >= entry*1.0015: be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp:
                    pnl = (entry-tp)/entry*100; exit_reason='TP'; break
                elif h >= sl:
                    if not be_act: first_sl_hit = True
                    pnl = (entry-sl)/entry*100; exit_reason='SL'; break
                if not be_act and l <= entry*0.9985: be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100

        rr = abs((tp-entry)/(entry-sl)) if entry != sl else 0
        trades.append({
            'dir': direction,
            'pnl': pnl,
            'exit': exit_reason,
            'first_sl': first_sl_hit,
            'rr': rr,
            'fibo': fibo_level
        })
    return pd.DataFrame(trades)

# ════════════════════════════════════════════════════════════
# 4. Summary by Fibo bins
# ════════════════════════════════════════════════════════════
def summarize_by_fibo_bins(trades_df):
    # Define bins for fibo levels: [0.5-0.618], (0.618-0.72], (0.72-0.78], (0.78-0.86]
    bins = [0.50, 0.618, 0.72, 0.78, 0.86]
    labels = ['0.618', '0.72', '0.78', '0.86']
    trades_df['fibo_bin'] = pd.cut(trades_df['fibo'], bins=bins, labels=labels, right=True)
    
    result = {}
    overall = trades_df
    total = len(overall)
    wins = overall[overall['pnl']>0]
    wr = len(wins)/total*100 if total else 0
    pnl = overall['pnl'].sum()
    # DD calculation
    cum=0; peak=0; dd=0
    for p in overall['pnl']:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    first_sl = overall['first_sl'].sum()
    avg_rr = overall['rr'].mean() if total else 0
    result['Overall'] = {'Total': total, 'Win%': wr, 'PnL%': pnl, 'DD%': dd, 'FirstSL': first_sl, 'AvgRR': avg_rr}
    
    for bin_name in labels:
        sub = trades_df[trades_df['fibo_bin']==bin_name]
        if sub.empty:
            result[bin_name] = {'Total':0,'Win%':0,'PnL%':0,'DD%':0,'FirstSL':0,'AvgRR':0}
            continue
        total_b = len(sub)
        wins_b = sub[sub['pnl']>0]
        wr_b = len(wins_b)/total_b*100
        pnl_b = sub['pnl'].sum()
        cum_b=0; peak_b=0; dd_b=0
        for p in sub['pnl']:
            cum_b+=p
            if cum_b>peak_b: peak_b=cum_b
            if peak_b-cum_b>dd_b: dd_b=peak_b-cum_b
        first_sl_b = sub['first_sl'].sum()
        avg_rr_b = sub['rr'].mean()
        result[bin_name] = {'Total':total_b, 'Win%':wr_b, 'PnL%':pnl_b, 'DD%':dd_b, 'FirstSL':first_sl_b, 'AvgRR':avg_rr_b}
    return result

# ════════════════════════════════════════════════════════════
# Main
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("📡 Generating synthetic 5m data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ Ready. Running backtest...")
    trades_df = backtest_with_fibo(df15)
    print(f"🔹 Total trades: {len(trades_df)}")
    stats = summarize_by_fibo_bins(trades_df)
    
    print("\n📊 Fibo Level Breakdown (NEW_V2)\n")
    print(f"{'Level':<10} {'Trades':<8} {'Win%':<8} {'PnL%':<8} {'DD%':<8} {'FirstSL':<8} {'AvgRR':<8}")
    print("-"*60)
    for lvl in ['Overall','0.618','0.72','0.78','0.86']:
        s = stats[lvl]
        print(f"{lvl:<10} {s['Total']:<8} {s['Win%']:<8.1f} {s['PnL%']:<+8.2f} {s['DD%']:<8.2f} {s['FirstSL']:<8} {s['AvgRR']:<8.2f}")
