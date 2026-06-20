#!/usr/bin/env python3
"""
backtest_v2_618_ha.py — NEW_V2 with extra HA 15m reversal filter for 0.618 zone only
Compare with original NEW_V2
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── Data (synthetic 5m -> 15m/1H) ────────────────────
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

# ── Indicators ───────────────────────────────────────
def heikin_ashi(df):
    ha_close = (df['open']+df['high']+df['low']+df['close'])/4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1])/2
    return pd.DataFrame({'HA_Open': ha_open, 'HA_Close': ha_close}, index=df.index)

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

    # HA 15m
    ha = heikin_ashi(df15)
    df15['HA_Open'] = ha['HA_Open']
    df15['HA_Close'] = ha['HA_Close']
    df15['HA_Bullish'] = df15['HA_Close'] > df15['HA_Open']

    # Golden Fibo 1H
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    if sw_high > sw_low:
        diff = sw_high - sw_low
        gl = sw_high - diff*0.786
        gh = sw_high - diff*0.5
    else:
        gl = gh = None
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Golden_Low_1H'] = gl
    df15['Golden_High_1H'] = gh

    # Sweep
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ── Backtest ─────────────────────────────────────────
def backtest(df15, use_618_ha_filter=False):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        # Session filter
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
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

        direction = entry = sl = tp = None
        fibo_level = np.nan
        if row['EMA20'] > row['EMA50']:  # BUY
            if row['Bull_Sweep']:
                # Compute fibo level
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['Swing_H'] - row['close']) / (row['Swing_H'] - row['Swing_L'])
                # Extra filter for 0.618 zone
                if use_618_ha_filter and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    # require HA 15m reversal: current bullish, previous bearish
                    if i > 0:
                        prev_bull = df15['HA_Bullish'].iloc[i-1]
                        curr_bull = row['HA_Bullish']
                        if not (curr_bull and not prev_bull):
                            continue  # skip
                    else:
                        continue
                direction = 'BUY'; entry = row['close']
        else:  # SELL
            if row['Bear_Sweep']:
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['Swing_H'] - row['close']) / (row['Swing_H'] - row['Swing_L'])
                if use_618_ha_filter and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    if i > 0:
                        prev_bull = df15['HA_Bullish'].iloc[i-1]
                        curr_bull = row['HA_Bullish']
                        if not (not curr_bull and prev_bull):
                            continue
                    else:
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

def summarize_fibo_bins(trades_df):
    bins = [0.50, 0.618, 0.72, 0.78, 0.86]
    labels = ['0.618', '0.72', '0.78', '0.86']
    trades_df['fibo_bin'] = pd.cut(trades_df['fibo'], bins=bins, labels=labels, right=True)
    result = {}
    for bin_name in labels:
        sub = trades_df[trades_df['fibo_bin']==bin_name]
        if sub.empty:
            result[bin_name] = {'Total':0,'Win%':0,'PnL%':0,'DD%':0,'FirstSL':0,'AvgRR':0}
            continue
        total = len(sub)
        wins = sub[sub['pnl']>0]
        wr = len(wins)/total*100 if total else 0
        pnl = sub['pnl'].sum()
        cum, peak, dd = 0,0,0
        for p in sub['pnl']:
            cum+=p
            if cum>peak: peak=cum
            if peak-cum>dd: dd=peak-cum
        first_sl = sub['first_sl'].sum()
        avg_rr = sub['rr'].mean()
        result[bin_name] = {'Total':total, 'Win%':wr, 'PnL%':pnl, 'DD%':dd, 'FirstSL':first_sl, 'AvgRR':avg_rr}
    return result

def overall_stats(trades_df):
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

# ── Main ──────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Generating synthetic data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ {len(df15)} 15m bars")

    # Original NEW_V2
    print("🔄 Backtesting NEW_V2 (original)...")
    trades_orig = backtest(df15, use_618_ha_filter=False)
    stats_orig = overall_stats(trades_orig)
    fibo_orig = summarize_fibo_bins(trades_orig)

    # NEW_V2 with 618 HA filter
    print("🔄 Backtesting NEW_V2 (618 HA filter)...")
    trades_new = backtest(df15, use_618_ha_filter=True)
    stats_new = overall_stats(trades_new)
    fibo_new = summarize_fibo_bins(trades_new)

    print("\n" + "="*100)
    print("📊 OVERALL COMPARISON")
    print("="*100)
    header = f"{'':<10} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}"
    print(header)
    print("-"*60)
    for name, s in [("Orig", stats_orig), ("618HA", stats_new)]:
        print(f"{name:<10} {s['Total']:<8} {s['Win%']:<8.1f} {s['PnL%']:<+10.2f} {s['DD%']:<10.2f} {s['FirstSL']:<8} {s['AvgRR']:<8.2f}")
    
    print("\n📊 FIBO BIN DETAIL (618 HA filter)")
    print("="*100)
    print(f"{'Level':<10} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}")
    print("-"*60)
    for lvl in ['0.618','0.72','0.78','0.86']:
        s = fibo_new[lvl]
        print(f"{lvl:<10} {s['Total']:<8} {s['Win%']:<8.1f} {s['PnL%']:<+10.2f} {s['DD%']:<10.2f} {s['FirstSL']:<8} {s['AvgRR']:<8.2f}")
