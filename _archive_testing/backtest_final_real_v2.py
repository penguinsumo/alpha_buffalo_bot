#!/usr/bin/env python3
"""FINAL: v11.2 vs New V4 on REAL GC=F 60 days (direct yfinance, robust)"""
import yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta

# ── 1. Download & Clean ──────────────────────────
print("📡 Downloading GC=F 15m (60 days)...")
end = datetime.now()
start = end - timedelta(days=60)
df = yf.download("GC=F", start=start, end=end, interval="15m")
if df.empty:
    raise ValueError("No data")

# Flatten MultiIndex columns if any
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)
# Lowercase column names
df.columns = [c.lower() for c in df.columns]
# Ensure DatetimeIndex
if not isinstance(df.index, pd.DatetimeIndex):
    df.index = pd.to_datetime(df.index)
# Select required columns
cols = ['open','high','low','close','volume']
df = df[[c for c in cols if c in df.columns]]
# Remove any remaining non-numeric
for c in df.columns:
    df[c] = pd.to_numeric(df[c], errors='coerce')
df = df.dropna()
print(f"✅ Clean data: {len(df)} bars")

# ── 2. Indicators ────────────────────────────────
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
    # 1H Swing
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
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    return df

def get_session(hour_utc):
    if 1 <= hour_utc < 8: return 'ASIA'
    elif 8 <= hour_utc < 13: return 'LONDON'
    elif 13 <= hour_utc < 19: return 'NY'
    return 'ASIA_LOW'

# ── 3. v11.2 Logic ──────────────────────────────
def v112_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; hour = row.name.hour
        if not (12 <= hour <= 22): continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02:
                direction = 'BUY'; entry = row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98:
                direction = 'SELL'; entry = row['close']
        if direction is None: continue
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act=False; hi=lo=entry; pnl=0.0
        for j in range(i+1, min(i+40, len(df))):
            r=df.iloc[j]; h,l=r['high'],r['low']
            if direction=='BUY':
                if h>hi: hi=h
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
                if be_act: sl=max(sl, hi*0.9995)
                if h>=tp: pnl=(tp-entry)/entry*100; break
                if l<=sl: pnl=(sl-entry)/entry*100; break
            else:
                if l<lo: lo=l
                if not be_act and lo<=entry*0.9990: be_act=True; sl=entry
                if be_act: sl=min(sl, lo*1.0005)
                if l<=tp: pnl=(entry-tp)/entry*100; break
                if h>=sl: pnl=(entry-sl)/entry*100; break
        else:
            last=df.iloc[min(i+40-1, len(df)-1)]['close']
            pnl=(last-entry)/entry*100 if direction=='BUY' else (entry-last)/entry*100
        trades.append({'dir':direction,'pnl':pnl,'session':get_session(hour)})
    return trades

# ── 4. New V4 Logic ──────────────────────────────
def new_v4_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row=df.iloc[i]; hour=row.name.hour; session=get_session(hour)
        # BUY (No618+100)
        if (row['EMA20']>row['EMA50'] and row['Swing_H']>row['Swing_L'] and row['Diff']>0):
            gl=row['Swing_H']-row['Diff']*1.0
            gh=row['Swing_H']-row['Diff']*0.5
            if gl<=row['close']<=gh and row['Bull_Sweep'] and row['low']<=row['BB_Lower']*1.02:
                entry=row['close']; sl=entry-row['ATR14']*1.5; tp=row['BB_Upper']
                be_act=False; highest=entry; pnl=0.0
                for j in range(i+1, min(i+40, len(df))):
                    r=df.iloc[j]; h,l=r['high'],r['low']
                    if h>highest: highest=h
                    if not be_act and highest>=entry*1.0015: be_act=True; sl=entry
                    if be_act: sl=max(sl,highest*0.9995)
                    if h>=tp: pnl=(tp-entry)/entry*100; break
                    if l<=sl: pnl=(sl-entry)/entry*100; break
                else:
                    last=df.iloc[min(i+40-1, len(df)-1)]['close']
                    pnl=(last-entry)/entry*100
                trades.append({'dir':'BUY','pnl':pnl,'session':session})
        # SELL (Visual SL)
        if (row['EMA20']<row['EMA50'] and row['Bear_Sweep'] and row['high']>=row['BB_Upper']*0.98):
            entry=row['close']; sl=entry+row['ATR14']*1.5; mid_crossed=False; pnl=0.0
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']:
                    mid_crossed=True; sl=entry
                if l<=r['BB_Lower']:
                    pnl=(entry-r['BB_Lower'])/entry*100; break
                if h>=sl:
                    pnl=(entry-sl)/entry*100; break
            else:
                last=df.iloc[min(i+40-1, len(df)-1)]['close']
                pnl=(entry-last)/entry*100
            trades.append({'dir':'SELL','pnl':pnl,'session':session})
    return trades

# ── Stats ─────────────────────────────────────────
def stats(trades):
    if not trades: return {'trades':0,'wr':0,'pnl':0,'dd':0}
    pnls=[t['pnl'] for t in trades]
    total=len(pnls); wins=[p for p in pnls if p>0]
    wr=len(wins)/total*100; pnl=sum(pnls)
    cum=0;peak=0;dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':total,'wr':wr,'pnl':pnl,'dd':dd}

def session_breakdown(trades):
    sessions=['ASIA','LONDON','NY','ASIA_LOW']
    return {s:stats([t for t in trades if t['session']==s]) for s in sessions}

# ── Run ───────────────────────────────────────────
df_15m = add_indicators(df).dropna()
orig = v112_trades(df_15m)
new = new_v4_trades(df_15m)

so=stats(orig); sn=stats(new)
so_s=session_breakdown(orig); sn_s=session_breakdown(new)

print("\n📊 OVERALL (GC=F 60d real)")
print(f"{'':<20} {'v11.2':<20} {'New V4':<20}")
print(f"{'Trades':<20} {so['trades']:<20} {sn['trades']:<20}")
print(f"{'Win Rate':<20} {so['wr']:.1f}%{'':<15} {sn['wr']:.1f}%")
print(f"{'Total PnL':<20} {so['pnl']:+.2f}%{'':<15} {sn['pnl']:+.2f}%")
print(f"{'Max DD':<20} -{so['dd']:.2f}%{'':<15} -{sn['dd']:.2f}%")

print("\n📊 BY SESSION")
for ses in ['ASIA','LONDON','NY','ASIA_LOW']:
    o=so_s[ses]; n=sn_s[ses]
    print(f"\n--- {ses} ---")
    print(f"{'':<20} {'v11.2':<20} {'New V4':<20}")
    print(f"{'Trades':<20} {o['trades']:<20} {n['trades']:<20}")
    print(f"{'Win Rate':<20} {o['wr']:.1f}%{'':<15} {n['wr']:.1f}%")
    print(f"{'PnL':<20} {o['pnl']:+.2f}%{'':<15} {n['pnl']:+.2f}%")
    print(f"{'DD':<20} -{o['dd']:.2f}%{'':<15} -{n['dd']:.2f}%")
