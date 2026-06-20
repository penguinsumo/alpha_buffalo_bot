#!/usr/bin/env python3
"""
เปรียบเทียบ v11.2, New V4, New V4+Tunnel บน GC=F 60 วัน
มี Risk Gates: Daily DD 3%, Consec Loss 5
Session: 01-19 UTC (ตัด ASIA_LOW)
"""
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

# ── 2. Indicators + Tunnel ────────────────────────
def add_indicators(df):
    df = df.copy()
    # BB
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    # ATR
    h,l,c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    # EMA
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    # Sweep
    df['Low_Prev'] = df['low'].shift(1); df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    # 1H Swing for Golden Zone
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max(); lows = df1h['low'].rolling(5).min()
        sw_high = highs.max(); sw_low = lows.min()
    else: sw_high = sw_low = 0
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = sw_high - sw_low
    # Tunnel (Parallel Channel) - Dow Theory
    # Find pivot highs and lows on 15m
    df = compute_tunnel(df)
    return df

def compute_tunnel(df, left=5, right=5):
    """คำนวณ Parallel Channel (Tunnel) ตาม Dow Theory"""
    highs = []; lows = []
    for i in range(len(df)):
        if i < left or i + right >= len(df): continue
        # Pivot High
        window_high = df['high'].iloc[i-left:i+right+1]
        if df['high'].iloc[i] == window_high.max():
            highs.append((i, df['high'].iloc[i]))
        # Pivot Low
        window_low = df['low'].iloc[i-left:i+right+1]
        if df['low'].iloc[i] == window_low.min():
            lows.append((i, df['low'].iloc[i]))
    
    # Default no tunnel
    df['tunnel_upper'] = np.nan
    df['tunnel_lower'] = np.nan
    df['tunnel_status'] = 'NONE'
    
    if len(highs) < 2 or len(lows) < 2:
        return df
    
    # กำหนด trend จาก EMA
    trend_up = df['EMA20'].iloc[-1] > df['EMA50'].iloc[-1]
    
    # เรียงตาม index
    highs.sort(key=lambda x: x[0]); lows.sort(key=lambda x: x[0])
    
    if trend_up:
        # เลือก HH 2 ตัวสุดท้ายที่สูงขึ้น
        valid_highs = []
        for idx, val in highs:
            if not valid_highs or val > valid_highs[-1][1]:
                valid_highs.append((idx, val))
        if len(valid_highs) < 2: return df
        
        h1, h2 = valid_highs[-2], valid_highs[-1]
        # เลือก HL 2 ตัวสุดท้ายที่สูงขึ้น
        valid_lows = []
        for idx, val in lows:
            if not valid_lows or val > valid_lows[-1][1]:
                valid_lows.append((idx, val))
        if len(valid_lows) < 2: return df
        
        l1, l2 = valid_lows[-2], valid_lows[-1]
        
        # Slope จาก lows
        slope = (l2[1] - l1[1]) / (l2[0] - l1[0]) if l2[0] != l1[0] else 0
        # Project line ไปทุกแท่ง
        for i in range(len(df)):
            df.loc[df.index[i], 'tunnel_lower'] = l2[1] + slope * (i - l2[0])
            # Upper line: parallel through h2
            upper_at_h2 = l2[1] + slope * (h2[0] - l2[0])
            offset = h2[1] - upper_at_h2
            df.loc[df.index[i], 'tunnel_upper'] = df['tunnel_lower'].iloc[i] + offset
        df['tunnel_status'] = 'CONFIRMED'
    else:
        # ขาลง: LH 2 ตัว, LL 2 ตัว
        valid_highs = []
        for idx, val in highs:
            if not valid_highs or val < valid_highs[-1][1]:
                valid_highs.append((idx, val))
        if len(valid_highs) < 2: return df
        
        h1, h2 = valid_highs[-2], valid_highs[-1]
        valid_lows = []
        for idx, val in lows:
            if not valid_lows or val < valid_lows[-1][1]:
                valid_lows.append((idx, val))
        if len(valid_lows) < 2: return df
        
        l1, l2 = valid_lows[-2], valid_lows[-1]
        
        # Slope จาก highs (ขาลง)
        slope = (h2[1] - h1[1]) / (h2[0] - h1[0]) if h2[0] != h1[0] else 0
        for i in range(len(df)):
            df.loc[df.index[i], 'tunnel_upper'] = h2[1] + slope * (i - h2[0])
            lower_at_h2 = h2[1] + slope * (l2[0] - h2[0])
            offset = l2[1] - lower_at_h2
            df.loc[df.index[i], 'tunnel_lower'] = df['tunnel_upper'].iloc[i] + offset
        df['tunnel_status'] = 'CONFIRMED'
    
    return df

# ── Session ──────────────────────────────────────
def is_valid_session(ts):
    hour = ts.hour
    return 1 <= hour < 19  # ASIA+LONDON+NY, no ASIA_LOW

