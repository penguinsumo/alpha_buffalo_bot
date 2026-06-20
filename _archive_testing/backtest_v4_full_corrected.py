#!/usr/bin/env python3
"""
backtest_v4_full_corrected.py — V4 Entry + Full Exit (CORRECTED 0.98 logic)
Entry: Golden Zone 0.5-1.00, Sweep, BB Touch
Exit: Partial 50% at Mid, TP 60% at Upper BB, 
      TP_0.98 (98% of distance to TP), Extension (1.272,1.618)
      BE fast, Trailing SL
Output: Detailed breakdown
"""
import pandas as pd, numpy as np
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

# ── Backtest ─────────────────────────────────────────
def run_backtest(df15):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6): continue
        if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']): continue
        diff = row['Diff']
        golden_low = row['Swing_H'] - diff*1.0
        golden_high = row['Swing_H'] - diff*0.5
        if not (golden_low <= row['close'] <= golden_high): continue

        direction = entry = None
        if row['EMA20'] > row['EMA50']:
            if row['Bull_Sweep'] and row['low'] <= row['BB_Lower']*1.02:
                direction = 'BUY'; entry = row['close']
        else:
            if row['Bear_Sweep'] and row['high'] >= row['BB_Upper']*0.98:
                direction = 'SELL'; entry = row['close']
        if direction is None: continue

        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        mid = row['BB_Mid']
        # Extension levels
        ext1272 = (row['Swing_L'] + diff * 1.272) if direction=='BUY' else (row['Swing_H'] - diff * 1.272)
        ext1618 = (row['Swing_L'] + diff * 1.618) if direction=='BUY' else (row['Swing_H'] - diff * 1.618)

        be_act = partial_done = tp_done = ext_done = False
        qty_rem = 1.0
        partial_pnl = tp_pnl = ext_pnl = 0.0
        exit_reason = 'TIMEOUT'
        exit_price = entry
        highest = lowest = entry
        pnl_098 = 0.0

        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l, c = r['high'], r['low'], r['close']
            if direction == 'BUY':
                if h > highest: highest = h
                if not be_act and highest >= entry*1.0015: be_act = True; sl = entry
                if be_act: sl = max(sl, highest*0.9995)

                # Partial
                if not partial_done and highest >= mid:
                    if l <= mid:
                        partial_done = True
                        partial_qty = 0.5
                        partial_pnl = (mid - entry)/entry * partial_qty * 100
                        qty_rem -= partial_qty

                # TP Upper
                if not tp_done and h >= tp:
                    tp_done = True
                    close_qty = qty_rem * 0.6
                    tp_pnl = (tp - entry)/entry * close_qty * 100
                    qty_rem -= close_qty

                # TP_0.98 (corrected)
                if not tp_done and not ext_done and h >= entry + 0.98*(tp - entry):
                    if l <= mid:
                        exit_reason = 'TP_0.98'
                        close_price = entry + 0.98*(tp - entry)
                        pnl_098 = (close_price - entry)/entry * qty_rem * 100
                        exit_price = close_price
                        break

                # Extension after TP
                if tp_done and qty_rem > 0:
                    if h >= ext1618:
                        ext_pnl += (ext1618 - entry)/entry * qty_rem * 100
                        exit_reason = 'EXT_1618'; exit_price = ext1618; break
                    elif h >= ext1272:
                        ext_pnl += (ext1272 - entry)/entry * qty_rem * 100
                        exit_reason = 'EXT_1272'; exit_price = ext1272; break

                # SL
                if l <= sl:
                    exit_reason = 'SL_FULL' if not partial_done else 'SL_AFTER_PARTIAL'
                    exit_price = sl
                    remaining_pnl = (sl - entry)/entry * qty_rem * 100
                    trades.append({
                        'dir': direction, 'exit': exit_reason,
                        'pnl': partial_pnl + tp_pnl + ext_pnl + remaining_pnl,
                        'partial': partial_done, 'tp_upper': tp_done, 'extension': ext_done
                    })
                    break

            else:  # SELL (mirror)
                if l < lowest: lowest = l
                if not be_act and lowest <= entry*0.9985: be_act = True; sl = entry
                if be_act: sl = min(sl, lowest*1.0005)

                if not partial_done and lowest <= mid:
                    if h >= mid:
                        partial_done = True
                        partial_qty = 0.5
                        partial_pnl = (entry - mid)/entry * partial_qty * 100
                        qty_rem -= partial_qty

                if not tp_done and l <= tp:
                    tp_done = True
                    close_qty = qty_rem * 0.6
                    tp_pnl = (entry - tp)/entry * close_qty * 100
                    qty_rem -= close_qty

                if not tp_done and not ext_done and l <= entry - 0.98*(entry - tp):
                    if h >= mid:
                        exit_reason = 'TP_0.98'
                        close_price = entry - 0.98*(entry - tp)
                        pnl_098 = (entry - close_price)/entry * qty_rem * 100
                        exit_price = close_price
                        break

                if tp_done and qty_rem > 0:
                    if l <= ext1618:
                        ext_pnl += (entry - ext1618)/entry * qty_rem * 100
                        exit_reason = 'EXT_1618'; exit_price = ext1618; break
                    elif l <= ext1272:
                        ext_pnl += (entry - ext1272)/entry * qty_rem * 100
                        exit_reason = 'EXT_1272'; exit_price = ext1272; break

                if h >= sl:
                    exit_reason = 'SL_FULL' if not partial_done else 'SL_AFTER_PARTIAL'
                    exit_price = sl
                    remaining_pnl = (entry - sl)/entry * qty_rem * 100
                    trades.append({
                        'dir': direction, 'exit': exit_reason,
                        'pnl': partial_pnl + tp_pnl + ext_pnl + remaining_pnl,
                        'partial': partial_done, 'tp_upper': tp_done, 'extension': ext_done
                    })
                    break
        else:  # Timeout
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            remaining_pnl = (last - entry)/entry * qty_rem * 100 if direction=='BUY' else (entry - last)/entry * qty_rem * 100
            trades.append({
                'dir': direction, 'exit': 'TIMEOUT',
                'pnl': partial_pnl + tp_pnl + ext_pnl + remaining_pnl,
                'partial': partial_done, 'tp_upper': tp_done, 'extension': ext_done
            })
            continue

        # If broke out of loop due to TP_0.98, Extension, or SL (for BUY we had break inside SL block)
        if exit_reason in ['TP_0.98', 'EXT_1272', 'EXT_1618']:
            total = partial_pnl + tp_pnl + ext_pnl + pnl_098
            trades.append({
                'dir': direction, 'exit': exit_reason,
                'pnl': total, 'partial': partial_done, 'tp_upper': tp_done, 'extension': ext_done
            })
    return trades

