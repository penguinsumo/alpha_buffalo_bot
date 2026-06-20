#!/usr/bin/env python3
"""
backtest_sell_new_kivanc_m5.py — New Sell Logic: Kivanc Fibo + M5 Touch + VSA
Old Sell: Sweep + Upper BB (for comparison)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── Data generation (5m) ──────────────────────────────
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

    # 1H Swing
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Diff'] = sw_high - sw_low

    # Sweep (for old sell)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])

    return df15

# ── Old Sell Logic (Sweep + Upper BB) ────────────────
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
            if not be_act and l <= entry * 0.9985:
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

# ── New Sell Logic (Kivanc Fibo + M5 Touch + VSA) ──
def new_sell_trades(df5, df15):
    # Prepare fibo levels from 1H Swing
    # We'll compute per 15m bar using 1H swing embedded in df15
    trades = []
    # We need 5m data aligned; we'll iterate over 15m bars and inside look at 5m touches
    # But easier: precompute fibo resistance levels per 15m bar
    # For each 15m bar i, we have swing H/L. Compute fibo level 1.0 (Swing High) and 1.18.
    # Then we check in the 5m bars that fall between i-1 and i (i.e., within the 15m bar) for touch.
    # Simpler: we'll use 5m bars directly and map to the nearest 15m bar's swing info.
    # For this mock, we'll precompute fibo levels on the 15m dataframe and then, for each 5m bar,
    # we'll look up the corresponding 15m bar's fibo levels (using the 15m bar that contains the 5m bar timestamp).
    # We'll resample fibo levels to 5m index.
    
    # First, get 15m with needed columns: Swing_H, Swing_L, Diff, etc. (already in df15)
    # Then create a mapping: for each 5m bar timestamp, find the 15m bar whose interval contains it.
    # We'll just align by floor to 15min.
    df15_fibo = df15[['Swing_H','Swing_L','Diff','BB_Upper','ATR14']].copy()
    df15_fibo['fibo_1_0'] = df15_fibo['Swing_H']  # 1.0 = Swing High
    df15_fibo['fibo_1_18'] = df15_fibo['Swing_L'] + (df15_fibo['Swing_H'] - df15_fibo['Swing_L']) * 1.18  # 1.18 extension
    
    # Resample to 5m index, forward fill
    fibo_5m = df15_fibo.resample('5min').ffill().reindex(df5.index, method='ffill')
    # Note: forward fill might carry stale values but okay for mock.
    
    # Now iterate over 5m bars
    min_bars_5m = 60  # at least some bars
    max_bars_5m = 160  # 40*4? actually 40*15m = 600 minutes = 120 5m bars, we'll use 120
    
    for i in range(min_bars_5m, len(df5)-120):
        row5 = df5.iloc[i]
        # Need trend: EMA20 < EMA50 on 15m? We can approximate from df15 trend, but we'll use simple trend from 5m? 
        # Use df15 EMA20<EMA50 from the corresponding 15m bar.
        # Find the 15m timestamp
        ts_15 = row5.name.floor('15min')
        try:
            row15 = df15.loc[ts_15]
        except KeyError:
            continue
        if row15['EMA20'] >= row15['EMA50']:
            continue
        
        # Get fibo levels at this 5m bar
        fibo = fibo_5m.iloc[i]
        sw_h = fibo['Swing_H']
        sw_l = fibo['Swing_L']
        diff = fibo['Diff']
        if pd.isna(sw_h) or pd.isna(sw_l) or diff <= 0:
            continue
        
        # Resistance levels to test: 1.0 and 1.18
        levels = [fibo['fibo_1_0'], fibo['fibo_1_18']]
        level_hit = None
        for lvl in levels:
            if pd.notna(lvl) and row5['high'] >= lvl:
                level_hit = lvl
                break
        if level_hit is None:
            continue
        
        # Check VSA on this 5m bar: volume > avg volume (from 15m) and bearish close?
        # Use 15m volume MA20 carried forward
        vol_ma = fibo['Volume_MA20'] if 'Volume_MA20' in fibo else 0
        if row5['volume'] <= vol_ma:
            continue
        if not (row5['close'] < row5['open']):  # bearish candle
            continue
        
        # Confirmation: next 5m bar closes below the level (pullback)
        next_row5 = df5.iloc[i+1]
        if next_row5['close'] >= level_hit:  # didn't pullback
            continue
        
        # Entry at close of next bar
        entry = next_row5['close']
        
        # Risk parameters based on the 15m bar's ATR
        atr = row15['ATR14']
        sl_initial = entry + atr * 1.5
        tp1 = sw_h - diff * 0.72   # 0.72 retracement from swing high
        tp2 = sw_l                  # Swing Low
        
        # Execute exit simulation on 5m bars? Too many bars, we'll simulate on 15m for simplicity.
        # We'll map to 15m timeline: find the 15m bar index that contains entry time.
        # Start from the 15m bar after entry bar.
        entry_time = next_row5.name
        start_15_idx = df15.index.searchsorted(entry_time, side='right')  # first 15m bar after entry
        if start_15_idx >= len(df15)-1:
            continue
        start_15_idx = min(start_15_idx, len(df15)-2)
        
        # Exit simulation on 15m data
        be_act = False
        lowest = entry
        qty_rem = 1.0
        partial_pnl = 0.0
        pnl = 0.0
        max_bars_15 = 20  # 20 bars of 15m = 5 hours
        for j in range(start_15_idx, min(start_15_idx+max_bars_15, len(df15))):
            r = df15.iloc[j]; l = r['low']; h = r['high']
            if l < lowest: lowest = l
            if not be_act and l <= entry * 0.9985:
                be_act = True
                sl_initial = entry
            if be_act:
                sl_initial = min(sl_initial, lowest * 1.0005)
            # Partial at tp1
            if l <= tp1 and qty_rem == 1.0:
                partial_pnl += (entry - tp1)/entry * 0.5 * 100
                qty_rem -= 0.5
            # Full TP at tp2
            if l <= tp2:
                remaining = (entry - tp2)/entry * qty_rem * 100
                pnl = partial_pnl + remaining
                break
            if h >= sl_initial:
                remaining = (entry - sl_initial)/entry * qty_rem * 100
                pnl = partial_pnl + remaining
                break
        else:
            last = df15.iloc[min(start_15_idx+max_bars_15-1, len(df15)-1)]['close']
            remaining = (entry - last)/entry * qty_rem * 100
            pnl = partial_pnl + remaining
        
        trades.append(pnl)
    
    return trades

# ── Stats ─────────────────────────────────────────────
def stats(trades):
    if not trades:
        return {'trades':0, 'wr':0, 'pnl':0, 'dd':0}
    wins = [t for t in trades if t > 0]
    wr = len(wins)/len(trades)*100
    pnl = sum(trades)
    cum, peak, dd = 0,0,0
    for t in trades:
        cum+=t
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':len(trades), 'wr':wr, 'pnl':pnl, 'dd':dd}

# ── Main ─────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Generating 5m mock data (90 days)...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 5m bars: {len(df5)}, 15m bars: {len(df15)}")

    print("\n🔄 OLD Sell (Sweep+BB)...")
    old_trades = old_sell_trades(df15)
    s_old = stats(old_trades)

    print("🔄 NEW Sell (Kivanc Fibo 1.0/1.18 + M5 + VSA)...")
    new_trades = new_sell_trades(df5, df15)
    s_new = stats(new_trades)

    print("\n📊 SELL COMPARISON")
    print(f"{'Metric':<15} {'OLD (Sweep+BB)':<20} {'NEW (Fibo+M5+VSA)':<20}")
    print(f"{'Trades':<15} {s_old['trades']:<20} {s_new['trades']:<20}")
    print(f"{'Win Rate':<15} {s_old['wr']:.1f}%{'':<15} {s_new['wr']:.1f}%")
    print(f"{'Total PnL':<15} {s_old['pnl']:+.2f}%{'':<15} {s_new['pnl']:+.2f}%")
    print(f"{'Max DD':<15} -{s_old['dd']:.2f}%{'':<15} -{s_new['dd']:.2f}%")
