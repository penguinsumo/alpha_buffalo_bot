#!/usr/bin/env python3
"""
backtest_entry_methods.py — เปรียบเทียบ Entry Method:
OLD: HA 15m Reversal + BB (Asia Blocked)
NEW: Golden Fibo 1H + Median Price + BB (Asia Rebound)
แยกฝั่ง Buy / Sell
ใช้ข้อมูล 90 วัน (Twelve Data หรือ mock)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ── 1. Load Data (90 days) ───────────────────────────────
def load_data(days=90):
    try:
        from data_provider_twelvedata import fetch_twelvedata
        df_15m, df_1h, _ = fetch_twelvedata()
        if df_15m is not None and len(df_15m) > 0:
            cutoff = df_15m.index.max() - pd.Timedelta(days=days)
            df_15m = df_15m[df_15m.index >= cutoff]
            if len(df_15m) < 100:
                raise ValueError("Not enough data")
            # resample 1h (ให้แน่ใจ)
            df_1h = df_15m.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
            return df_15m, df_1h
    except Exception as e:
        print(f"⚠️ Twelve Data fallback: {e}")

    # Mock 90 days
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

# ── 2. Indicators ──────────────────────────────────────
def add_indicators(df, df_1h):
    df = df.copy()
    # BB
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']
    # ATR
    high, low, close = df['high'], df['low'], df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    # EMA
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    # Heikin Ashi 15m (for OLD method)
    ha = heikin_ashi(df)
    df['HA_Open'] = ha['open']
    df['HA_Close'] = ha['close']
    df['HA_Bullish'] = df['HA_Close'] > df['HA_Open']
    # Scores (simple)
    df['Score_Buy'] = np.where(df['EMA20'] > df['EMA50'], 5, 2)
    df['Score_Sell'] = np.where(df['EMA20'] < df['EMA50'], 5, 2)

    # Golden Fibo 1H (for NEW method)
    df_1h = df_1h.copy()
    highs = df_1h['high'].rolling(5).max()
    lows = df_1h['low'].rolling(5).min()
    swing_high = highs.max()
    swing_low = lows.min()
    if swing_high > swing_low:
        diff = swing_high - swing_low
        golden_low = swing_high - diff * 0.786
        golden_high = swing_high - diff * 0.618
    else:
        golden_low = golden_high = None
    # ใช้ค่าเดียวกันสำหรับทุกแท่งใน df_15m (static จากข้อมูล 1H ที่มี) 
    # แต่เพื่อความถูกต้อง เราต้องคำนวณ golden zone สำหรับแต่ละแท่ง? 
    # ง่าย: ใช้ golden zone ล่าสุดจาก 1h ที่มีข้อมูลถึงแท่งปัจจุบัน
    # เราทำเป็น series ใน df_15m โดยใช้ rolling บน 1h แล้ว map กลับมา (ซับซ้อน)
    # เพื่อความง่าย เราจะใช้ golden zone จากการคำนวณบน 1h ทั้งชุด (อาจไม่ dynamic มาก)
    # แต่พอใช้เปรียบเทียบได้
    df['Golden_Low_1H'] = golden_low
    df['Golden_High_1H'] = golden_high

    # Median Price Reversal (2-bar average)
    df['Median'] = (df['high'] + df['low']) / 2
    df['MP_Avg_2'] = df['Median'].rolling(2).mean().shift(1)  # average of previous 2 bars

    return df

def heikin_ashi(df):
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    return pd.DataFrame({'open': ha_open, 'close': ha_close}, index=df.index)

# ── 3. Session Filters ──────────────────────────────────
def compute_session_filters(df):
    utc_hour = df.index.hour
    # OLD: Asia blocked (12-22 UTC only)
    df['session_OLD'] = (utc_hour >= 12) & (utc_hour <= 22)
    # NEW: Asia Rebound conditions (จะถูกเช็คใน entry logic)

# ── 4. Entry Logic ──────────────────────────────────────
def run_backtest(df, method='OLD'):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        # Session filter
        if method == 'OLD':
            if not row['session_OLD']:
                continue
        else:  # NEW
            utc_hour = row.name.hour
            if utc_hour < 12 or utc_hour > 22:  # นอก London/NY
                # Asia (0-6) ต้องผ่านเงื่อนไขพิเศษ
                if not (0 <= utc_hour <= 6):
                    continue
                # ตรวจสอบ Golden Zone + BB touch + Median Reversal
                if not (row['Golden_Low_1H'] and row['Golden_High_1H']):
                    continue
                if not (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H']):
                    continue
                if not (row['low'] <= row['BB_Lower'] * 1.02):  # BUY
                    continue
                if not (row['close'] > row['MP_Avg_2'] + 0.0002 * row['close']):
                    continue
            # ถ้าเป็น London/NY ก็เข้าได้ตามปกติ (ไม่มีเงื่อนไขเพิ่มเติม)

        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if method == 'OLD':
                # OLD: BB touch + HA 15m reversal (แดง -> เขียว)
                v4 = row['low'] <= row['BB_Lower'] * 1.02
                ha_rev = row['HA_Bullish'] and not df['HA_Bullish'].iloc[i-1] if i>0 else False
                if v4 and ha_rev:
                    direction = 'BUY'
                    entry = row['close']
            else:  # NEW
                # NEW: BB touch + Golden Zone + Median Reversal (เช็คแล้วใน session filter สำหรับ Asia? แต่ London ยังต้องเช็ค)
                # สำหรับ London/NY เราใช้แค่ BB touch + EMA (ไม่บังคับ Golden Zone? อาจใช้ก็ได้เพื่อ consistency)
                # เราจะใช้: BB touch + EMA และ Golden Zone เป็น optional boost
                v4 = row['low'] <= row['BB_Lower'] * 1.02
                # Golden zone check (optional)
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                # Median reversal
                median_rev = row['close'] > row['MP_Avg_2'] + 0.0002 * row['close']
                if v4 and in_golden and median_rev:
                    direction = 'BUY'
                    entry = row['close']
        elif row['EMA20'] < row['EMA50']:
            if method == 'OLD':
                v4 = row['high'] >= row['BB_Upper'] * 0.98
                ha_rev = (not row['HA_Bullish']) and df['HA_Bullish'].iloc[i-1] if i>0 else False
                if v4 and ha_rev:
                    direction = 'SELL'
                    entry = row['close']
            else:  # NEW
                v4 = row['high'] >= row['BB_Upper'] * 0.98
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                median_rev = row['close'] < row['MP_Avg_2'] - 0.0002 * row['close']
                if v4 and in_golden and median_rev:
                    direction = 'SELL'
                    entry = row['close']
        if direction is None:
            continue

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
        trades.append((direction, pnl))
    return trades

def stats_by_dir(trades):
    buy_trades = [t[1] for t in trades if t[0] == 'BUY']
    sell_trades = [t[1] for t in trades if t[0] == 'SELL']
    def calc(tr):
        if not tr: return {'total':0, 'wr':0, 'pnl':0, 'dd':0}
        total = len(tr)
        wins = [x for x in tr if x > 0]
        wr = len(wins)/total*100
        pnl = sum(tr)
        cum=0; peak=0; dd=0
        for x in tr:
            cum+=x
            if cum>peak: peak=cum
            if peak-cum>dd: dd=peak-cum
        return {'total':total, 'wr':wr, 'pnl':pnl, 'dd':dd}
    return calc(buy_trades), calc(sell_trades)

# ── 5. Main ──────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading 90‑day data...")
    df_15m, df_1h = load_data(90)
    print(f"✅ Loaded {len(df_15m)} candles (15m)")

    df_15m = add_indicators(df_15m, df_1h).dropna()
    compute_session_filters(df_15m)

    print("🔄 Backtesting OLD method (HA 15m + BB, Asia Blocked)...")
    trades_old = run_backtest(df_15m, 'OLD')
    buy_old, sell_old = stats_by_dir(trades_old)

    print("🔄 Backtesting NEW method (Golden Fibo 1H + Median Price + BB, Asia Rebound)...")
    trades_new = run_backtest(df_15m, 'NEW')
    buy_new, sell_new = stats_by_dir(trades_new)

    print("\n" + "="*80)
    print("📊 BUY SIDE COMPARISON")
    print("="*80)
    print(f"{'':<20} {'OLD (HA 15m)':<25} {'NEW (GFibo+MP)':<25}")
    print(f"{'Trades':<20} {buy_old['total']:<25} {buy_new['total']:<25}")
    print(f"{'Win Rate':<20} {buy_old['wr']:.1f}%{'':<21} {buy_new['wr']:.1f}%")
    print(f"{'Total PnL':<20} {buy_old['pnl']:+.2f}%{'':<20} {buy_new['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{buy_old['dd']:.2f}%{'':<21} -{buy_new['dd']:.2f}%")
    print("="*80)
    print("\n📊 SELL SIDE COMPARISON")
    print("="*80)
    print(f"{'':<20} {'OLD (HA 15m)':<25} {'NEW (GFibo+MP)':<25}")
    print(f"{'Trades':<20} {sell_old['total']:<25} {sell_new['total']:<25}")
    print(f"{'Win Rate':<20} {sell_old['wr']:.1f}%{'':<21} {sell_new['wr']:.1f}%")
    print(f"{'Total PnL':<20} {sell_old['pnl']:+.2f}%{'':<20} {sell_new['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{sell_old['dd']:.2f}%{'':<21} -{sell_new['dd']:.2f}%")
    print("="*80)
