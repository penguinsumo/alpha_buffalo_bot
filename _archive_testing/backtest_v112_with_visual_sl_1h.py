#!/usr/bin/env python3
"""
v11.2 (1H) + Visual SL (New V4) on Twelve Data (or GC=F fallback) 60 days
"""
import pandas as pd, numpy as np
from datetime import datetime, timedelta

# ── 1. Load Data ──────────────────────────────────
print("📡 Loading Twelve Data 1H...")
try:
    from data_provider_twelvedata import fetch_twelvedata
    df_15m, df_1h, df_4h = fetch_twelvedata()
    if df_1h is not None and len(df_1h) > 10:
        cutoff = df_1h.index.max() - pd.Timedelta(days=60)
        df_1h = df_1h[df_1h.index >= cutoff]
        print(f"✅ Twelve Data 1H: {len(df_1h)} candles")
    else:
        raise ValueError("Not enough 1H data")
except Exception as e:
    print(f"⚠️ Twelve Data failed ({e}), using GC=F 15m resampled to 1H...")
    import yfinance as yf
    end = datetime.now(); start = end - timedelta(days=60)
    df = yf.download("GC=F", start=start, end=end, interval="15m")
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex): df.index = pd.to_datetime(df.index)
    for c in ['open','high','low','close','volume']:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df[['open','high','low','close','volume']].dropna()
    df_1h = df.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    print(f"✅ GC=F resampled to 1H: {len(df_1h)} candles")

# ── 2. Indicators ─────────────────────────────────
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
    return df

# ── 3. Trade Logic ────────────────────────────────
def trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        hour = ts.hour
        if not (1 <= hour < 19): continue  # Session 01-19 UTC
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02: direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98: direction='SELL'; entry=row['close']
        if direction is None: continue
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        exit_price = entry
        if direction == 'BUY':
            be_act=False; hi=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if h>hi: hi=h
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
                if be_act: sl=max(sl, hi*0.9995)
                if h>=tp: exit_price=tp; break
                if l<=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        else:  # SELL Visual SL
            mid_crossed=False
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'dir':direction,'entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    return trades

# ── 4. Simulation ─────────────────────────────────
def simulate(trades, initial=10000, risk_pct=0.01, max_contracts=10,
             daily_dd_limit=0.03, max_consec_loss=5):
    equity=initial; curve=[initial]; result=[]
    daily_start=initial; cur_day=None; consec_losses=0; stop_day=False; stopped_days=0
    for t in trades:
        day = t['time'].date()
        if day != cur_day: cur_day=day; daily_start=equity; consec_losses=0; stop_day=False
        if stop_day: continue
        sl_dist = abs(t['entry']-t['sl']); 
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = equity * risk_pct
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, max_contracts); contracts = max(contracts, 0.01)
        pnl_pts = (t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        equity += pnl_dollar
        if pnl_dollar <= 0: consec_losses += 1
        else: consec_losses = 0
        daily_dd = (daily_start - equity) / daily_start
        if daily_dd >= daily_dd_limit or consec_losses >= max_consec_loss:
            stop_day=True; stopped_days+=1
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t,'contracts':contracts,'pnl_$':pnl_dollar,'equity':equity})
    return result, curve, stopped_days

def stats(curve, trades_result, initial, stopped_days):
    if not curve: return {}
    final_eq = curve[-1]; ret = (final_eq/initial - 1)*100 if initial>0 else 0
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
    return {'final':final_eq,'return':ret,'dd':max_dd,'wr':wr,'pf':pf,'total_trades':len(trades_result),'stopped_days':stopped_days}

# ── 5. Run ────────────────────────────────────────
df_1h = add_indicators(df_1h).dropna()
t_list = trades(df_1h)
res, eq, stop = simulate(t_list)
s = stats(eq, res, 10000, stop)

print("\n📊 v11.2 (1H) + Visual SL (New V4) — Twelve Data 60 days")
print("="*60)
print(f"{'Metric':<25} {'Value':<20}")
print(f"{'Final Equity':<25} ${s['final']:<19,.2f}")
print(f"{'Return':<25} {s['return']:<19.2f}%")
print(f"{'Max Drawdown':<25} {s['dd']:<19.2f}%")
print(f"{'Win Rate':<25} {s['wr']:<19.2f}%")
print(f"{'Profit Factor':<25} {s['pf']:<19.2f}")
print(f"{'Total Trades':<25} {s['total_trades']:<19}")
print(f"{'Days Stopped':<25} {s['stopped_days']:<19}")
