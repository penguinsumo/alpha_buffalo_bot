#!/usr/bin/env python3
"""Hybrid Backtest: v11.2 Entry + Visual SL Exit + All Sessions (No Pre-Close)"""
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
    return df

# ── Session Logic (ตัด Pre-Close 19-24) ────────
def is_valid_session(hour_utc):
    """อนุญาตเฉพาะ Asia, London, NY"""
    if 1 <= hour_utc < 19:  # 01:00-19:00 UTC
        return True
    return False

# ── 3. Trade Logics ──────────────────────────────
# v11.2 Entry (Buy/Sell) + Visual SL Exit
def hybrid_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; hour = row.name.hour
        if not is_valid_session(hour): continue
        
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02:
                direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98:
                direction='SELL'; entry=row['close']
        if direction is None: continue
        
        # ── Exit Logic แยกตามฝั่ง ──
        if direction == 'BUY':
            # ใช้ Simple Trailing (เหมือน New V4 Buy)
            sl = entry - row['ATR14']*1.5
            tp = row['BB_Upper']
            be_act=False; highest=entry; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if h>highest: highest=h
                if not be_act and highest>=entry*1.0015: be_act=True; sl=entry
                if be_act: sl=max(sl,highest*0.9995)
                if h>=tp: exit_price=tp; break
                if l<=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        else:  # SELL
            # ใช้ Visual SL (แตะ Mid BB → SL=Entry)
            sl = entry + row['ATR14']*1.5
            mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']:
                    mid_crossed=True; sl=entry
                if l<=r['BB_Lower']:
                    exit_price=r['BB_Lower']; break
                if h>=sl:
                    exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        
        trades.append({'dir':direction,'entry':entry,'exit':exit_price,'sl':sl})
    return trades

# ── 4. Position Sizing (1% Risk, max 10 contracts) ─
def simulate_equity(trades, initial=10000, risk_pct=0.01, contract_size=10, max_contracts=10):
    equity = initial; curve = [initial]; result = []
    for t in trades:
        sl_dist = abs(t['entry'] - t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = equity * risk_pct
        contracts = risk_amount / (sl_dist * contract_size)
        contracts = min(contracts, max_contracts)
        contracts = max(contracts, 0.01)
        pnl_pts = (t['exit'] - t['entry']) if t['dir']=='BUY' else (t['entry'] - t['exit'])
        pnl_dollar = pnl_pts * contract_size * contracts
        equity += pnl_dollar
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t, 'contracts':contracts, 'pnl_$':pnl_dollar, 'equity':equity})
    return result, curve

def stats(curve, trades_result, initial):
    if not curve or len(curve)<2: return {}
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

# ── 5. Run ────────────────────────────────────────
df_15m = add_indicators(df).dropna()
t_hybrid = hybrid_trades(df_15m)
res_hybrid, eq_hybrid = simulate_equity(t_hybrid)
s_hybrid = stats(eq_hybrid, res_hybrid, 10000)

print("\n📊 HYBRID (v11.2 Entry + Visual SL Exit + All Sessions No Pre-Close)")
print("="*60)
print(f"{'Initial':<25} ${10000:<14,.0f}")
print(f"{'Final Equity':<25} ${s_hybrid['final']:<14,.2f}")
print(f"{'Return':<25} {s_hybrid['return']:<14.2f}%")
print(f"{'Max Drawdown':<25} {s_hybrid['dd']:<14.2f}%")
print(f"{'Win Rate':<25} {s_hybrid['wr']:<14.2f}%")
print(f"{'Profit Factor':<25} {s_hybrid['pf']:<14.2f}")
print(f"{'Total Trades':<25} {s_hybrid['total']:<14}")
