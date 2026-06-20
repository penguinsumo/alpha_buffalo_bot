#!/usr/bin/env python3
"""v11.2 mod + Visual SL on 1H – find best 3‑hour windows per session"""
import pandas as pd, numpy as np
from datetime import datetime, timedelta

# ── 1. Load 1H data (Twelve Data or GC=F fallback) ──
print("📡 Loading 1H data...")
try:
    from data_provider_twelvedata import fetch_twelvedata
    _, df_1h, _ = fetch_twelvedata()
    if df_1h is None or len(df_1h) < 20: raise ValueError("Not enough 1H")
    cutoff = df_1h.index.max() - pd.Timedelta(days=60)
    df_1h = df_1h[df_1h.index >= cutoff]
    print(f"✅ Twelve Data 1H: {len(df_1h)} candles")
except:
    import yfinance as yf
    end = datetime.now(); start = end - timedelta(days=60)
    df = yf.download("GC=F", start=start, end=end, interval="15m")
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
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

# ── 3. Session function ───────────────────────────
def get_session(ts):
    h = ts.hour
    if 1 <= h < 8: return 'ASIA'
    elif 8 <= h < 13: return 'LONDON'
    elif 13 <= h < 19: return 'NY'
    else: return 'ASIA_LOW'

# ── 4. Generate trades (v11.2 mod + Visual SL for sell) ──
def generate_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
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
        else: # SELL Visual SL
            mid_crossed=False
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        pnl_pts = (exit_price - entry) if direction=='BUY' else (entry - exit_price)
        trades.append({'dir':direction, 'pnl_pts':pnl_pts, 'time':ts, 'session':get_session(ts),
                        'entry':entry, 'exit':exit_price, 'sl':sl})
    return trades

# ── 5. Equity simulation (1% risk) ────────────────
def simulate_equity(trades, initial=10000, risk_pct=0.01, max_contracts=10):
    equity=initial; curve=[initial]; result=[]
    for t in trades:
        sl_dist = abs(t['entry']-t['sl'])
        if sl_dist < 0.5: sl_dist=0.5
        risk_amount = equity * risk_pct
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, max_contracts)
        contracts = max(contracts, 0.01)
        pnl_pts = (t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        equity += pnl_dollar
        if equity <= 0: equity=0; curve.append(0); break
        curve.append(equity)
        result.append({**t, 'pnl_$':pnl_dollar, 'equity':equity, 'contracts':contracts})
    return result, curve

def stats(curve, initial):
    if not curve or len(curve)<2: return {}
    final_eq = curve[-1]; ret = (final_eq/initial - 1)*100
    peak=initial; max_dd=0
    for eq in curve:
        if eq>peak: peak=eq
        dd = (peak-eq)/peak*100 if peak>0 else 0
        if dd>max_dd: max_dd=dd
    return {'final':final_eq,'return':ret,'dd':max_dd}

# ── 6. Find best 3‑hour windows ───────────────────
def best_3hr_windows(trades_with_eq, session):
    # Filter trades for this session
    ses_trades = [t for t in trades_with_eq if t['session']==session]
    if not ses_trades: return []
    # aggregate by hour
    df = pd.DataFrame(ses_trades)
    df['hour'] = df['time'].dt.hour
    hourly = df.groupby('hour').agg(trades=('pnl_$','count'), pnl_pts=('pnl_pts','sum'),
                                    buy_pnl_pts=('pnl_pts', lambda x: x[df['dir']=='BUY'].sum()),
                                    sell_pnl_pts=('pnl_pts', lambda x: x[df['dir']=='SELL'].sum()))
    # Define session hours range
    if session=='ASIA': hours = range(1,9)
    elif session=='LONDON': hours = range(8,14)
    elif session=='NY': hours = range(13,20)
    else: hours = range(0,1) # ASIA_LOW skip for now
    # create continuous index for sliding window
    idx = pd.Index(hours, name='hour')
    hourly = hourly.reindex(idx, fill_value=0)
    # slide 3-hour window
    best = []
    for start_h in hours:
        end_h = start_h + 2
        if end_h > max(hours): break
        window = hourly.loc[start_h:end_h]
        total_pnl = window['pnl_pts'].sum()
        total_trades = window['trades'].sum()
        buy_pnl = window['buy_pnl_pts'].sum()
        sell_pnl = window['sell_pnl_pts'].sum()
        best.append((start_h, end_h, total_pnl, total_trades, buy_pnl, sell_pnl))
    best.sort(key=lambda x: x[2], reverse=True)
    return best[:2]  # top 2

# ── 7. Main ──────────────────────────────────────
df_1h = add_indicators(df_1h).dropna()
trades_raw = generate_trades(df_1h)
print(f"Total trades: {len(trades_raw)}")

# Simulate equity for all trades (for overall stats)
all_res, all_eq = simulate_equity(trades_raw)
all_stats = stats(all_eq, 10000)

# Overall by session
for ses in ['ASIA','LONDON','NY','ASIA_LOW']:
    ses_trades = [t for t in all_res if t['session']==ses]
    if not ses_trades: continue
    ses_eq = [10000] + [t['equity'] for t in ses_trades]
    s = stats(ses_eq, 10000)
    print(f"\n--- {ses} ---")
    print(f"Trades: {len(ses_trades)} | Return: {s['return']:.1f}% | MaxDD: {s['dd']:.2f}%")
    # Best 3‑hour windows
    top2 = best_3hr_windows(all_res, ses)
    for rank, (sh, eh, pnl, trds, buy, sell) in enumerate(top2, 1):
        window_str = f"{sh:02d}:00-{eh:02d}:59"
        print(f"  #{rank} {window_str} | PnL(pts)={pnl:+.1f} | Trades={trds} | Buy={buy:+.1f} Sell={sell:+.1f}")
