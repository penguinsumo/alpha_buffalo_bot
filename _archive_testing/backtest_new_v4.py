#!/usr/bin/env python3
"""
backtest_new_v4.py — เปรียบเทียบ NEW_V2 vs NEW_V4 (Sweep Buffer + Confirmation Bar)
ใช้ mock 90 วัน
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── 1. Load Mock Data ───────────────────────────────
def load_data(days=90):
    np.random.seed(42)
    end = datetime.now(timezone.utc).replace(minute=(datetime.now().minute // 15) * 15, second=0, microsecond=0)
    start = end - timedelta(days=days)
    dates = pd.date_range(start, end, freq='15min')
    close = 2400 + np.cumsum(np.random.randn(len(dates)) * 0.6)
    df = pd.DataFrame({
        'open': close - 0.2,
        'high': close + 1.2,
        'low': close - 1.2,
        'close': close,
        'volume': np.random.randint(50, 300, len(dates))
    }, index=dates)
    df_1h = df.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    return df, df_1h

# ── 2. Indicators ──────────────────────────────────
def add_indicators(df, df_1h):
    df = df.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']
    high, low, close = df['high'], df['low'], df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()

    # Golden Fibo 1H (0.5–0.786)
    df_1h = df_1h.copy()
    highs = df_1h['high'].rolling(5).max()
    lows = df_1h['low'].rolling(5).min()
    swing_high = highs.max()
    swing_low = lows.min()
    if swing_high > swing_low:
        diff = swing_high - swing_low
        golden_low = swing_high - diff * 0.786
        golden_high = swing_high - diff * 0.5
    else:
        golden_low = golden_high = None
    df['Golden_Low_1H'] = golden_low
    df['Golden_High_1H'] = golden_high

    # Sweep conditions
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    # NEW_V2 simple sweep
    df['Bull_Sweep_V2'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep_V2'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    # NEW_V4 sweep buffer: หลุด 0.05% แล้วกลับ
    df['Bull_Sweep_V4'] = (df['low'] < df['Low_Prev'] * 0.9995) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep_V4'] = (df['high'] > df['High_Prev'] * 1.0005) & (df['close'] < df['High_Prev'])
    return df

# ── 3. Backtest Engine ─────────────────────────────
def run_backtest(df, method):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df)-max_bars-1):  # -1 เพื่อให้มีช่องสำหรับ confirmation bar
        row = df.iloc[i]
        utc_hour = row.name.hour

        # Session filter (Asia Rebound + London/NY)
        if utc_hour < 0 or utc_hour > 23:
            continue
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
            if not (row['Golden_Low_1H'] and row['Golden_High_1H']):
                continue
            if not (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H']):
                continue
            # BB touch
            if row['EMA20'] > row['EMA50']:
                if not (row['low'] <= row['BB_Lower'] * 1.02):
                    continue
            else:
                if not (row['high'] >= row['BB_Upper'] * 0.98):
                    continue

        direction = entry = sl = tp = None

        if row['EMA20'] > row['EMA50']:
            if method == 'NEW_V2':
                if row['Bull_Sweep_V2']:
                    direction = 'BUY'; entry = row['close']
            else:  # NEW_V4
                if row['Bull_Sweep_V4']:
                    # Confirmation bar: แท่งถัดไป (i+1) ต้อง close > Low_Prev
                    next_row = df.iloc[i+1]
                    if next_row['close'] > row['Low_Prev']:
                        direction = 'BUY'; entry = next_row['close']
        else:
            if method == 'NEW_V2':
                if row['Bear_Sweep_V2']:
                    direction = 'SELL'; entry = row['close']
            else:  # NEW_V4
                if row['Bear_Sweep_V4']:
                    next_row = df.iloc[i+1]
                    if next_row['close'] < row['High_Prev']:
                        direction = 'SELL'; entry = next_row['close']

        if direction is None:
            continue

        sl = (entry - row['ATR14'] * 1.5) if direction == 'BUY' else (entry + row['ATR14'] * 1.5)
        tp = row['BB_Upper'] if direction == 'BUY' else row['BB_Lower']
        be_act = False; hi = entry; lo = entry; pnl = 0.0
        for j in range(i+2 if method=='NEW_V4' else i+1, min(i+max_bars, len(df))):
            r = df.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp: pnl = (tp-entry)/entry*100; break
                elif l <= sl: pnl = (sl-entry)/entry*100; break
                if not be_act and h >= entry*1.0015: be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp: pnl = (entry-tp)/entry*100; break
                elif h >= sl: pnl = (entry-sl)/entry*100; break
                if not be_act and l <= entry*0.9985: be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100
        trades.append((direction, pnl))
    return trades

def stats_by_dir(trades):
    buy = [t[1] for t in trades if t[0]=='BUY']
    sell = [t[1] for t in trades if t[0]=='SELL']
    def calc(tr):
        if not tr: return {'total':0,'wr':0,'pnl':0,'dd':0}
        total = len(tr)
        wins = [x for x in tr if x>0]
        wr = len(wins)/total*100
        pnl = sum(tr)
        cum=0; peak=0; dd=0
        for x in tr:
            cum+=x
            if cum>peak: peak=cum
            if peak-cum>dd: dd=peak-cum
        return {'total':total,'wr':wr,'pnl':pnl,'dd':dd}
    return calc(buy), calc(sell)

# ── 4. Main ──────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading mock data (90 days)...")
    df_15m, df_1h = load_data(90)
    print(f"✅ Loaded {len(df_15m)} candles")

    df_15m = add_indicators(df_15m, df_1h).dropna()

    methods = ['NEW_V2', 'NEW_V4']
    results = {}
    for m in methods:
        print(f"🔄 Backtesting {m}...")
        trades = run_backtest(df_15m, m)
        buy, sell = stats_by_dir(trades)
        results[m] = (buy, sell)

    print("\n" + "="*100)
    print("📊 BUY SIDE COMPARISON")
    print("="*100)
    header = f"{'':<20} {'NEW_V2 (GF+Sweep)':<25} {'NEW_V4 (Buffer+Confirm)':<25}"
    print(header)
    print(f"{'Trades':<20} {results['NEW_V2'][0]['total']:<25} {results['NEW_V4'][0]['total']:<25}")
    print(f"{'Win Rate':<20} {results['NEW_V2'][0]['wr']:.1f}%{'':<21} {results['NEW_V4'][0]['wr']:.1f}%")
    print(f"{'Total PnL':<20} {results['NEW_V2'][0]['pnl']:+.2f}%{'':<20} {results['NEW_V4'][0]['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{results['NEW_V2'][0]['dd']:.2f}%{'':<21} -{results['NEW_V4'][0]['dd']:.2f}%")

    print("\n📊 SELL SIDE COMPARISON")
    print("="*100)
    header = f"{'':<20} {'NEW_V2 (GF+Sweep)':<25} {'NEW_V4 (Buffer+Confirm)':<25}"
    print(header)
    print(f"{'Trades':<20} {results['NEW_V2'][1]['total']:<25} {results['NEW_V4'][1]['total']:<25}")
    print(f"{'Win Rate':<20} {results['NEW_V2'][1]['wr']:.1f}%{'':<21} {results['NEW_V4'][1]['wr']:.1f}%")
    print(f"{'Total PnL':<20} {results['NEW_V2'][1]['pnl']:+.2f}%{'':<20} {results['NEW_V4'][1]['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{results['NEW_V2'][1]['dd']:.2f}%{'':<21} -{results['NEW_V4'][1]['dd']:.2f}%")

    total_v2 = results['NEW_V2'][0]['pnl'] + results['NEW_V2'][1]['pnl']
    total_v4 = results['NEW_V4'][0]['pnl'] + results['NEW_V4'][1]['pnl']
    print(f"\n💰 Total PnL: NEW_V2 = {total_v2:+.2f}% | NEW_V4 = {total_v4:+.2f}%")
