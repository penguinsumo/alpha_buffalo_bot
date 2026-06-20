#!/usr/bin/env python3
"""วิเคราะห์ไม้แพ้ของแต่ละเวอร์ชัน แยกตาม Session และ Buy/Sell"""
import yfinance as yf, pandas as pd, numpy as np
from datetime import datetime, timedelta

# ── 1. Download & Clean ──────────────────────────
print("📡 Downloading GC=F 15m (60 days)...")
end = datetime.now(); start = end - timedelta(days=60)
df = yf.download("GC=F", start=start, end=end, interval="15m")
if df.empty: raise ValueError("No data")
if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
df.columns = [c.lower() for c in df.columns]
if not isinstance(df.index, pd.DatetimeIndex): df.index = pd.to_datetime(df.index)
for c in ['open','high','low','close','volume']:
    if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
df = df[['open','high','low','close','volume']].dropna()
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
    df['Low_Prev'] = df['low'].shift(1); df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max(); lows = df1h['low'].rolling(5).min()
        sw_high = highs.max(); sw_low = lows.min()
    else: sw_high = sw_low = 0
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = sw_high - sw_low
    return df

# ── Session Logic (ไม่ตัด Overlap) ───────────────
def get_session(ts):
    hour = ts.hour
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    else: return 'ASIA_LOW'  # 19:00-01:00 UTC

# ── Trade Functions (เหมือนเดิม ใช้ Session Filter ของแต่ละเวอร์ชัน) ─
# แต่เราจะเปลี่ยนให้ทุกเวอร์ชันเปิดเทรดได้ทุก Session (เพื่อเปรียบเทียบกันอย่างยุติธรรม)
# ดังนั้นเราจะกำหนด session filter ใหม่สำหรับ Backtest นี้:
def universal_session(ts):
    """เปิดเทรดทุก Session ยกเว้น ASIA_LOW (เพื่อดูเฉพาะช่วงหลัก)"""
    hour = ts.hour
    return 1 <= hour < 19  # ASIA + LONDON + NY (ไม่มี Overlap cut)

# เราจะใช้ universal_session สำหรับทั้ง 3 เวอร์ชัน
def v112_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if not universal_session(ts): continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02: direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98: direction='SELL'; entry=row['close']
        if direction is None: continue
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act=False; hi=lo=entry; exit_price=entry
        for j in range(i+1, min(i+40, len(df))):
            r=df.iloc[j]; h,l=r['high'],r['low']
            if direction=='BUY':
                if h>hi: hi=h
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
                if be_act: sl=max(sl, hi*0.9995)
                if h>=tp: exit_price=tp; break
                if l<=sl: exit_price=sl; break
            else:
                if l<lo: lo=l
                if not be_act and lo<=entry*0.9990: be_act=True; sl=entry
                if be_act: sl=min(sl, lo*1.0005)
                if l<=tp: exit_price=tp; break
                if h>=sl: exit_price=sl; break
        else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
        pnl_pts = (exit_price - entry) if direction=='BUY' else (entry - exit_price)
        trades.append({'dir':direction,'session':get_session(ts),'pnl_pts':pnl_pts})
    return trades

def new_v4_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row=df.iloc[i]; ts=row.name
        if not universal_session(ts): continue
        if (row['EMA20']>row['EMA50'] and row['Swing_H']>row['Swing_L'] and row['Diff']>0):
            gl=row['Swing_H']-row['Diff']*1.0; gh=row['Swing_H']-row['Diff']*0.5
            if gl<=row['close']<=gh and row['Bull_Sweep'] and row['low']<=row['BB_Lower']*1.02:
                entry=row['close']; sl=entry-row['ATR14']*1.5; tp=row['BB_Upper']
                be_act=False; highest=entry; exit_price=entry
                for j in range(i+1, min(i+40, len(df))):
                    r=df.iloc[j]; h,l=r['high'],r['low']
                    if h>highest: highest=h
                    if not be_act and highest>=entry*1.0015: be_act=True; sl=entry
                    if be_act: sl=max(sl,highest*0.9995)
                    if h>=tp: exit_price=tp; break
                    if l<=sl: exit_price=sl; break
                else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
                pnl_pts = exit_price - entry
                trades.append({'dir':'BUY','session':get_session(ts),'pnl_pts':pnl_pts})
        if (row['EMA20']<row['EMA50'] and row['Bear_Sweep'] and row['high']>=row['BB_Upper']*0.98):
            entry=row['close']; sl=entry+row['ATR14']*1.5; mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
            pnl_pts = entry - exit_price
            trades.append({'dir':'SELL','session':get_session(ts),'pnl_pts':pnl_pts})
    return trades

def hybrid_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if not universal_session(ts): continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02: direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98: direction='SELL'; entry=row['close']
        if direction is None: continue
        if direction == 'BUY':
            sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
            be_act=False; highest=entry; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if h>highest: highest=h
                if not be_act and highest>=entry*1.0015: be_act=True; sl=entry
                if be_act: sl=max(sl,highest*0.9995)
                if h>=tp: exit_price=tp; break
                if l<=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
            pnl_pts = exit_price - entry
            trades.append({'dir':'BUY','session':get_session(ts),'pnl_pts':pnl_pts})
        else:
            sl = entry + row['ATR14']*1.5; mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
            pnl_pts = entry - exit_price
            trades.append({'dir':'SELL','session':get_session(ts),'pnl_pts':pnl_pts})
    return trades

# ── Analysis Function ────────────────────────────
def analyze_losses(trades, version_name):
    df = pd.DataFrame(trades)
    # เฉพาะไม้ที่ขาดทุน (pnl_pts < 0)
    losses = df[df['pnl_pts'] < 0].copy()
    losses['abs_pnl'] = -losses['pnl_pts']  # ค่า DD เป็นบวก
    total_dd = losses['abs_pnl'].sum()
    
    print(f"\n{'='*60}")
    print(f"📉 {version_name} - Losing Trades Breakdown (All Sessions, no Overlap Cut)")
    print(f"Total losing trades: {len(losses)} | Total DD (points): {total_dd:.2f}")
    
    # แยกตาม Session
    for ses in ['ASIA','LONDON','NY','ASIA_LOW']:
        sub = losses[losses['session'] == ses]
        if sub.empty:
            print(f"  {ses}: 0 trades")
            continue
        count = len(sub)
        dd_sum = sub['abs_pnl'].sum()
        pct = (dd_sum / total_dd * 100) if total_dd > 0 else 0
        print(f"  {ses}: {count} trades, DD={dd_sum:.2f} pts ({pct:.1f}% of total DD)")
        # แยก Buy/Sell
        for d in ['BUY','SELL']:
            sub_d = sub[sub['dir'] == d]
            if not sub_d.empty:
                print(f"    {d}: {len(sub_d)} trades, DD={sub_d['abs_pnl'].sum():.2f} pts")

# ── Run ──────────────────────────────────────────
df_15m = add_indicators(df).dropna()
print("Generating trades...")
t_v = v112_trades(df_15m); analyze_losses(t_v, "v11.2")
t_n = new_v4_trades(df_15m); analyze_losses(t_n, "New V4")
t_h = hybrid_trades(df_15m); analyze_losses(t_h, "Hybrid")
