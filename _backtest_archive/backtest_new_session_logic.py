#!/usr/bin/env python3
"""Backtest New Session Logic (ASIA/LONDON SELL, NY BUY+SELL) vs Original Visual TP"""
import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
from collections import defaultdict
warnings.filterwarnings('ignore')

# ── 1. Load Twelve Data 15m & 1H ──────────────────
env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end_date = datetime(2026, 6, 17)
start_date = end_date - timedelta(days=60)
def fetch(symbol='XAU/USD', interval='15min'):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol': symbol, 'interval': interval,
              'start_date': start_date.strftime('%Y-%m-%d'),
              'end_date': end_date.strftime('%Y-%m-%d'),
              'outputsize': 5000, 'apikey': API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        raise RuntimeError(f"API error: {data.get('message','')}")
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open','high','low','close']:
        df[col] = pd.to_numeric(df[col])
    df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
    return df

df_15m = fetch('XAU/USD', '15min')
df_1h  = fetch('XAU/USD', '1h')
print(f"Loaded 15m:{len(df_15m)} 1H:{len(df_1h)}")

# ── 2. Indicators (use 15m) ───────────────────────
def add_indicators(df):
    df = df.copy()
    df['BB_Mid'] = df['Close'].rolling(20).mean()
    df['BB_Std'] = df['Close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    h,l,c = df['High'], df['Low'], df['Close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['Close'].ewm(span=20).mean()
    df['EMA50'] = df['Close'].ewm(span=50).mean()
    df['Low_Prev'] = df['Low'].shift(1); df['High_Prev'] = df['High'].shift(1)
    df['Bull_Sweep'] = (df['Low'] < df['Low_Prev']) & (df['Close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['High'] > df['High_Prev']) & (df['Close'] < df['High_Prev'])
    # 1H Swing for Golden Zone & Visual TP
    df1h = df_1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()
    if len(df1h) >= 5:
        sw_high = df1h['High'].rolling(5).max()
        sw_low = df1h['Low'].rolling(5).min()
        sw_high = sw_high.reindex(df.index, method='ffill')
        sw_low = sw_low.reindex(df.index, method='ffill')
    else:
        sw_high = df['High'].rolling(100).max()
        sw_low = df['Low'].rolling(100).min()
    df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
    df['Diff'] = df['Swing_H'] - df['Swing_L']
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    df['PRZ_Next'] = df['Swing_L']  # simplified
    return df

df_15m = add_indicators(df_15m).dropna()

def get_session(h):
    if 1 <= h < 8: return 'ASIA'
    elif 8 <= h < 13: return 'LONDON'
    elif 13 <= h < 19: return 'NY'
    return 'OTHER'

# ── 3. Trade Generation ───────────────────────────
def generate_trades(df, logic='new'):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name; h = ts.hour
        if not (1 <= h < 19): continue
        session = get_session(h)

        # ---- BUY ----
        if logic == 'original' or (logic == 'new' and session == 'NY'):
            if (row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
                gl = row['Swing_H'] - row['Diff']*1.0
                gh = row['Swing_H'] - row['Diff']*0.5
                if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                    entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
                    be_act=False; highest=entry; exit_price=entry
                    for j in range(i+1, min(i+40, len(df))):
                        r = df.iloc[j]; hh, ll = r['High'], r['Low']
                        if hh>highest: highest=hh
                        if not be_act and highest >= entry * 1.0015:
                            be_act=True; sl=entry
                        if be_act: sl = max(sl, highest*0.9995)
                        if hh >= tp: exit_price=tp; break
                        if ll <= sl: exit_price=sl; break
                    else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
                    trades.append({'session':session, 'dir':'BUY', 'entry':entry, 'exit':exit_price, 'sl':sl, 'time':ts, 'hour':h})

        # ---- SELL (Visual TP) ----
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98):
            entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
            mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['High'], r['Low']
                if not mid_crossed and ll <= r['BB_Mid']:
                    mid_crossed=True; sl=entry
                if ll <= tp: exit_price=tp; break
                if hh >= sl: exit_price=sl; break
            else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            trades.append({'session':session, 'dir':'SELL', 'entry':entry, 'exit':exit_price, 'sl':sl, 'time':ts, 'hour':h})
    return trades

# ── 4. Simulation (per session, 1% risk) ─────────
def simulate_per_session(trades, initial=10000):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades': [], 'curve': [initial], 'equity': initial,
                                    'daily_eq_start': initial, 'current_day': None,
                                    'consec_loss': 0, 'stop_day': False, 'stopped': 0})
    for t in trades:
        sess = t['session']
        sd = sessions[sess]
        trade_day = t['time'].date()
        if trade_day != sd['current_day']:
            sd['current_day'] = trade_day; sd['daily_eq_start'] = sd['equity']
            sd['consec_loss'] = 0; sd['stop_day'] = False
        if sd['stop_day']: continue
        sl_dist = abs(t['entry'] - t['sl']); 
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = sd['equity'] * 0.01
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, 10.0); contracts = max(contracts, 0.01)
        pnl_pts = (t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        sd['equity'] += pnl_dollar
        if pnl_dollar <= 0: sd['consec_loss'] += 1
        else: sd['consec_loss'] = 0
        daily_dd = (sd['daily_eq_start'] - sd['equity']) / sd['daily_eq_start']
        if daily_dd >= 0.03 or sd['consec_loss'] >= 5:
            sd['stop_day'] = True; sd['stopped'] += 1
        if sd['equity'] <= 0: sd['equity']=0; sd['curve'].append(0); break
        sd['curve'].append(sd['equity'])
        sd['trades'].append({**t, 'pnl_$': pnl_dollar})
    stats = {}
    for sess, sd in sessions.items():
        curve = sd['curve']; final_eq = curve[-1]
        ret = (final_eq/initial - 1)*100 if initial>0 else 0
        peak=initial; max_dd=0
        for eq in curve:
            if eq>peak: peak=eq
            dd = (peak-eq)/peak*100 if peak>0 else 0
            if dd>max_dd: max_dd=dd
        total = len(sd['trades'])
        wins = [x for x in sd['trades'] if x['pnl_$']>0]
        wr = len(wins)/total*100 if total else 0
        gross_profit = sum(x['pnl_$'] for x in wins)
        gross_loss = abs(sum(x['pnl_$'] for x in sd['trades'] if x['pnl_$']<0))
        pf = gross_profit/gross_loss if gross_loss>0 else float('inf')
        stats[sess] = {'trades':total,'wr':wr,'return':ret,'dd':max_dd,'pf':pf,'stopped':sd['stopped'],'final_eq':final_eq}
    return stats

# ── 5. Hourly breakdown ──────────────────────────
def hourly_breakdown(trades):
    hours = range(1,19)  # 1..18 UTC
    result = []
    for h in hours:
        sub = [t for t in trades if t['hour']==h]
        if not sub: continue
        pnl = sum((t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit']) for t in sub)
        wins = [t for t in sub if ((t['exit']-t['entry']) if t['dir']=='BUY' else (t['entry']-t['exit'])) > 0]
        wr = len(wins)/len(sub)*100 if sub else 0
        result.append({'hour':h, 'trades':len(sub), 'wr':wr, 'pnl_pts':pnl})
    return pd.DataFrame(result)

# ── 6. Run both scenarios ────────────────────────
print("Running NEW logic (ASIA/LONDON SELL, NY BUY+SELL)...")
trades_new = generate_trades(df_15m, logic='new')
stats_new = simulate_per_session(trades_new)

print("Running ORIGINAL Visual TP (both sides all sessions)...")
trades_orig = generate_trades(df_15m, logic='original')
stats_orig = simulate_per_session(trades_orig)

# ── 7. Print comparison ──────────────────────────
print("\n📊 SESSION RETURN COMPARISON (60 days, 1% risk, reset equity)")
print("="*80)
print(f"{'Session':<10} {'New Trades':<12} {'New Return':<12} {'New DD':<10} {'Orig Trades':<14} {'Orig Return':<12} {'Orig DD':<10}")
print("-"*80)
for s in ['ASIA','LONDON','NY']:
    n = stats_new.get(s); o = stats_orig.get(s)
    if n and o:
        print(f"{s:<10} {n['trades']:<12} {n['return']:<12.2f}% {n['dd']:<10.2f}% {o['trades']:<14} {o['return']:<12.2f}% {o['dd']:<10.2f}%")

# Hourly breakdown for NEW
print("\n📊 NEW LOGIC HOURLY BREAKDOWN (PnL in points)")
print("="*60)
df_hour = hourly_breakdown(trades_new)
for _, r in df_hour.iterrows():
    print(f"UTC {r['hour']:02d}:00  Trades:{int(r['trades']):<4}  WR:{r['wr']:.1f}%  PnL:{r['pnl_pts']:+.1f} pts")
