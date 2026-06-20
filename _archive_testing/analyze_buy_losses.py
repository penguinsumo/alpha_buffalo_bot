#!/usr/bin/env python3
"""
analyze_buy_losses.py — วิเคราะห์ Buy Trades ของ NEW_V2 (Golden Fibo + Sweep)
ดูว่าแต่ละไม้ที่เสีย หรือ DD เกิดจากอะไร
และเช็คว่า BE ทำงานถูกต้องเมื่อราคาวิ่งถึง 1.5% หรือไม่
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── 1. Load Data ──────────────────────────────────────
def load_data(days=90):
    try:
        from data_provider_twelvedata import fetch_twelvedata
        df_15m, _, _ = fetch_twelvedata()
        if df_15m is not None and len(df_15m) > 0:
            cutoff = df_15m.index.max() - pd.Timedelta(days=days)
            df_15m = df_15m[df_15m.index >= cutoff]
            if len(df_15m) < 100:
                raise ValueError("Not enough data")
            df_1h = df_15m.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
            return df_15m, df_1h
    except Exception as e:
        print(f"⚠️ Twelve Data fallback: {e}")

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

# ── 2. Indicators ────────────────────────────────────
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
    # Sweep
    df['Low_Prev'] = df['low'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    return df

# ── 3. Detailed Buy Backtest ─────────────────────────
def analyze_buy_trades(df):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        utc_hour = row.name.hour
        # Session filter: same as NEW_V2
        if utc_hour < 0 or utc_hour > 23:
            continue
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
            if not (row['Golden_Low_1H'] and row['Golden_High_1H']):
                continue
            if not (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H']):
                continue
            if not (row['low'] <= row['BB_Lower'] * 1.02):
                continue

        # Entry condition for BUY (NEW_V2)
        if row['EMA20'] <= row['EMA50']:
            continue
        v4 = row['low'] <= row['BB_Lower'] * 1.02
        in_golden = True
        if row['Golden_Low_1H'] and row['Golden_High_1H']:
            in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
        sweep_ok = row['Bull_Sweep']
        if not (v4 and in_golden and sweep_ok):
            continue

        entry = row['close']
        sl = entry - row['ATR14'] * 1.5
        tp = row['BB_Upper']
        be_act = False
        hi = entry
        exit_reason = 'TIMEOUT'
        exit_price = entry
        pnl = 0.0
        be_done_at = None  # ราคาที่ BE ถูกขยับ
        reached_1p5 = False  # ถึง 1.5% กำไรหรือไม่

        for j in range(i+1, min(i+max_bars, len(df))):
            r = df.iloc[j]
            h, l = r['high'], r['low']
            # Check if profit reached 1.5% before any exit
            if not be_act and h >= entry * 1.015:
                reached_1p5 = True
            # BE activation (fast, at 0.15%)
            if not be_act and h >= entry * 1.0015:
                be_act = True
                sl = entry
                be_done_at = h
            # Update highest
            if h > hi:
                hi = h
            # Trailing (after BE)
            if be_act:
                sl = max(sl, hi * 0.9995)
            # Check TP or SL
            if h >= tp:
                exit_reason = 'TP'
                exit_price = tp
                pnl = (tp - entry) / entry * 100
                break
            elif l <= sl:
                exit_reason = 'SL'
                exit_price = sl
                pnl = (sl - entry) / entry * 100
                break
        else:
            last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
            exit_price = last
            pnl = (last - entry) / entry * 100

        trades.append({
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'exit_price': exit_price,
            'pnl': pnl,
            'exit_reason': exit_reason,
            'be_activated': be_act,
            'be_done_at': be_done_at,
            'reached_1p5': reached_1p5
        })
    return trades

# ── 4. Main ──────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading data...")
    df_15m, df_1h = load_data(90)
    print(f"✅ Loaded {len(df_15m)} candles")
    df_15m = add_indicators(df_15m, df_1h).dropna()
    trades = analyze_buy_trades(df_15m)
    print(f"🔍 Total BUY trades: {len(trades)}")

    # แยกตามผลลัพธ์
    wins = [t for t in trades if t['pnl'] > 0]
    losses = [t for t in trades if t['pnl'] <= 0]
    tp_hits = [t for t in trades if t['exit_reason'] == 'TP']
    sl_hits = [t for t in trades if t['exit_reason'] == 'SL']
    timeouts = [t for t in trades if t['exit_reason'] == 'TIMEOUT']

    print(f"✅ Wins: {len(wins)} | ❌ Losses: {len(losses)}")
    print(f"TP exits: {len(tp_hits)} | SL exits: {len(sl_hits)} | Timeouts: {len(timeouts)}")

    # วิเคราะห์ loss trades ที่มี reached_1p5 = True
    loss_reached_1p5 = [t for t in losses if t['reached_1p5']]
    print(f"\n🔥 Loss trades that reached +1.5% profit before losing: {len(loss_reached_1p5)}")
    for t in loss_reached_1p5:
        print(f"   Entry: {t['entry']:.2f} | BE activated: {t['be_activated']} | BE at: {t.get('be_done_at', 'N/A')} | Exit: {t['exit_price']:.2f} ({t['exit_reason']}) | PnL: {t['pnl']:+.2f}%")

    # สรุป BE ทำงานหรือไม่ใน loss trades
    no_be_in_loss = [t for t in losses if not t['be_activated']]
    print(f"\n⚠️ Loss trades without BE activation: {len(no_be_in_loss)}")
    if no_be_in_loss:
        print("   (เหล่านี้คือไม้ที่ราคาไม่เคยถึงจุด BE เลย)")
