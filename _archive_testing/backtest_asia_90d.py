#!/usr/bin/env python3
"""
backtest_asia_90d.py — One‑Shot ASIA Blocked vs Rebound (BB + HA 15m)
ใช้ข้อมูลจริงจาก Twelve Data ย้อนหลัง 90 วัน (fallback mock)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── 1. Load Data (90 days) ───────────────────────────────
def load_data(days=90):
    try:
        from data_provider_twelvedata import fetch_twelvedata
        df_15m, _, _ = fetch_twelvedata()
        if df_15m is not None and len(df_15m) > 0:
            cutoff = df_15m.index.max() - pd.Timedelta(days=days)
            df_15m = df_15m[df_15m.index >= cutoff]
            if len(df_15m) < 100:
                raise ValueError("Not enough data")
            return df_15m
    except Exception as e:
        print(f"⚠️ Twelve Data fallback: {e}")

    # Mock 90 days (8640 candles 15m)
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
    return df

# ── 2. Indicators (15m only) ─────────────────────────────
def add_indicators(df):
    df = df.copy()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['ATR14'] = calc_atr(df, 14)
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']

    # Heikin Ashi (15m)
    ha = heikin_ashi(df)
    df['HA_Open'] = ha['open']
    df['HA_Close'] = ha['close']
    df['HA_Bullish'] = df['HA_Close'] > df['HA_Open']

    # Simplified Scores (matching original v11.2 style)
    df['Score_Buy'] = np.where(df['EMA20'] > df['EMA50'], 5, 2)
    df['Score_Sell'] = np.where(df['EMA20'] < df['EMA50'], 5, 2)
    return df

def calc_atr(df, period=14):
    high, low, close = df['high'], df['low'], df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def heikin_ashi(df):
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    return pd.DataFrame({'open': ha_open, 'close': ha_close}, index=df.index)

# ── 3. Session Filters ────────────────────────────────────
def compute_session_filters(df):
    utc_hour = df.index.hour
    # Original: London+NY only (12-22 UTC)
    df['session_ok_orig'] = (utc_hour >= 12) & (utc_hour <= 22)

    # Asia Rebound: allow Asia (0-6 UTC) if BB touch + Heikin Ashi reversal
    asia_hours = (utc_hour >= 0) & (utc_hour <= 6)
    bb_touch = (df['low'] <= df['BB_Lower'] * 1.01) | (df['high'] >= df['BB_Upper'] * 0.99)
    ha_rev_bull = df['HA_Bullish'] & ~df['HA_Bullish'].shift(1).fillna(False)  # red -> green
    ha_rev_bear = ~df['HA_Bullish'].fillna(False) & df['HA_Bullish'].shift(1).fillna(False)  # green -> red
    ha_reversal = ha_rev_bull | ha_rev_bear

    df['session_ok_rebound'] = df['session_ok_orig'] | (asia_hours & bb_touch & ha_reversal)

# ── 4. Backtest Engine (same as original v11.2) ───────────
def run_backtest(df, session_col):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        if not row[session_col]:
            continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            v4 = row['low'] <= row['BB_Lower'] * 1.02
            v5 = v4 and (row['Score_Buy'] >= 5) and row['HA_Bullish']
            if v4 or v5: direction = 'BUY'; entry = row['close']
        elif row['EMA20'] < row['EMA50']:
            v4 = row['high'] >= row['BB_Upper'] * 0.98
            v5 = v4 and (row['Score_Sell'] >= 5) and not row['HA_Bullish']
            if v4 or v5: direction = 'SELL'; entry = row['close']
        if direction is None: continue

        sl = (entry - row['ATR14'] * 1.5) if direction == 'BUY' else (entry + row['ATR14'] * 1.5)
        tp = row['BB_Upper'] if direction == 'BUY' else row['BB_Lower']
        be_act = False; hi = entry; lo = entry; pnl = 0.0
        for j in range(i+1, min(i+max_bars, len(df))):
            r = df.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp: pnl = (tp-entry)/entry*100; break
                elif l <= sl: pnl = (sl-entry)/entry*100; break
                if not be_act and h >= entry*1.0010: be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp: pnl = (entry-tp)/entry*100; break
                elif h >= sl: pnl = (entry-sl)/entry*100; break
                if not be_act and l <= entry*0.9990: be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100
        trades.append(pnl)
    return trades

def stats(trades):
    total = len(trades)
    if total == 0: return {'total':0, 'wr':0, 'pnl':0, 'dd':0}
    wins = [t for t in trades if t > 0]
    wr = len(wins)/total*100
    pnl = sum(trades)
    cum=0; peak=0; dd=0
    for t in trades:
        cum+=t
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'total':total, 'wr':wr, 'pnl':pnl, 'dd':dd}

# ── 5. Main ───────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading 90‑day data...")
    df_15m = load_data(90)
    print(f"✅ Loaded {len(df_15m)} candles (15m)")

    df_15m = add_indicators(df_15m).dropna()
    compute_session_filters(df_15m)

    print("🔄 Backtesting ORIGINAL (Asia Blocked)...")
    trades_orig = run_backtest(df_15m, 'session_ok_orig')
    s_orig = stats(trades_orig)

    print("🔄 Backtesting REBOUND (Asia BB+HA 15m)...")
    trades_reb = run_backtest(df_15m, 'session_ok_rebound')
    s_reb = stats(trades_reb)

    print("\n" + "="*60)
    print("📊 ASIA BACKTEST COMPARISON (90 days)")
    print("="*60)
    print(f"{'':<25} {'ORIGINAL (Blocked)':<20} {'REBOUND (BB+HA 15m)':<20}")
    print(f"{'Trades':<25} {s_orig['total']:<20} {s_reb['total']:<20}")
    print(f"{'Win Rate':<25} {s_orig['wr']:.1f}%{'':<15} {s_reb['wr']:.1f}%")
    print(f"{'Total PnL':<25} {s_orig['pnl']:+.2f}%{'':<15} {s_reb['pnl']:+.2f}%")
    print(f"{'Max DD':<25} -{s_orig['dd']:.2f}%{'':<15} -{s_reb['dd']:.2f}%")
    print("="*60)
    if s_reb['pnl'] > s_orig['pnl'] and s_reb['wr'] >= s_orig['wr'] and s_reb['dd'] <= s_orig['dd']:
        print("✅ ASIA Rebound IMPROVES performance — consider unlocking")
    else:
        print("⚠️ Rebound does NOT improve — keep current block")
    print("="*60)