# ── Trade Functions ──────────────────────────────
def v112_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if not (12 <= ts.hour <= 22): continue  # v11.2 session: 12-22 UTC
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
        trades.append({'dir':direction,'entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    return trades

def new_v4_trades(df, use_tunnel=False):
    trades = []
    for i in range(20, len(df)-40):
        row=df.iloc[i]; ts=row.name
        if not is_valid_session(ts): continue
        # BUY (Golden Zone)
        if (row['EMA20']>row['EMA50'] and row['Swing_H']>row['Swing_L'] and row['Diff']>0):
            gl=row['Swing_H']-row['Diff']*1.0; gh=row['Swing_H']-row['Diff']*0.5
            if gl<=row['close']<=gh and row['Bull_Sweep'] and row['low']<=row['BB_Lower']*1.02:
                # Tunnel filter (optional)
                if use_tunnel and row['tunnel_status']=='CONFIRMED':
                    # ต้องอยู่ใกล้ Lower Tunnel
                    if row['close'] > row['tunnel_lower'] * 1.02: continue
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
                trades.append({'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':ts})
        # SELL (Visual SL)
        if (row['EMA20']<row['EMA50'] and row['Bear_Sweep'] and row['high']>=row['BB_Upper']*0.98):
            # Tunnel filter for sell
            if use_tunnel and row['tunnel_status']=='CONFIRMED':
                if row['close'] < row['tunnel_upper'] * 0.98: continue
            entry=row['close']; sl=entry+row['ATR14']*1.5; mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    return trades

# ── Risk Gated Simulation ───────────────────────
def simulate_with_risk_gates(trades, initial=10000, risk_pct=0.01, max_contracts=10,
                             daily_dd_limit=0.03, max_consec_loss=5):
    equity = initial; curve = [initial]; result = []
    daily_equity_start = initial; current_day = None
    consec_losses = 0; stop_trading_day = False; stopped_days = 0
    for t in trades:
        trade_day = t['time'].date()
        if trade_day != current_day:
            current_day = trade_day; daily_equity_start = equity
            consec_losses = 0; stop_trading_day = False
        if stop_trading_day: continue
        sl_dist = abs(t['entry'] - t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = equity * risk_pct
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, max_contracts)
        contracts = max(contracts, 0.01)
        pnl_pts = (t['exit'] - t['entry']) if t['dir']=='BUY' else (t['entry'] - t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        equity += pnl_dollar
        if pnl_dollar <= 0: consec_losses += 1
        else: consec_losses = 0
        daily_dd = (daily_equity_start - equity) / daily_equity_start
        if daily_dd >= daily_dd_limit or consec_losses >= max_consec_loss:
            stop_trading_day = True; stopped_days += 1
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t, 'contracts':contracts, 'pnl_$':pnl_dollar, 'equity':equity})
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
    return {'final':final_eq,'return':ret,'dd':max_dd,'wr':wr,'pf':pf,
            'total_trades':len(trades_result),'stopped_days':stopped_days}

# ── Run ──────────────────────────────────────────
df_15m = add_indicators(df).dropna()
print("Running v11.2...")
res_v, eq_v, stop_v = simulate_with_risk_gates(v112_trades(df_15m))
s_v = stats(eq_v, res_v, 10000, stop_v)

print("Running New V4 (no Tunnel)...")
res_n, eq_n, stop_n = simulate_with_risk_gates(new_v4_trades(df_15m, use_tunnel=False))
s_n = stats(eq_n, res_n, 10000, stop_n)

print("Running New V4 + Tunnel...")
res_t, eq_t, stop_t = simulate_with_risk_gates(new_v4_trades(df_15m, use_tunnel=True))
s_t = stats(eq_t, res_t, 10000, stop_t)

print("\n📊 COMPARISON (GC=F 60 days, Risk Gates, 01-19 UTC)")
print("="*90)
print(f"{'Metric':<25} {'v11.2':<20} {'New V4':<20} {'V4+Tunnel':<20}")
print(f"{'Final Equity':<25} ${s_v['final']:<19,.2f} ${s_n['final']:<19,.2f} ${s_t['final']:<19,.2f}")
print(f"{'Return':<25} {s_v['return']:<19.2f}% {s_n['return']:<19.2f}% {s_t['return']:<19.2f}%")
print(f"{'Max Drawdown':<25} {s_v['dd']:<19.2f}% {s_n['dd']:<19.2f}% {s_t['dd']:<19.2f}%")
print(f"{'Win Rate':<25} {s_v['wr']:<19.2f}% {s_n['wr']:<19.2f}% {s_t['wr']:<19.2f}%")
print(f"{'Profit Factor':<25} {s_v['pf']:<19.2f} {s_n['pf']:<19.2f} {s_t['pf']:<19.2f}")
print(f"{'Total Trades':<25} {s_v['total_trades']:<19} {s_n['total_trades']:<19} {s_t['total_trades']:<19}")
print(f"{'Days Stopped':<25} {s_v['stopped_days']:<19} {s_n['stopped_days']:<19} {s_t['stopped_days']:<19}")
