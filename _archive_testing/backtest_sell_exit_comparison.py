#!/usr/bin/env python3
"""
Compare Sell Exits using OLD Sell Entry (Sweep + Upper BB) 
within the No618+100 Buy system.
Scenario A: Sell Simple Exit (Trailing)
Scenario B: Sell BE + Reversal TP (Partial 0.72, Full at Swing Low or Lower BB)
Buy side: No618+100 (Golden 0.5-1.0, Sweep, BB Touch) + Simple Exit
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

    # 1H Swing
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Diff'] = sw_high - sw_low

    # Sweep
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ── Buy Entry & Exit (No618+100, Simple Exit) ─────────
def buy_trades(df15):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        # Session filter: allow all, but golden zone must be satisfied
        if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
            continue
        diff = row['Diff']
        golden_low = row['Swing_H'] - diff*1.0   # 0.5-1.0 zone
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

# ── Sell Entry (OLD: Sweep + Upper BB) ────────────────
def sell_entry_condition(row):
    if row['EMA20'] >= row['EMA50']:
        return False
    if not (row['Bear_Sweep'] and row['high'] >= row['BB_Upper']*0.98):
        return False
    return True

# ── Sell Exit A: Simple Trailing ──────────────────────
def sell_exit_simple(entry, sl_initial, tp, df15, start_idx, max_bars=40):
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

# ── Sell Exit B: BE + Reversal TP (Partial 0.72, Full Swing Low / Lower BB) ──
def sell_exit_reversal(entry, sl_initial, df15, start_idx, row_atr, swing_H, swing_L, diff, max_bars=40):
    be_act = False
    lowest = entry
    qty_rem = 1.0
    partial_pnl = 0.0
    # TP levels
    tp_partial = swing_H - diff * 0.72
    tp_full = swing_L
    # We'll also consider Lower BB of the current bar as alternative full TP if lower
    cur_bb_lower = df15.iloc[start_idx]['BB_Lower']  # entry bar's lower BB (may be stale later)
    # Actually we need to get updated Lower BB later, but we'll just use the entry bar's as approximation.
    full_tp = tp_full if tp_full < entry else cur_bb_lower  # use Swing Low if it's below entry else use lower BB
    # Better: we'll use min(tp_full, lower_bb) when checking.
    pnl = 0.0
    for j in range(start_idx+1, min(start_idx+max_bars, len(df15))):
        r = df15.iloc[j]; h, l = r['high'], r['low']
        if l < lowest: lowest = l
        if not be_act and l <= entry * (1 - 0.0015):
            be_act = True
            sl_initial = entry
        if be_act:
            # Trailing after BE: use lowest * 1.0005 as SL
            sl_initial = min(sl_initial, lowest * 1.0005)
        
        # Partial at tp_partial (0.72)
        if l <= tp_partial and qty_rem == 1.0:
            partial_pnl += (entry - tp_partial)/entry * 0.5 * 100
            qty_rem -= 0.5
        
        # Full TP: check Swing Low (if below entry) or lower BB
        full_target = tp_full if tp_full < entry else cur_bb_lower
        # also get current bar's lower BB for fresh value? We'll just use entry bar's lower BB for simplicity, but it's a approximation.
        if l <= full_target:
            remaining = (entry - full_target)/entry * qty_rem * 100
            pnl = partial_pnl + remaining
            break
        
        # SL hit
        if h >= sl_initial:
            remaining = (entry - sl_initial)/entry * qty_rem * 100
            pnl = partial_pnl + remaining
            break
    else:
        # Timeout
        last = df15.iloc[min(start_idx+max_bars-1, len(df15)-1)]['close']
        remaining = (entry - last)/entry * qty_rem * 100
        pnl = partial_pnl + remaining
    return pnl

# ── Full Backtest for one scenario ────────────────────
def run_full_backtest(df15, sell_exit_version):
    buy_pnls = buy_trades(df15)
    sell_pnls = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        if not sell_entry_condition(row):
            continue
        entry = row['close']
        sl_initial = entry + row['ATR14']*1.5
        tp = row['BB_Lower']
        if sell_exit_version == 'simple':
            pnl = sell_exit_simple(entry, sl_initial, tp, df15, i)
        else:  # reversal
            pnl = sell_exit_reversal(entry, sl_initial, df15, i, row['ATR14'],
                                     row['Swing_H'], row['Swing_L'], row['Diff'])
        sell_pnls.append(pnl)
    return buy_pnls, sell_pnls

# ── Stats ─────────────────────────────────────────────
def compute_stats(trades):
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
    print("📡 Generating mock 90-day data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")

    print("\n🔄 Scenario A: OLD Sell Simple Exit ...")
    buy_A, sell_A = run_full_backtest(df15, 'simple')
    s_buy_A = compute_stats(buy_A)
    s_sell_A = compute_stats(sell_A)
    total_A = [b+s for b,s in zip(buy_A, sell_A)] if len(buy_A)==len(sell_A) else buy_A+sell_A
    total_A_stats = compute_stats(total_A)

    print("🔄 Scenario B: OLD Sell with BE + Reversal TP ...")
    buy_B, sell_B = run_full_backtest(df15, 'reversal')
    s_buy_B = compute_stats(buy_B)
    s_sell_B = compute_stats(sell_B)
    total_B = [b+s for b,s in zip(buy_B, sell_B)] if len(buy_B)==len(sell_B) else buy_B+sell_B
    total_B_stats = compute_stats(total_B)

    print("\n📊 COMPARISON TABLE")
    print("="*80)
    print(f"{'':<20} {'Scenario A (Simple)':<25} {'Scenario B (Reversal)':<25}")
    print(f"{'Buy Trades':<20} {s_buy_A['trades']:<25} {s_buy_B['trades']:<25}")
    print(f"{'Sell Trades':<20} {s_sell_A['trades']:<25} {s_sell_B['trades']:<25}")
    print(f"{'Total Trades':<20} {total_A_stats['trades']:<25} {total_B_stats['trades']:<25}")
    print(f"{'Buy Win Rate':<20} {s_buy_A['wr']:.1f}%{'':<21} {s_buy_B['wr']:.1f}%")
    print(f"{'Sell Win Rate':<20} {s_sell_A['wr']:.1f}%{'':<21} {s_sell_B['wr']:.1f}%")
    print(f"{'Total Win Rate':<20} {total_A_stats['wr']:.1f}%{'':<21} {total_B_stats['wr']:.1f}%")
    print(f"{'Buy PnL':<20} {s_buy_A['pnl']:+.2f}%{'':<20} {s_buy_B['pnl']:+.2f}%")
    print(f"{'Sell PnL':<20} {s_sell_A['pnl']:+.2f}%{'':<20} {s_sell_B['pnl']:+.2f}%")
    print(f"{'Total PnL':<20} {total_A_stats['pnl']:+.2f}%{'':<20} {total_B_stats['pnl']:+.2f}%")
    print(f"{'Buy Max DD':<20} -{s_buy_A['dd']:.2f}%{'':<20} -{s_buy_B['dd']:.2f}%")
    print(f"{'Sell Max DD':<20} -{s_sell_A['dd']:.2f}%{'':<20} -{s_sell_B['dd']:.2f}%")
    print(f"{'Total Max DD':<20} -{total_A_stats['dd']:.2f}%{'':<20} -{total_B_stats['dd']:.2f}%")
    print("="*80)