# ── Summary ───────────────────────────────────────────
def summarize(trades):
    df = pd.DataFrame(trades)
    total_trades = len(df)
    print(f"\n📊 Total Trades: {total_trades}")

    # Exit counts
    exit_counts = df['exit'].value_counts()
    print("\nExit Reason Counts:")
    print(exit_counts.to_string())

    # Partial / TP Upper counts
    partial_count = df['partial'].sum()
    tp_upper_count = df['tp_upper'].sum()
    ext_count = df[df['extension'].notna()].shape[0] if 'extension' in df.columns else 0
    print(f"\nPartial Executed: {partial_count}")
    print(f"TP Upper Executed: {tp_upper_count}")
    print(f"Extension Hit: {ext_count}")

    # Win/Loss/Total PnL/Max DD
    wins = df[df['pnl'] > 0]
    wr = len(wins)/total_trades*100 if total_trades else 0
    total_pnl = df['pnl'].sum()
    cum = peak = dd = 0
    for p in df['pnl']:
        cum += p
        if cum > peak: peak = cum
        if peak - cum > dd: dd = peak - cum
    print(f"\nWin Rate: {wr:.1f}%")
    print(f"Total PnL: {total_pnl:+.2f}%")
    print(f"Max DD: -{dd:.2f}%")

    # Breakdown by direction
    for dir_ in ['BUY', 'SELL']:
        sub = df[df['dir'] == dir_]
        if sub.empty: continue
        print(f"\n--- {dir_} ---")
        print(sub['exit'].value_counts().to_string())
        print(f"PnL: {sub['pnl'].sum():+.2f}% | WR: {len(sub[sub['pnl']>0])/len(sub)*100:.1f}% | DD: -{...}")

    # Additional: how many hit extension after TP upper
    tp_then_ext = df[(df['tp_upper']==True) & (df['exit'].isin(['EXT_1272','EXT_1618']))]
    print(f"\nTrades that hit TP then extension: {len(tp_then_ext)}")

if __name__ == "__main__":
    print("📡 Generating mock 90-day data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15 = add_indicators(df15, df1h).dropna()
    print(f"✅ 15m bars: {len(df15)}")
    trades = run_backtest(df15)
    summarize(trades)
