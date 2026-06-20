#!/usr/bin/env python3
"""
backtest_v2_618_ha5m.py — NEW_V2 with HA 5m reversal filter for 0.618 zone
เฉพาะ 0.618 zone: HA 5m แท่งล่าสุดเขียว และ 2 แท่งก่อนหน้าแดง (กลับตัวจากแดง)
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

# ── HA calculation ────────────────────────────────────
def heikin_ashi(df):
    ha_close = (df['open']+df['high']+df['low']+df['close'])/4
    ha_open = ha_close.copy()
    ha_open.iloc[0] = df['open'].iloc[0]
    for i in range(1,len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1])/2
    return pd.DataFrame({'HA_Open':ha_open, 'HA_Close':ha_close}, index=df.index)

# ── Indicators ────────────────────────────────────────
def add_indicators(df15, df1h, df5):
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

    # HA 5m on df5
    ha5 = heikin_ashi(df5)
    df5['HA_Open'] = ha5['HA_Open']
    df5['HA_Close'] = ha5['HA_Close']
    df5['HA_Red'] = df5['HA_Close'] < df5['HA_Open']  # red candle
    df5['HA_Green'] = df5['HA_Close'] > df5['HA_Open'] # green candle

    # Golden Fibo 1H
    df1h = df1h.copy()
    highs = df1h['high'].rolling(5).max()
    lows = df1h['low'].rolling(5).min()
    sw_high = highs.max()
    sw_low = lows.min()
    if sw_high > sw_low:
        diff = sw_high - sw_low
        gl = sw_high - diff*0.786
        gh = sw_high - diff*0.5
    else:
        gl = gh = None
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low

    # Sweep
    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15, df5

# ── HA 5m reversal check ─────────────────────────────
def ha5m_reversal_618(df5, ts):
    """
    Check HA 5m at or just before timestamp ts (close of 15m bar).
    ต้องการ: แท่งล่าสุด (ที่จบพร้อม ts) เป็นเขียว และ 2 แท่งก่อนหน้าเป็นแดง
    """
    # get HA bars up to ts
    ha_bars = df5[df5.index <= ts].tail(3)  # last 3 completed 5m bars
    if len(ha_bars) < 3:
        return False
    # check: last bar green, previous two red
    last3 = ha_bars[['HA_Green','HA_Red']].values
    return last3[2,0] and last3[1,1] and last3[0,1]  # green, red, red

# ── Backtest ─────────────────────────────────────────
def backtest(df15, df5, use_ha5m_618=False):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        utc_hour = row.name.hour
        # Session filter
        if utc_hour < 12 or utc_hour > 22:
            if not (0 <= utc_hour <= 6):
                continue
            if not (row['Swing_H'] and row['Swing_L'] and row['Swing_H'] > row['Swing_L']):
                continue
            # golden zone check
            diff = row['Swing_H'] - row['Swing_L']
            gl = row['Swing_H'] - diff*0.786
            gh = row['Swing_H'] - diff*0.5
            if not (gl <= row['close'] <= gh):
                continue
            if row['EMA20'] > row['EMA50']:
                if not (row['low'] <= row['BB_Lower']*1.02):
                    continue
            else:
                if not (row['high'] >= row['BB_Upper']*0.98):
                    continue

        direction = entry = sl = tp = None
        fibo_level = np.nan
        if row['EMA20'] > row['EMA50']:  # BUY
            if row['Bull_Sweep']:
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['Swing_H'] - row['close']) / (row['Swing_H'] - row['Swing_L'])
                if use_ha5m_618 and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    if not ha5m_reversal_618(df5, row.name):
                        continue
                direction = 'BUY'; entry = row['close']
        else:  # SELL
            if row['Bear_Sweep']:
                if row['Swing_H'] and row['Swing_L'] and row['Swing_H'] != row['Swing_L']:
                    fibo_level = (row['Swing_H'] - row['close']) / (row['Swing_H'] - row['Swing_L'])
                if use_ha5m_618 and fibo_level is not np.nan and 0.5 <= fibo_level <= 0.618:
                    # for sell, need HA reversal: green->red pattern: last red, prev two green
                    # we'll check opposite: last red, previous two green
                    ha_bars = df5[df5.index <= row.name].tail(3)
                    if len(ha_bars) < 3:
                        continue
                    last3 = ha_bars[['HA_Red','HA_Green']].values
                    if not (last3[2,0] and last3[1,1] and last3[0,1]):  # red, green, green
                        continue
                direction = 'SELL'; entry = row['close']

        if direction is None:
            continue

        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act, hi, lo = False, entry, entry
        pnl, exit_reason, first_sl = 0.0, 'TIMEOUT', False
        for j in range(i+1, min(i+max_bars, len(df15))):
            r = df15.iloc[j]; h, l = r['high'], r['low']
            if direction == 'BUY':
                if h >= tp: pnl = (tp-entry)/entry*100; exit_reason='TP'; break
                elif l <= sl:
                    if not be_act: first_sl = True
                    pnl = (sl-entry)/entry*100; exit_reason='SL'; break
                if not be_act and h >= entry*1.0015: be_act=True; sl=entry
                if h > hi: hi = h
                if be_act: sl = max(sl, hi*0.9995)
            else:
                if l <= tp: pnl = (entry-tp)/entry*100; exit_reason='TP'; break
                elif h >= sl:
                    if not be_act: first_sl = True
                    pnl = (entry-sl)/entry*100; exit_reason='SL'; break
                if not be_act and l <= entry*0.9985: be_act=True; sl=entry
                if l < lo: lo = l
                if be_act: sl = min(sl, lo*1.0005)
        else:
            last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100

        rr = abs((tp-entry)/(entry-sl)) if entry != sl else 0
        trades.append({
            'dir': direction, 'pnl': pnl, 'exit': exit_reason,
            'first_sl': first_sl, 'rr': rr, 'fibo': fibo_level
        })
    return pd.DataFrame(trades)

def stats(trades_df):
    total = len(trades_df)
    if total==0: return {'Total':0,'Win%':0,'PnL%':0,'DD%':0,'FirstSL':0,'AvgRR':0}
    wins = trades_df[trades_df['pnl']>0]
    wr = len(wins)/total*100
    pnl = trades_df['pnl'].sum()
    cum, peak, dd = 0,0,0
    for p in trades_df['pnl']:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    first_sl = trades_df['first_sl'].sum()
    avg_rr = trades_df['rr'].mean()
    return {'Total':total,'Win%':wr,'PnL%':pnl,'DD%':dd,'FirstSL':first_sl,'AvgRR':avg_rr}

def fibo_breakdown(trades_df):
    bins = [0.50, 0.618, 0.72, 0.78, 0.86]
    labels = ['0.618', '0.72', '0.78', '0.86']
    trades_df['fibo_bin'] = pd.cut(trades_df['fibo'], bins=bins, labels=labels, right=True)
    result = {}
    for lvl in labels:
        sub = trades_df[trades_df['fibo_bin']==lvl]
        result[lvl] = stats(sub)
    return result

# ── Main ──────────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Generating synthetic 5m data...")
    df5 = generate_5min_data(90)
    df15, df1h = resample_ohlc(df5)
    df15, df5 = add_indicators(df15, df1h, df5)
    df15.dropna(inplace=True)
    print(f"✅ 15m bars: {len(df15)}")

    # Original
    print("🔄 Backtesting NEW_V2 (original)...")
    trades_orig = backtest(df15, df5, use_ha5m_618=False)
    stats_orig = stats(trades_orig)

    # With HA 5m reversal for 618
    print("🔄 Backtesting NEW_V2 (HA5m reversal for 0.618)...")
    trades_ha5m = backtest(df15, df5, use_ha5m_618=True)
    stats_ha5m = stats(trades_ha5m)

    print("\n📊 OVERALL COMPARISON")
    print("="*80)
    print(f"{'':<12} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}")
    print(f"{'Original':<12} {stats_orig['Total']:<8} {stats_orig['Win%']:<8.1f} {stats_orig['PnL%']:<+10.2f} {stats_orig['DD%']:<10.2f} {stats_orig['FirstSL']:<8} {stats_orig['AvgRR']:<8.2f}")
    print(f"{'HA5m-618':<12} {stats_ha5m['Total']:<8} {stats_ha5m['Win%']:<8.1f} {stats_ha5m['PnL%']:<+10.2f} {stats_ha5m['DD%']:<10.2f} {stats_ha5m['FirstSL']:<8} {stats_ha5m['AvgRR']:<8.2f}")

    print("\n📊 FIBO BREAKDOWN (HA5m-618)")
    print("="*80)
    fibo_new = fibo_breakdown(trades_ha5m)
    print(f"{'Level':<10} {'Trades':<8} {'Win%':<8} {'PnL%':<10} {'DD%':<10} {'FirstSL':<8} {'AvgRR':<8}")
    for lvl in ['0.618','0.72','0.78','0.86']:
        s = fibo_new[lvl]
        print(f"{lvl:<10} {s['Total']:<8} {s['Win%']:<8.1f} {s['PnL%']:<+10.2f} {s['DD%']:<10.2f} {s['FirstSL']:<8} {s['AvgRR']:<8.2f}")
