#!/usr/bin/env python3
"""
backtest_sell_pinbar_vs_old_fixed.py — Fixed Sell Logic Comparison
New: Pinbar Entry (1.00-1.18, VSA, Upper BB) + BE fast + Reversal TP (0.72-1.00)
Old: Sweep + Upper BB Touch + Simple Trailing Exit (NEW_V2 style)
Mock 90 days
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
    df15['Volume_MA20'] = df15['volume'].rolling(20).mean()

    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Diff'] = sw_high - sw_low

    # Sweep (old)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])

    # Pinbar
    body = abs(df15['close'] - df15['open'])
    upper_wick = df15['high'] - df15[['open','close']].max(axis=1)
    df15['Pinbar'] = (upper_wick > 2*body) & (df15['close'] < df15['open']) & (body > 0.01)
    return df15

# ── Sell Old Logic (NEW_V2 style) ─────────────────────
def old_sell_trades(df15):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        if row['EMA20'] >= row['EMA50']:
            continue
        if not (row['Bear_Sweep'] and row['high'] >= row['BB_Upper']*0.98):
            continue
        entry = row['close']
        sl = entry + row['ATR14']*1.5
        tp = row['BB_Lower']
        be_act = False
        lowest = entry
        pnl = 0.0; exit_reason = 'TIMEOUT'
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if l < lowest: lowest = l
            if not be_act and lowest <= entry * 0.9985:
                be_act = True; sl = entry
            if be_act:
                sl = min(sl, lowest * 1.0005)
            if l <= tp:
                pnl = (entry - tp)/entry*100; exit_reason='TP'; break
            if h >= sl:
                pnl = (entry - sl)/entry*100; exit_reason='SL'; break
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (entry - last)/entry*100
        trades.append(pnl)
    return trades

# ── Sell New Logic (Pinbar + BE + Reversal TP) ────────
def new_sell_trades(df15):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        if row['EMA20'] >= row['EMA50']:
            continue
        if not row['Pinbar'] or not (row['volume'] > row['Volume_MA20']):
            continue
        diff = row['Diff']
        if diff <= 0:
            continue
        extension = (row['high'] - row['Swing_L']) / diff
        if not (1.0 <= extension <= 1.18):
            continue
        if row['high'] < row['BB_Upper']:
            continue
        entry = row['high']  # entry at pinbar's high
        sl = entry + row['ATR14']*1.5
        tp1 = row['Swing_H'] - diff * 0.72
        tp2 = row['Swing_L']
        be_act = False
        lowest = entry
        qty_rem = 1.0
        partial_pnl = 0.0
        pnl = 0.0; exit_reason = 'TIMEOUT'
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if l < lowest: lowest = l
            if not be_act and l <= entry * 0.9985:
                be_act = True; sl = entry
            if be_act:
                sl = min(sl, lowest * 1.0005)
            # Partial at tp1
            if l <= tp1 and qty_rem == 1.0:
                partial_pnl += (entry - tp1)/entry * 0.5 * 100
                qty_rem -= 0.5
            # Full TP at tp2 (Swing Low)
            if l <= tp2:
                remaining = (entry - tp2)/entry * qty_rem * 100
                pnl = partial_pnl + remaining
                exit_reason = 'TP2'; break
            if h >= sl:
                remaining = (entry - sl)/entry * qty_rem * 100
                pnl = partial_pnl + remaining
                exit_reason = 'SL'; break
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            remaining = (entry - last)/entry * qty_rem * 100
            pnl = partial_pnl + remaining
        trades.append(pnl)
    return trades

# ── Statistics ─────────────────────────────────────────
def stats(trades):
    if not trades: return {}
    wins = [t for t in trades if t > 0]
    wr = len(wins)/len(trades)*100
    pnl = sum(trades)
    cum, peak, dd = 0,0,0
    for t in trades:
        cum+=t
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':len(trades), 'wr':wr, 'pnl':pnl, 'dd':dd}

# ── Main ───────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Generating mock 90-day data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")

    print("\n🔄 OLD Sell Logic...")
    old = old_sell_trades(df15)
    s_old = stats(old)

    print("🔄 NEW Sell Logic (Pinbar + BE + Reversal TP)...")
    new = new_sell_trades(df15)
    s_new = stats(new)

    print("\n📊 SELL COMPARISON")
    print(f"{'Metric':<15} {'OLD':<15} {'NEW':<15}")
    print(f"{'Trades':<15} {s_old.get('trades',0):<15} {s_new.get('trades',0):<15}")
    print(f"{'Win Rate':<15} {s_old.get('wr',0):.1f}%{'':<10} {s_new.get('wr',0):.1f}%")
    print(f"{'Total PnL':<15} {s_old.get('pnl',0):+.2f}%{'':<10} {s_new.get('pnl',0):+.2f}%")
    print(f"{'Max DD':<15} -{s_old.get('dd',0):.2f}%{'':<10} -{s_new.get('dd',0):.2f}%")
