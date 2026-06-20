#!/usr/bin/env python3
"""
backtest_sell_pinbar_vs_old.py — Sell Logic Comparison
New: Pinbar Entry (1.00-1.18, VSA, Upper Wick) + BE fast + Reversal TP (0.72-1.00)
Old: Sweep + Upper BB Touch + Simple Trailing Exit (NEW_V2 style)
Buy side unchanged (NEW_V2 Simple Exit)
Mock 90 days
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ════════════════════════════════════════════════════════════
# 1. Mock Data
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

    # Sweep (for old Sell)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])

    # Pinbar features
    body = abs(df15['close'] - df15['open'])
    upper_wick = df15['high'] - df15[['open','close']].max(axis=1)
    df15['Pinbar'] = (upper_wick > 2*body) & (df15['close'] < df15['open']) & (body > 0.01)
    return df15

# ════════════════════════════════════════════════════════════
# 3. Sell Old Logic (Sweep + BB Touch + Simple Trailing)
# ════════════════════════════════════════════════════════════
def sell_old_entry(row):
    if not (row['EMA20'] < row['EMA50']):
        return False
    if not (row['Bear_Sweep'] and row['high'] >= row['BB_Upper']*0.98):
        return False
    # Golden Zone 0.5-1.0 (same as buy side but mirrored? Actually sell old used same golden zone logic)
    if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
        return False
    diff = row['Diff']
    golden_low = row['Swing_H'] - diff*1.0   # 0.5-1.0? wait, for sell old we used same 0.5-1.0 but price above swing high? Let's keep as original NEW_V2 sell: it was just Sweep+BB, no golden zone for sell? Actually original NEW_V2 sell entry was simply Bear_Sweep and BB touch, no golden zone. We'll keep it that way for old comparison.
    return True

def sell_old_exit(entry, sl, tp, df15, start_idx, max_bars=40):
    be_act = False
    lowest = entry
    pnl = 0.0
    exit_reason = 'TIMEOUT'
    exit_price = entry
    for j in range(start_idx+1, min(start_idx+max_bars, len(df15))):
        r = df15.iloc[j]; h, l = r['high'], r['low']
        # Update lowest
        if l < lowest: lowest = l
        # BE
        if not be_act and lowest <= entry * 0.9985:
            be_act = True
            sl = entry
        # Trailing
        if be_act:
            sl = min(sl, lowest * 1.0005)
        # TP (Lower BB)
        if l <= tp:
            pnl = (entry - tp)/entry*100
            exit_reason = 'TP'; exit_price = tp; break
        # SL
        if h >= sl:
            pnl = (entry - sl)/entry*100
            exit_reason = 'SL'; exit_price = sl; break
    else:
        last = df15.iloc[min(start_idx+max_bars-1, len(df15)-1)]['close']
        pnl = (entry - last)/entry*100
        exit_reason = 'TIMEOUT'; exit_price = last
    return pnl, exit_reason

# ════════════════════════════════════════════════════════════
# 4. Sell New Logic (Pinbar Entry + BE + Reversal TP)
# ════════════════════════════════════════════════════════════
def sell_new_entry(row):
    if not (row['EMA20'] < row['EMA50']):
        return False
    # Pinbar + VSA
    if not row['Pinbar']:
        return False
    if not (row['volume'] > row['Volume_MA20']):
        return False
    # Zone 1.00-1.18
    if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
        return False
    diff = row['Diff']
    extension = (row['high'] - row['Swing_L']) / diff
    if not (1.0 <= extension <= 1.18):
        return False
    # Also price above Upper BB? we can check high >= BB_Upper
    if row['high'] < row['BB_Upper']:
        return False
    return True

def sell_new_exit(entry, sl_initial, df15, start_idx, diff, swing_H, swing_L, atr, max_bars=40):
    # entry is at row.high (the pinbar high)
    tp1 = swing_H - diff * 0.72
    tp2 = swing_L  # 1.00 (Swing Low)
    be_act = False
    qty_rem = 1.0
    partial_pnl = 0.0
    pnl = 0.0
    exit_reason = 'TIMEOUT'
    exit_price = entry
    lowest = entry
    sl = sl_initial
    atr_trail = atr * 0.5

    for j in range(start_idx+1, min(start_idx+max_bars, len(df15))):
        r = df15.iloc[j]; h, l = r['high'], r['low']
        if l < lowest: lowest = l
        # BE
        if not be_act and lowest <= entry * (1 - 0.0015):
            be_act = True
            sl = entry
        # Trailing after BE
        if be_act:
            sl = min(sl, lowest + atr_trail)

        # Partial at tp1 (0.72)
        if l <= tp1 and qty_rem == 1.0:
            partial_qty = 0.5
            partial_pnl = (entry - tp1)/entry * partial_qty * 100
            qty_rem -= partial_qty

        # Full close conditions
        if l <= tp2:  # reached Swing Low
            remaining_pnl = (entry - tp2)/entry * qty_rem * 100
            pnl = partial_pnl + remaining_pnl
            exit_reason = 'TP2_SwingLow'; exit_price = tp2; break
        if h >= sl:
            remaining_pnl = (entry - sl)/entry * qty_rem * 100
            pnl = partial_pnl + remaining_pnl
            exit_reason = 'SL'; exit_price = sl; break
        # also check if price crosses below Lower BB as alternative target
        if l <= row['BB_Lower']:  # row here is the entry bar; we need current bar's BB_Lower
            cur_bb_lower = r['BB_Lower'] if 'BB_Lower' in r else None
            if cur_bb_lower and l <= cur_bb_lower:
                remaining_pnl = (entry - cur_bb_lower)/entry * qty_rem * 100
                pnl = partial_pnl + remaining_pnl
                exit_reason = 'TP_BB_Lower'; exit_price = cur_bb_lower; break
    else:
        last = df15.iloc[min(start_idx+max_bars-1, len(df15)-1)]['close']
        remaining_pnl = (entry - last)/entry * qty_rem * 100
        pnl = partial_pnl + remaining_pnl
        exit_reason = 'TIMEOUT'; exit_price = last

    return pnl, exit_reason, partial_pnl

# ════════════════════════════════════════════════════════════
# 5. Main Backtest
# ════════════════════════════════════════════════════════════
def run_backtest(df15, sell_method='old'):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour

        # Session filter: Asia (0-6) + London/NY (12-22) – for simplicity, always allow? We'll skip session filter for comparison
        # We'll keep it light: only process sell if trend is down (EMA20<EMA50)
        if row['EMA20'] >= row['EMA50']:
            continue  # only sell

        # Decide entry based on method
        if sell_method == 'old':
            if not sell_old_entry(row):
                continue
            entry = row['close']  # old entry at close
            sl = entry + row['ATR14']*1.5
            tp = row['BB_Lower']
            pnl, exit_reason = sell_old_exit(entry, sl, tp, df15, i)
            trades.append({'pnl': pnl, 'exit': exit_reason, 'method': 'old'})
        else:  # new
            if not sell_new_entry(row):
                continue
            entry = row['high']  # entry at pinbar high
            sl_initial = entry + row['ATR14']*1.5
            pnl, exit_reason, _ = sell_new_exit(entry, sl_initial, df15, i,
                                                row['Diff'], row['Swing_H'], row['Swing_L'],
                                                row['ATR14'])
            trades.append({'pnl': pnl, 'exit': exit_reason, 'method': 'new'})

    return trades

# ════════════════════════════════════════════════════════════
# 6. Summary
# ════════════════════════════════════════════════════════════
def summarize(trades, label):
    if not trades:
        print(f"{label}: No trades")
        return
    pnls = [t['pnl'] for t in trades]
    wr = len([p for p in pnls if p > 0])/len(pnls)*100
    total_pnl = sum(pnls)
    cum=0; peak=0; dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    exits = pd.Series([t['exit'] for t in trades]).value_counts()
    print(f"\n--- {label} ---")
    print(f"Trades: {len(trades)}, WR: {wr:.1f}%, PnL: {total_pnl:+.2f}%, MaxDD: -{dd:.2f}%")
    print("Exits:")
    print(exits.to_string())

if __name__ == "__main__":
    print("📡 Generating mock 90-day data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")

    print("\n🔄 Running OLD Sell Logic...")
    old_trades = run_backtest(df15, sell_method='old')
    summarize(old_trades, "OLD SELL (Sweep+BB)")

    print("\n🔄 Running NEW Sell Logic (Pinbar + BE + Reversal TP)...")
    new_trades = run_backtest(df15, sell_method='new')
    summarize(new_trades, "NEW SELL (Pinbar+BE+RevTP)")

    # Comparison table
    if old_trades and new_trades:
        old_pnl = sum(t['pnl'] for t in old_trades)
        new_pnl = sum(t['pnl'] for t in new_trades)
        print("\n📊 SELL COMPARISON")
        print(f"{'Metric':<15} {'OLD':<15} {'NEW':<15}")
        print(f"{'Trades':<15} {len(old_trades):<15} {len(new_trades):<15}")
        print(f"{'Win Rate':<15} {len([t for t in old_trades if t['pnl']>0])/len(old_trades)*100:.1f}%{'':<10} {len([t for t in new_trades if t['pnl']>0])/len(new_trades)*100:.1f}%")
        print(f"{'Total PnL':<15} {old_pnl:+.2f}%{'':<10} {new_pnl:+.2f}%")
