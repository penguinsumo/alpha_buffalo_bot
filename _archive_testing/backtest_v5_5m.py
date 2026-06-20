#!/usr/bin/env python3
"""
backtest_v5_5m.py — เปรียบเทียบ OLD vs NEW_V2 vs NEW_V5 (Sweep Buffer + HA 5m Confirm)
สร้างข้อมูลสังเคราะห์ 5m แล้ว resample เป็น 15m/1H
แสดงผลแยก Total / Buy / Sell (Trades, WR, PnL, DD, First SL, Avg RR)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ════════════════════════════════════════════════════════════
# 1. สร้างข้อมูล 5m สังเคราะห์ 90 วัน
# ════════════════════════════════════════════════════════════
def generate_5min_data(days=90, seed=42):
    np.random.seed(seed)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    freq = '5min'
    dates = pd.date_range(start, end, freq=freq)
    n = len(dates)
    # Random walk
    r = np.random.randn(n) * 0.3
    close = 2400 + np.cumsum(r)
    # Generate OHLC
    df5 = pd.DataFrame({
        'open': close + np.random.randn(n) * 0.1,
        'high': close + abs(np.random.randn(n) * 0.6),
        'low': close - abs(np.random.randn(n) * 0.6),
        'close': close,
        'volume': np.random.randint(30, 150, n)
    }, index=dates)
    # Adjust high/low to be >= open/close
    df5['high'] = df5[['open','high','close']].max(axis=1)
    df5['low'] = df5[['open','low','close']].min(axis=1)
    return df5

# Resample 5m -> 15m และ 1H
def resample_ohlc(df5):
    df15 = df5.resample('15min').agg({
        'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'
    }).dropna()
    df1h = df5.resample('1h').agg({
        'open':'first', 'high':'max', 'low':'min', 'close':'last', 'volume':'sum'
    }).dropna()
    return df15, df1h

# ════════════════════════════════════════════════════════════
# 2. Indicators
# ════════════════════════════════════════════════════════════
def add_indicators(df15, df1h, df5):
    df15 = df15.copy()
    # BB 15m
    df15['BB_Mid'] = df15['close'].rolling(20).mean()
    df15['BB_Std'] = df15['close'].rolling(20).std()
    df15['BB_Lower'] = df15['BB_Mid'] - 2*df15['BB_Std']
    df15['BB_Upper'] = df15['BB_Mid'] + 2*df15['BB_Std']
    # ATR 14
    h,l,c = df15['high'], df15['low'], df15['close'].shift(1)
    tr = pd.concat([h-l, (h-c).abs(), (l-c).abs()], axis=1).max(axis=1)
    df15['ATR14'] = tr.rolling(14).mean()
    # EMA trend
    df15['EMA20'] = df15['close'].ewm(span=20).mean()
    df15['EMA50'] = df15['close'].ewm(span=50).mean()
    # HA 15m (for OLD)
    ha15 = heikin_ashi(df15)
    df15['HA_Open'] = ha15['open']
    df15['HA_Close'] = ha15['close']
    df15['HA_Bullish'] = df15['HA_Close'] > df15['HA_Open']
    # Golden Fibo 1H (0.5-0.786)
    df1h = df1h.copy()
    highs1h = df1h['high'].rolling(5).max()
    lows1h = df1h['low'].rolling(5).min()
    sw_high = highs1h.max()
    sw_low = lows1h.min()
    if sw_high > sw_low:
        diff = sw_high - sw_low
        gl = sw_high - diff*0.786
        gh = sw_high - diff*0.5
    else:
        gl = gh = None
    df15['Golden_Low_1H'] = gl
    df15['Golden_High_1H'] = gh
    # Sweep signals
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    # OLD uses simple sweep? OLD method doesn't use sweep, but NEW_V2 uses simple sweep
    df15['Bull_Sweep_V2'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep_V2'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    # NEW_V5 sweep buffer
    df15['Bull_Sweep_V5'] = (df15['low'] < df15['Low_Prev']*0.9995) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep_V5'] = (df15['high'] > df15['High_Prev']*1.0005) & (df15['close'] < df15['High_Prev'])
    # HA 5m (computed on df5, but we'll later map to 15m time)
    ha5 = heikin_ashi(df5)
    df5['HA_Close'] = ha5['close']
    df5['HA_Open'] = ha5['open']
    df5['HA_Bullish'] = df5['HA_Close'] > df5['HA_Open']
    return df15, df5

def heikin_ashi(df):
    ha_close = (df['open']+df['high']+df['low']+df['close'])/4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1,len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1])/2
    return pd.DataFrame({'open':ha_open, 'close':ha_close}, index=df.index)

# ════════════════════════════════════════════════════════════
# 3. Backtest
# ════════════════════════════════════════════════════════════
def run_backtest(df15, df5, method):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        # Session filter
        if method == 'OLD':
            if not (12 <= utc_hour <= 22):
                continue
        else:  # NEW_V2, NEW_V5
            if utc_hour < 12 or utc_hour > 22:   # outside London/NY
                if not (0 <= utc_hour <= 6):     # not Asia
                    continue
                # Asia must pass Golden Zone + BB touch
                if not (row['Golden_Low_1H'] and row['Golden_High_1H']):
                    continue
                if not (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H']):
                    continue
                if row['EMA20'] > row['EMA50']:
                    if not (row['low'] <= row['BB_Lower']*1.02):
                        continue
                else:
                    if not (row['high'] >= row['BB_Upper']*0.98):
                        continue
            # London/NY no extra filter
        # Direction
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            # BUY
            if method == 'OLD':
                # HA 15m reversal
                ha_rev = row['HA_Bullish'] and (not df15['HA_Bullish'].iloc[i-1]) if i>0 else False
                v4 = row['low'] <= row['BB_Lower']*1.02
                if v4 and ha_rev:
                    direction = 'BUY'; entry = row['close']
            elif method == 'NEW_V2':
                if row['Bull_Sweep_V2']:
                    direction = 'BUY'; entry = row['close']
            else:  # NEW_V5
                if row['Bull_Sweep_V5']:
                    # Check HA 5m confirmation within 2 bars after this 15m close
                    # Find 5m bars after row.name and before next 10 minutes
                    ts = row.name
                    next_5m = df5[df5.index > ts].iloc[:2]  # up to 2 bars
                    if len(next_5m) > 0:
                        conf = next_5m[next_5m['HA_Bullish']]  # bullish HA
                        if not conf.empty:
                            direction = 'BUY'
                            entry = conf.iloc[0]['close']  # enter at close of first confirmed 5m
        else:
            # SELL
            if method == 'OLD':
                ha_rev = (not row['HA_Bullish']) and df15['HA_Bullish'].iloc[i-1] if i>0 else False
                v4 = row['high'] >= row['BB_Upper']*0.98
                if v4 and ha_rev:
                    direction = 'SELL'; entry = row['close']
            elif method == 'NEW_V2':
                if row['Bear_Sweep_V2']:
                    direction = 'SELL'; entry = row['close']
            else:  # NEW_V5
                if row['Bear_Sweep_V5']:
                    ts = row.name
                    next_5m = df5[df5.index > ts].iloc[:2]
                    if len(next_5m) > 0:
                        conf = next_5m[~next_5m['HA_Bullish']]  # bearish HA
                        if not conf.empty:
                            direction = 'SELL'
                            entry = conf.iloc[0]['close']

        if direction is None:
            continue

        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act = False; hi = entry; lo = entry; pnl = 0.0; exit_reason = 'TIMEOUT'
        first_sl_hit = False
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp:
                    pnl = (tp-entry)/entry*100; exit_reason='TP'; break
                elif l <= sl:
                    if not be_act:   # โดน SL ก่อน BE → first SL
                        first_sl_hit = True
                    pnl = (sl-entry)/entry*100; exit_reason='SL'; break
                if not be_act and h >= entry*1.0015:
                    be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp:
                    pnl = (entry-tp)/entry*100; exit_reason='TP'; break
                elif h >= sl:
                    if not be_act:
                        first_sl_hit = True
                    pnl = (entry-sl)/entry*100; exit_reason='SL'; break
                if not be_act and l <= entry*0.9985:
                    be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100

        # RR
        rr = abs((tp-entry)/(entry-sl)) if entry != sl else 0

        trades.append({
            'dir': direction,
            'pnl': pnl,
            'exit': exit_reason,
            'first_sl': first_sl_hit,
            'rr': rr,
            'be_activated': be_act
        })
    return trades

# ════════════════════════════════════════════════════════════
# 4. Statistics
# ════════════════════════════════════════════════════════════
def compute_stats(trades):
    if not trades:
        return {'total':0,'buy':0,'sell':0,'wr':0,'pnl':0,'dd':0,'first_sl':0,'avg_rr':0}
    df = pd.DataFrame(trades)
    total = len(df)
    buy_df = df[df['dir']=='BUY']
    sell_df = df[df['dir']=='SELL']
    wins = df[df['pnl']>0]
    wr = len(wins)/total*100 if total else 0
    pnl = df['pnl'].sum()
    cum=0; peak=0; dd=0
    for p in df['pnl']:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    first_sl = df['first_sl'].sum()
    avg_rr = df['rr'].mean() if total else 0
    return {
        'total': total,
        'buy': len(buy_df),
        'sell': len(sell_df),
        'wr': wr,
        'pnl': pnl,
        'dd': dd,
        'first_sl': first_sl,
        'avg_rr': avg_rr
    }

# ════════════════════════════════════════════════════════════
# 5. Main
# ════════════════════════════════════════════════════════════
if __name__ == "__main__":
    print("📡 Generating 5m synthetic data 90 days...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    print(f"✅ {len(df5)} 5m bars, {len(df15)} 15m bars, {len(df1h)} 1H bars")

    df15, df5 = add_indicators(df15, df1h, df5)
    df15.dropna(inplace=True)

    methods = ['OLD', 'NEW_V2', 'NEW_V5']
    results = {}
    for m in methods:
        print(f"🔄 Backtesting {m}...")
        trades = run_backtest(df15, df5, m)
        results[m] = compute_stats(trades)

    # ── Print comparison ──
    print("\n" + "="*120)
    print("📊 COMPARISON TABLE")
    print("="*120)
    header = f"{'Method':<10} {'Total':>6} {'Buy':>6} {'Sell':>6} {'WR%':>8} {'PnL%':>8} {'DD%':>8} {'1stSL':>6} {'AvgRR':>6}"
    print(header)
    print("-"*120)
    for m in methods:
        s = results[m]
        print(f"{m:<10} {s['total']:>6} {s['buy']:>6} {s['sell']:>6} {s['wr']:>8.1f} {s['pnl']:>+8.2f} {s['dd']:>8.2f} {s['first_sl']:>6} {s['avg_rr']:>6.2f}")
    print("="*120)

    # Additional detail: Win/Loss breakdown for each method
    for m in methods:
        s = results[m]
        print(f"\n🔹 {m}: Total={s['total']}, Win={s['wr']:.1f}%, PnL={s['pnl']:+.2f}%, DD={s['dd']:.2f}%, FirstSL={s['first_sl']}, AvgRR={s['avg_rr']:.2f}")
