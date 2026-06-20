#!/usr/bin/env python3
"""
backtest_sweep_volume.py — เปรียบเทียบ Entry Methods:
1) OLD: HA 15m Reversal + BB (Asia Blocked)
2) NEW_V2: Golden Fibo 1H (0.5‑0.786) + Sweep + BB (Asia Rebound)
3) NEW_V3: เหมือน NEW_V2 แต่ Sweep ต้องมี Volume ยืนยัน (Volume > Avg Volume 20 แท่ง)
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

# ── 2. Indicators ────────────────────────────────────
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
    # Heikin Ashi (for OLD)
    ha = heikin_ashi(df)
    df['HA_Open'] = ha['open']
    df['HA_Close'] = ha['close']
    df['HA_Bullish'] = df['HA_Close'] > df['HA_Open']
    df['Score_Buy'] = np.where(df['EMA20'] > df['EMA50'], 5, 2)
    df['Score_Sell'] = np.where(df['EMA20'] < df['EMA50'], 5, 2)

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

    # Sweep conditions (simple)
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])

    # Volume indicators for filter
    df['Volume_MA20'] = df['volume'].rolling(20).mean()
    df['Volume_Spike'] = df['volume'] > df['Volume_MA20']  # volume above average

    return df

def heikin_ashi(df):
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    return pd.DataFrame({'open': ha_open, 'close': ha_close}, index=df.index)

# ── 3. Session Filters ──────────────────────────────
def compute_session_filters(df):
    utc_hour = df.index.hour
    df['session_OLD'] = (utc_hour >= 12) & (utc_hour <= 22)

# ── 4. Entry Logic ──────────────────────────────────
def run_backtest(df, method):
    trades = []
    min_bars = 20
    max_bars = 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        utc_hour = row.name.hour

        # Session rule per method
        if method == 'OLD':
            if not row['session_OLD']:
                continue
        else:  # NEW_V2, NEW_V3
            # London+NY always ok
            if utc_hour < 0 or utc_hour > 23:
                continue
            if utc_hour < 12 or utc_hour > 22:  # outside London+NY
                if not (0 <= utc_hour <= 6):  # Asia window
                    continue
                # Asia must pass Golden Zone + BB touch
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

        # Determine trend and potential entry
        if row['EMA20'] > row['EMA50']:
            if method == 'OLD':
                v4 = row['low'] <= row['BB_Lower'] * 1.02
                ha_rev = row['HA_Bullish'] and not df['HA_Bullish'].iloc[i-1] if i>0 else False
                if v4 and ha_rev:
                    direction = 'BUY'; entry = row['close']
            elif method == 'NEW_V2':
                v4 = row['low'] <= row['BB_Lower'] * 1.02
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                sweep_ok = row['Bull_Sweep']
                if v4 and in_golden and sweep_ok:
                    direction = 'BUY'; entry = row['close']
            else:  # NEW_V3
                v4 = row['low'] <= row['BB_Lower'] * 1.02
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                sweep_ok = row['Bull_Sweep'] and row['Volume_Spike']  # volume must be above avg
                if v4 and in_golden and sweep_ok:
                    direction = 'BUY'; entry = row['close']
        else:
            if method == 'OLD':
                v4 = row['high'] >= row['BB_Upper'] * 0.98
                ha_rev = (not row['HA_Bullish']) and df['HA_Bullish'].iloc[i-1] if i>0 else False
                if v4 and ha_rev:
                    direction = 'SELL'; entry = row['close']
            elif method == 'NEW_V2':
                v4 = row['high'] >= row['BB_Upper'] * 0.98
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                sweep_ok = row['Bear_Sweep']
                if v4 and in_golden and sweep_ok:
                    direction = 'SELL'; entry = row['close']
            else:  # NEW_V3
                v4 = row['high'] >= row['BB_Upper'] * 0.98
                in_golden = True
                if row['Golden_Low_1H'] and row['Golden_High_1H']:
                    in_golden = (row['Golden_Low_1H'] <= row['close'] <= row['Golden_High_1H'])
                sweep_ok = row['Bear_Sweep'] and row['Volume_Spike']
                if v4 and in_golden and sweep_ok:
                    direction = 'SELL'; entry = row['close']

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

# ── 5. Main ──────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading 90‑day data...")
    df_15m, df_1h = load_data(90)
    print(f"✅ Loaded {len(df_15m)} candles (15m)")

    df_15m = add_indicators(df_15m, df_1h).dropna()
    compute_session_filters(df_15m)

    methods = ['OLD', 'NEW_V2', 'NEW_V3']
    results = {}
    for m in methods:
        print(f"🔄 Backtesting {m}...")
        trades = run_backtest(df_15m, m)
        buy, sell = stats_by_dir(trades)
        results[m] = (buy, sell)

    # Print comparison tables
    print("\n" + "="*100)
    print("📊 BUY SIDE COMPARISON")
    print("="*100)
    header = f"{'':<20} {'OLD (HA15m)':<25} {'NEW_V2 (GF+Sweep)':<25} {'NEW_V3 (GF+Sweep+Vol)':<25}"
    print(header)
    print(f"{'Trades':<20} {results['OLD'][0]['total']:<25} {results['NEW_V2'][0]['total']:<25} {results['NEW_V3'][0]['total']:<25}")
    print(f"{'Win Rate':<20} {results['OLD'][0]['wr']:.1f}%{'':<21} {results['NEW_V2'][0]['wr']:.1f}%{'':<21} {results['NEW_V3'][0]['wr']:.1f}%")
    print(f"{'Total PnL':<20} {results['OLD'][0]['pnl']:+.2f}%{'':<20} {results['NEW_V2'][0]['pnl']:+.2f}%{'':<20} {results['NEW_V3'][0]['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{results['OLD'][0]['dd']:.2f}%{'':<21} -{results['NEW_V2'][0]['dd']:.2f}%{'':<21} -{results['NEW_V3'][0]['dd']:.2f}%")

    print("\n📊 SELL SIDE COMPARISON")
    print("="*100)
    header = f"{'':<20} {'OLD (HA15m)':<25} {'NEW_V2 (GF+Sweep)':<25} {'NEW_V3 (GF+Sweep+Vol)':<25}"
    print(header)
    print(f"{'Trades':<20} {results['OLD'][1]['total']:<25} {results['NEW_V2'][1]['total']:<25} {results['NEW_V3'][1]['total']:<25}")
    print(f"{'Win Rate':<20} {results['OLD'][1]['wr']:.1f}%{'':<21} {results['NEW_V2'][1]['wr']:.1f}%{'':<21} {results['NEW_V3'][1]['wr']:.1f}%")
    print(f"{'Total PnL':<20} {results['OLD'][1]['pnl']:+.2f}%{'':<20} {results['NEW_V2'][1]['pnl']:+.2f}%{'':<20} {results['NEW_V3'][1]['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{results['OLD'][1]['dd']:.2f}%{'':<21} -{results['NEW_V2'][1]['dd']:.2f}%{'':<21} -{results['NEW_V3'][1]['dd']:.2f}%")
    print("="*100)

    total_old = results['OLD'][0]['pnl'] + results['OLD'][1]['pnl']
    total_v2 = results['NEW_V2'][0]['pnl'] + results['NEW_V2'][1]['pnl']
    total_v3 = results['NEW_V3'][0]['pnl'] + results['NEW_V3'][1]['pnl']
    print(f"\n💰 Total PnL (Buy+Sell): OLD = {total_old:+.2f}% | NEW_V2 = {total_v2:+.2f}% | NEW_V3 = {total_v3:+.2f}%")
