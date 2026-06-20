#!/usr/bin/env python3
"""
backtest_v2_no618_add100.py — NEW_V2 ตัดโซน 0.618 ทิ้ง และเพิ่มโซน 1.00 (0.86–1.00)
เปรียบเทียบกับ NEW_V2 Original
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── Data ──────────────────────────────────────────────
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

# ── Indicators ────────────────────────────────────────
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

    # Swing 1H
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low

    # Sweep
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ── Backtest ─────────────────────────────────────────
def backtest(df15, exclude_618=False, allow_100=False):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        # Session filter: Asia (0-6) requires golden zone? We'll skip golden zone check entirely for simplicity
        # and rely on fibo exclusion later.
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
            # Asia must still have BB touch (below) but we'll remove golden zone cap
            # just check that Swing_H/Swing_L exist
            if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
                continue
            # BB touch required
            if row['EMA20'] > row['EMA50']:
                if not (row['low'] <= row['BB_Lower']*1.02):
                    continue
            else:
                if not (row['high'] >= row['BB_Upper']*0.98):
                    continue

        direction = entry = sl = tp = None
        fibo_level = np.nan
        if row['EMA20'] > row['EMA50']:  # BUY
            if row['Bull_Sweep']:
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['Swing_H'] - row['close']) / (row['Swing_H'] - row['Swing_L'])
                # Filter: exclude 0.618 zone if enabled
                if exclude_618 and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    continue
                # If allow_100, we permit up to 1.00, no upper bound; otherwise original golden zone cap 0.786
                if not allow_100 and fibo_level is not np.nan and fibo_level > 0.786:
                    continue
                if allow_100 and fibo_level is not np.nan and fibo_level > 1.00:
                    continue   # beyond 1.00 not allowed (price below swing low)
                direction = 'BUY'; entry = row['close']
        else:  # SELL
            if row['Bear_Sweep']:
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['close'] - row['Swing_L']) / (row['Swing_H'] - row['Swing_L'])  # retrace from low to price
                if exclude_618 and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    continue
                if not allow_100 and fibo_level is not np.nan and fibo_level > 0.786:
                    continue
                if allow_100 and fibo_level is not np.nan and fibo_level > 1.00:
                    continue
                direction = 'SELL'; entry = row['close']

        if direction is None:
            continue

        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act, hi, lo = False, entry, entry
        pnl, exit_reason, first_sl = 0.0, 'TIMEOUT', False
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp: pnl = (tp-entry)/entry*100; exit_reason='TP'; break
                elif l <= sl:
                    if not be_act: first_sl = True
                    pnl = (sl-entry)/entry*100; exit_reason='SL'; break
                if not be_act and h >= entry*1.0015: be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp: pnl = (entry-tp)/entry*100; exit_reason='TP'; break
                elif h >= sl:
                    if not be_act: first_sl = True
                    pnl = (entry-sl)/entry*100; exit_reason='SL'; break
                if not be_act and l <= entry*0.9985: be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100

        rr = abs((tp-entry)/(entry-sl)) if entry != sl else 0
        trades.append({
            'dir': direction, 'pnl': pnl, 'exit': exit_reason,
            'first_sl': first_sl, 'rr': rr, 'fibo': fibo_level
        })
    return pd.DataFrame(trades)

def stats(trades_df):
    total = len(trades_df)
    if total==0: return {'Total':0,'Win%':0,'PnL%':0,'DD%':0,'FirstSL':0,'AvgRR':0}
    wins = trades_df[trades_df['pnl']>0]
    wr = len(wins)/total*100
    pnl = trades_df['pnl'].sum()
    cum, peak, dd = 0,0,0
    for p in trades_df['pnl']:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    first_sl = trades_df['first_sl'].sum()
    avg_rr = trades_df['rr'].mean()
    return {'Total':total,'Win%':wr,'PnL%':pnl,'DD%':dd,'FirstSL':first_sl,'AvgRR':avg_rr}

def fibo_breakdown(trades_df):
    bins = [0.50, 0.618, 0.72, 0.78, 0.86, 1.00]
    labels = ['0.618', '0.72', '0.78', '0.86', '1.00']
    trades_df['fibo_bin'] = pd.cut(trades_df['fibo'], bins=bins, labels=labels, right=True)
    result = {}
    for lvl in labels:
        sub = trades_df[trades_df['fibo_bin']==lvl]
        result[lvl] = stats(sub)
    return result

if __name__ == "__main__":
    print("📡 Generating data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")

    print("🔄 Backtesting NEW_V2 (Original)...")
    trades_orig = backtest(df15, exclude_618=False, allow_100=False)
    stats_orig = stats(trades_orig)

    print("🔄 Backtesting NEW_V2 (No 0.618 + 1.00)...")
    trades_new = backtest(df15, exclude_618=True, allow_100=True)
    stats_new = stats(trades_new)

    print("\n📊 OVERALL COMPARISON")
    print("="*80)
    print(f"{'':<12} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}")
    print(f"{'Original':<12} {stats_orig['Total']:<8} {stats_orig['Win%']:<8.1f} {stats_orig['PnL%']:<+10.2f} {stats_orig['DD%']:<10.2f} {stats_orig['FirstSL']:<8} {stats_orig['AvgRR']:<8.2f}")
    print(f"{'No618+100':<12} {stats_new['Total']:<8} {stats_new['Win%']:<8.1f} {stats_new['PnL%']:<+10.2f} {stats_new['DD%']:<10.2f} {stats_new['FirstSL']:<8} {stats_new['AvgRR']:<8.2f}")

    print("\n📊 FIBO BREAKDOWN (No 0.618 + 1.00)")
    print("="*80)
    fibo = fibo_breakdown(trades_new)
    print(f"{'Level':<10} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}")
    for lvl in ['0.72','0.78','0.86','1.00']:
        s = fibo[lvl]
        print(f"{lvl:<10} {s['Total']:<8} {s['Win%']:<8.1f} {s['PnL%']:<+10.2f} {s['DD%']:<10.2f} {s['FirstSL']:<8} {s['AvgRR']:<8.2f}")
