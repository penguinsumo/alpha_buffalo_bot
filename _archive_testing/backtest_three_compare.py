#!/usr/bin/env python3
"""Compare v11.2 (BE+Trailing), New V4, Hybrid on GC=F 60 days real data"""
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
    # Golden Zone
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max(); lows = df1h['low'].rolling(5).min()
        sw_high = highs.max(); sw_low = lows.min()
    else: sw_high = sw_low = 0
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = sw_high - sw_low
    return df

# ── Session Logic ────────────────────────────────
def is_valid_session_v112(hour): return 12 <= hour <= 22
def is_valid_session_new(hour): return 1 <= hour < 19  # Asia, London, NY (no pre-close)

# ── Trade Functions ──────────────────────────────
def v112_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; hour = row.name.hour
        if not is_valid_session_v112(hour): continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02:
                direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98:
                direction='SELL'; entry=row['close']
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
        trades.append({'dir':direction,'entry':entry,'exit':exit_price,'sl':sl})
    return trades

def new_v4_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row=df.iloc[i]; hour=row.name.hour
        if not is_valid_session_new(hour): continue
        # BUY (Golden Zone)
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
                trades.append({'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl})
        # SELL (Visual SL)
        if (row['EMA20']<row['EMA50'] and row['Bear_Sweep'] and row['high']>=row['BB_Upper']*0.98):
            entry=row['close']; sl=entry+row['ATR14']*1.5; mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl})
    return trades

def hybrid_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; hour = row.name.hour
        if not is_valid_session_new(hour): continue
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02:
                direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98:
                direction='SELL'; entry=row['close']
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
        else: # SELL Visual SL
            sl = entry + row['ATR14']*1.5; mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'dir':direction,'entry':entry,'exit':exit_price,'sl':sl})
    return trades

# ── Position Sizing ──────────────────────────────
def simulate_equity(trades, initial=10000, risk_pct=0.01, max_contracts=10):
    equity=initial; curve=[initial]; result=[]
    for t in trades:
        sl_dist = abs(t['entry']-t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = equity * risk_pct
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, max_contracts); contracts = max(contracts, 0.01)
        pnl_pts = (t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        equity += pnl_dollar
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t, 'contracts':contracts, 'pnl_$':pnl_dollar, 'equity':equity})
    return result, curve

def stats(curve, trades_result, initial):
    if not curve: return {}
    final_eq = curve[-1]; ret = (final_eq/initial - 1)*100
    peak=initial; max_dd=0
    for eq in curve:
        if eq>peak: peak=eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        if dd>max_dd: max_dd=dd
    wins = [t for t in trades_result if t['pnl_$']>0]
    wr = len(wins)/len(trades_result)*100 if trades_result else 0
    gross_profit = sum(t['pnl_$'] for t in trades_result if t['pnl_$']>0)
    gross_loss = abs(sum(t['pnl_$'] for t in trades_result if t['pnl_$']<0))
    pf = gross_profit/gross_loss if gross_loss>0 else float('inf')
    return {'final':final_eq,'return':ret,'dd':max_dd,'wr':wr,'pf':pf,'total':len(trades_result)}

# ── Run ────────────────────────────────────────────
df_15m = add_indicators(df).dropna()
print("Running v11.2..."); t1, c1 = simulate_equity(v112_trades(df_15m)); s1 = stats(c1, t1, 10000)
print("Running New V4..."); t2, c2 = simulate_equity(new_v4_trades(df_15m)); s2 = stats(c2, t2, 10000)
print("Running Hybrid..."); t3, c3 = simulate_equity(hybrid_trades(df_15m)); s3 = stats(c3, t3, 10000)

print("\n📊 COMPARISON (GC=F 60 days, 1% Risk)")
print("="*80)
print(f"{'Metric':<20} {'v11.2 (BE+Trail)':<20} {'New V4':<20} {'Hybrid':<20}")
print(f"{'Final Equity':<20} ${s1['final']:<19,.2f} ${s2['final']:<19,.2f} ${s3['final']:<19,.2f}")
print(f"{'Return':<20} {s1['return']:<19.2f}% {s2['return']:<19.2f}% {s3['return']:<19.2f}%")
print(f"{'Max DD':<20} {s1['dd']:<19.2f}% {s2['dd']:<19.2f}% {s3['dd']:<19.2f}%")
print(f"{'Win Rate':<20} {s1['wr']:<19.2f}% {s2['wr']:<19.2f}% {s3['wr']:<19.2f}%")
print(f"{'Profit Factor':<20} {s1['pf']:<19.2f} {s2['pf']:<19.2f} {s3['pf']:<19.2f}")
print(f"{'Total Trades':<20} {s1['total']:<19} {s2['total']:<19} {s3['total']:<19}")
