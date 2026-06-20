#!/usr/bin/env python3
"""
backtest_sell_be_tp50.py — Compare Sell Exit: Simple Trailing vs BE+TP50
Buy: No618+100 (unchanged Simple Exit)
Sell Entry: OLD (Sweep + Upper BB)
Mock 90 days
"""
import pandas as pd, numpy as np
from datetime import datetime, timezone, timedelta

# ── Data & Indicators (same as before) ──────────────────
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
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Diff'] = sw_high - sw_low
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ── Buy (unchanged) ──────────────────────────────────────
def buy_trades(df15):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
            continue
        diff = row['Diff']
        golden_low = row['Swing_H'] - diff*1.0
        golden_high = row['Swing_H'] - diff*0.5
        if not (golden_low <= row['close'] <= golden_high):
            continue
        if row['EMA20'] <= row['EMA50']:
            continue
        if not (row['Bull_Sweep'] and row['low'] <= row['BB_Lower']*1.02):
            continue
        entry = row['close']
        sl = entry - row['ATR14']*1.5
        tp = row['BB_Upper']
        be_act = False
        highest = entry
        pnl = 0.0
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if h > highest: highest = h
            if not be_act and highest >= entry*1.0015:
                be_act = True; sl = entry
            if be_act: sl = max(sl, highest*0.9995)
            if h >= tp:
                pnl = (tp - entry)/entry*100; break
            if l <= sl:
                pnl = (sl - entry)/entry*100; break
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (last - entry)/entry*100
        trades.append(pnl)
    return trades

# ── Sell Entry (OLD) ─────────────────────────────────────
def sell_entry_ok(row):
    return (row['EMA20'] < row['EMA50'] and
            row['Bear_Sweep'] and
            row['high'] >= row['BB_Upper']*0.98)

# ── Sell Exit A: Simple Trailing (OLD) ──────────────────
def sell_simple_exit(entry, sl_initial, tp, df15, start_idx, max_bars=40):
    be_act = False
    lowest = entry
    pnl = 0.0
    for j in range(start_idx+1, min(start_idx+max_bars, len(df15))):
        r = df15.iloc[j]; h, l = r['high'], r['low']
        if l < lowest: lowest = l
        if not be_act and l <= entry * 0.9985:
            be_act = True; sl_initial = entry
        if be_act:
            sl_initial = min(sl_initial, lowest * 1.0005)
        if l <= tp:
            pnl = (entry - tp)/entry*100; break
        if h >= sl_initial:
            pnl = (entry - sl_initial)/entry*100; break
    else:
        last = df15.iloc[min(start_idx+max_bars-1, len(df15)-1)]['close']
        pnl = (entry - last)/entry*100
    return pnl

# ── Sell Exit B: BE + TP at Fibo 0.50 ─────────────────
def sell_be_tp50_exit(entry, sl_initial, df15, start_idx, swing_H, swing_L, diff, max_bars=40):
    be_act = False
    pnl = 0.0
    tp = swing_H - diff * 0.50   # Fibo 0.50
    for j in range(start_idx+1, min(start_idx+max_bars, len(df15))):
        r = df15.iloc[j]; h, l = r['high'], r['low']
        # BE
        if not be_act and l <= entry * 0.9985:
            be_act = True
            sl_initial = entry
        # TP
        if l <= tp:
            pnl = (entry - tp)/entry*100
            break
        # SL (original or BE)
        if h >= sl_initial:
            pnl = (entry - sl_initial)/entry*100
            break
    else:
        last = df15.iloc[min(start_idx+max_bars-1, len(df15)-1)]['close']
        pnl = (entry - last)/entry*100
    return pnl

# ── Run both scenarios ─────────────────────────────────
if __name__ == "__main__":
    print("📡 Generating mock 90-day data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")

    buy_pnls = buy_trades(df15)
    sell_simple_pnls = []
    sell_be50_pnls = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        if not sell_entry_ok(row):
            continue
        entry = row['close']
        sl_initial = entry + row['ATR14']*1.5
        tp = row['BB_Lower']
        # Simple trailing
        pnl_simple = sell_simple_exit(entry, sl_initial, tp, df15, i)
        sell_simple_pnls.append(pnl_simple)
        # BE+TP50
        pnl_be50 = sell_be_tp50_exit(entry, sl_initial, df15, i,
                                     row['Swing_H'], row['Swing_L'], row['Diff'])
        sell_be50_pnls.append(pnl_be50)

    # ── Stats helper ──────────────────────────────────
    def stats(trades):
        if not trades: return {'trades':0,'wr':0,'pnl':0,'dd':0}
        wins = [t for t in trades if t > 0]
        wr = len(wins)/len(trades)*100
        pnl = sum(trades)
        cum=0; peak=0; dd=0
        for t in trades:
            cum+=t
            if cum>peak: peak=cum
            if peak-cum>dd: dd=peak-cum
        return {'trades':len(trades),'wr':wr,'pnl':pnl,'dd':dd}

    s_buy = stats(buy_pnls)
    s_sell_simple = stats(sell_simple_pnls)
    s_sell_be50 = stats(sell_be50_pnls)

    total_simple = buy_pnls + sell_simple_pnls
    total_be50 = buy_pnls + sell_be50_pnls
    st_simple = stats(total_simple)
    st_be50 = stats(total_be50)

    print("\n📊 COMPARISON (Buy + Sell Combined)")
    print("="*70)
    print(f"{'Metric':<20} {'Simple Trailing':<25} {'BE + TP 0.50':<25}")
    print(f"{'Buy Trades':<20} {s_buy['trades']:<25} {s_buy['trades']:<25}")
    print(f"{'Sell Trades':<20} {s_sell_simple['trades']:<25} {s_sell_be50['trades']:<25}")
    print(f"{'Total Trades':<20} {st_simple['trades']:<25} {st_be50['trades']:<25}")
    print(f"{'Buy WR':<20} {s_buy['wr']:.1f}%{'':<21} {s_buy['wr']:.1f}%")
    print(f"{'Sell WR':<20} {s_sell_simple['wr']:.1f}%{'':<21} {s_sell_be50['wr']:.1f}%")
    print(f"{'Total WR':<20} {st_simple['wr']:.1f}%{'':<21} {st_be50['wr']:.1f}%")
    print(f"{'Buy PnL':<20} {s_buy['pnl']:+.2f}%{'':<20} {s_buy['pnl']:+.2f}%")
    print(f"{'Sell PnL':<20} {s_sell_simple['pnl']:+.2f}%{'':<20} {s_sell_be50['pnl']:+.2f}%")
    print(f"{'Total PnL':<20} {st_simple['pnl']:+.2f}%{'':<20} {st_be50['pnl']:+.2f}%")
    print(f"{'Buy DD':<20} -{s_buy['dd']:.2f}%{'':<20} -{s_buy['dd']:.2f}%")
    print(f"{'Sell DD':<20} -{s_sell_simple['dd']:.2f}%{'':<20} -{s_sell_be50['dd']:.2f}%")
    print(f"{'Total DD':<20} -{st_simple['dd']:.2f}%{'':<20} -{st_be50['dd']:.2f}%")
    print("="*70)
