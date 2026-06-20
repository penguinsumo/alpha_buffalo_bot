#!/usr/bin/env python3
"""
เปรียบเทียบ v11.2 vs New V4 บนข้อมูลจริง GC=F 60 วัน
แยกตาม Session / Buy / Sell
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import yfinance as yf
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ── 1. โหลดข้อมูลจริง GC=F 60 วัน ────────────────
def load_real_data():
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=60)
    ticker = "GC=F"
    logger.info(f"Downloading {ticker} 15m from {start.date()} to {end.date()}...")
    df = yf.download(ticker, start=start, end=end, interval="15m")
    if df.empty:
        raise ValueError("Empty DataFrame")
    df = df.rename(columns={"Open":"open","High":"high","Low":"low","Close":"close","Volume":"volume"})
    df = df[['open','high','low','close','volume']]
    df.index = pd.to_datetime(df.index)
    if df.index.tz is None:
        df.index = df.index.tz_localize('UTC')
    else:
        df.index = df.index.tz_convert('UTC')
    logger.info(f"✅ Loaded {len(df)} candles from {df.index[0]} to {df.index[-1]}")
    return df

# ── 2. Indicators ──────────────────────────────────
def add_indicators(df):
    df = df.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    h,l,c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()

    # 1H Swing สำหรับ New V4
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max()
        lows = df1h['low'].rolling(5).min()
        sw_high = highs.max()
        sw_low = lows.min()
    else:
        sw_high = sw_low = 0
    df['Swing_H'] = sw_high
    df['Swing_L'] = sw_low
    df['Diff'] = sw_high - sw_low

    # Sweep
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    return df

# ── 3. Session helpers ──────────────────────────────
def get_session(hour_utc):
    if 1 <= hour_utc < 8: return 'ASIA'
    elif 8 <= hour_utc < 13: return 'LONDON'
    elif 13 <= hour_utc < 19: return 'NY'
    return 'ASIA_LOW'

# ── 4. Original v11.2 ───────────────────────────────
def original_v112_trades(df):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        hour = row.name.hour
        if not (12 <= hour <= 22):
            continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02:
                direction = 'BUY'; entry = row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98:
                direction = 'SELL'; entry = row['close']
        if direction is None: continue
        sl = (entry - row['ATR14'] * 1.5) if direction=='BUY' else (entry + row['ATR14'] * 1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act = False; hi=lo=entry
        pnl = 0.0
        for j in range(i+1, min(i+max_bars, len(df))):
            r = df.iloc[j]; h,l = r['high'], r['low']
            if direction=='BUY':
                if h>hi: hi=h
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
                if be_act: sl = max(sl, hi*0.9995)
                if h>=tp: pnl=(tp-entry)/entry*100; break
                if l<=sl: pnl=(sl-entry)/entry*100; break
            else:
                if l<lo: lo=l
                if not be_act and lo<=entry*0.9990: be_act=True; sl=entry
                if be_act: sl = min(sl, lo*1.0005)
                if l<=tp: pnl=(entry-tp)/entry*100; break
                if h>=sl: pnl=(entry-sl)/entry*100; break
        else:
            last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
            pnl = (last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100
        trades.append({'direction':direction,'pnl':pnl,'session':get_session(hour)})
    return trades

# ── 5. New V4 ───────────────────────────────────────
def new_v4_trades(df):
    trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df)-max_bars):
        row = df.iloc[i]
        hour = row.name.hour
        session = get_session(hour)
        # BUY (No618+100)
        if (row['EMA20'] > row['EMA50'] and row['Swing_H'] > row['Swing_L'] and row['Diff'] > 0):
            golden_low = row['Swing_H'] - row['Diff']*1.0
            golden_high = row['Swing_H'] - row['Diff']*0.5
            if (golden_low <= row['close'] <= golden_high and
                row['Bull_Sweep'] and row['low'] <= row['BB_Lower']*1.02):
                entry = row['close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
                be_act=False; highest=entry
                pnl=0.0
                for j in range(i+1, min(i+max_bars, len(df))):
                    r = df.iloc[j]; h,l = r['high'], r['low']
                    if h>highest: highest=h
                    if not be_act and highest>=entry*1.0015: be_act=True; sl=entry
                    if be_act: sl = max(sl, highest*0.9995)
                    if h>=tp: pnl=(tp-entry)/entry*100; break
                    if l<=sl: pnl=(sl-entry)/entry*100; break
                else:
                    last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
                    pnl = (last-entry)/entry*100
                trades.append({'direction':'BUY','pnl':pnl,'session':session})
        # SELL (Visual SL)
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and
            row['high'] >= row['BB_Upper']*0.98):
            entry = row['close']; sl = entry + row['ATR14']*1.5
            mid_crossed=False; pnl=0.0
            for j in range(i+1, min(i+max_bars, len(df))):
                r = df.iloc[j]; h,l = r['high'], r['low']
                if not mid_crossed and l <= r['BB_Mid']:
                    mid_crossed=True; sl = entry
                if l <= r['BB_Lower']:
                    pnl = (entry - r['BB_Lower'])/entry*100; break
                if h >= sl:
                    pnl = (entry - sl)/entry*100; break
            else:
                last = df.iloc[min(i+max_bars-1, len(df)-1)]['close']
                pnl = (entry - last)/entry*100
            trades.append({'direction':'SELL','pnl':pnl,'session':session})
    return trades

# ── 6. Stats ────────────────────────────────────────
def compute_stats(trades):
    if not trades: return {'trades':0,'wr':0,'pnl':0,'dd':0}
    pnls = [t['pnl'] for t in trades]
    total = len(pnls); wins = [p for p in pnls if p>0]
    wr = len(wins)/total*100; pnl = sum(pnls)
    cum=0; peak=0; dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':total,'wr':wr,'pnl':pnl,'dd':dd}

def session_breakdown(trades):
    sessions = ['ASIA','LONDON','NY','ASIA_LOW']
    result = {}
    for ses in sessions:
        ses_trades = [t for t in trades if t['session']==ses]
        result[ses] = compute_stats(ses_trades)
    return result

# ── 7. Main ─────────────────────────────────────────
if __name__ == "__main__":
    logger.info("กำลังโหลดข้อมูลจริง GC=F 60 วัน...")
    df_15m = load_real_data()
    df_15m = add_indicators(df_15m).dropna()
    logger.info(f"หลังคำนวณ Indicator: {len(df_15m)} แท่ง")

    logger.info("กำลังทดสอบ Original v11.2...")
    trades_orig = original_v112_trades(df_15m)
    logger.info("กำลังทดสอบ New V4...")
    trades_new = new_v4_trades(df_15m)

    orig_stats = compute_stats(trades_orig)
    new_stats = compute_stats(trades_new)
    orig_session = session_breakdown(trades_orig)
    new_session = session_breakdown(trades_new)

    print("\n" + "="*80)
    print("📊 เปรียบเทียบ Original v11.2 (ซ้าย) vs New V4 (ขวา) — GC=F 60 วัน")
    print("="*80)
    print(f"{'ภาพรวม':<20} {'v11.2':<25} {'New V4':<25}")
    print(f"{'Trades':<20} {orig_stats['trades']:<25} {new_stats['trades']:<25}")
    print(f"{'Win Rate':<20} {orig_stats['wr']:.1f}%{'':<21} {new_stats['wr']:.1f}%")
    print(f"{'Total PnL':<20} {orig_stats['pnl']:+.2f}%{'':<20} {new_stats['pnl']:+.2f}%")
    print(f"{'Max DD':<20} -{orig_stats['dd']:.2f}%{'':<21} -{new_stats['dd']:.2f}%")

    print("\n📊 แยกตาม Session (v11.2 / New V4)")
    print("="*80)
    for ses in ['ASIA','LONDON','NY','ASIA_LOW']:
        o = orig_session[ses]
        n = new_session[ses]
        print(f"\n--- {ses} ---")
        print(f"{'':<20} {'v11.2 Trades':<12} {'v11.2 PnL':<12} {'New V4 Trades':<14} {'New V4 PnL':<12}")
        buy_orig = sum(t['pnl'] for t in trades_orig if t['session']==ses and t['direction']=='BUY')
        sell_orig = sum(t['pnl'] for t in trades_orig if t['session']==ses and t['direction']=='SELL')
        buy_new = sum(t['pnl'] for t in trades_new if t['session']==ses and t['direction']=='BUY')
        sell_new = sum(t['pnl'] for t in trades_new if t['session']==ses and t['direction']=='SELL')
        print(f"{'BUY':<20} {len([t for t in trades_orig if t['session']==ses and t['direction']=='BUY']):<12} {buy_orig:+.2f}%{'':<6} {len([t for t in trades_new if t['session']==ses and t['direction']=='BUY']):<14} {buy_new:+.2f}%")
        print(f"{'SELL':<20} {len([t for t in trades_orig if t['session']==ses and t['direction']=='SELL']):<12} {sell_orig:+.2f}%{'':<6} {len([t for t in trades_new if t['session']==ses and t['direction']=='SELL']):<14} {sell_new:+.2f}%")
