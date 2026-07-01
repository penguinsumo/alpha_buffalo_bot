#!/usr/bin/env python3
"""
DD Analysis – V12 Backtest (SessionClock) with Lower BB TP for SELL
Parameter Tuning: BUY cutoff, Risk%, Daily DD Limit
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ------------------------------------------------------------------
# 1. DATA (Twelve Data 15m)
# ------------------------------------------------------------------
print("[INFO] Loading Twelve Data 15m XAU/USD...")
try:
    from data_provider_twelvedata import fetch_twelvedata
    df = fetch_twelvedata('XAU/USD', '15min', 90)
    cutoff = df.index.max() - pd.Timedelta(days=60)
    df = df[df.index >= cutoff]
    print(f"[OK] Twelve Data 15m: {len(df)} candles ({df.index.min().date()} -> {df.index.max().date()})")
except Exception as e:
    print(f"[FAIL] {e}"); sys.exit(1)

# ------------------------------------------------------------------
# 2. SESSION CLOCK (V12)
# ------------------------------------------------------------------
from session_clock import SessionClock
_clock = SessionClock()

def get_session_v12(ts):
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC') if hasattr(ts, 'tz_localize') else ts.replace(tzinfo=timezone.utc)
    s = _clock.get(ts)
    return s.session

# ------------------------------------------------------------------
# 3. INDICATORS
# ------------------------------------------------------------------
def add_indicators(df):
    df = df.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    h, l, c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l, (h-c).abs(), (l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        sw_high = df1h['high'].rolling(5).max()
        sw_low = df1h['low'].rolling(5).min()
        sw_high = sw_high.reindex(df.index, method='ffill')
        sw_low = sw_low.reindex(df.index, method='ffill')
    else:
        sw_high = df['high'].rolling(100).max()
        sw_low = df['low'].rolling(100).min()
    df['Swing_H'] = sw_high
    df['Swing_L'] = sw_low
    df['Diff'] = df['Swing_H'] - df['Swing_L']
    return df

# ------------------------------------------------------------------
# 4. TRADE LOGIC (New V4 BUY NY + Visual SL SELL with Lower BB TP)
# ------------------------------------------------------------------
def generate_trades(df, ny_buy_cutoff_hour=19):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        session = get_session_v12(ts)
        utc_hour = ts.hour
        if session == 'CLOSED': continue

        # BUY (New V4) NY only, before cutoff hour
        if (session == 'NY' and utc_hour < ny_buy_cutoff_hour and
            row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
            gl = row['Swing_H'] - row['Diff'] * 1.0
            gh = row['Swing_H'] - row['Diff'] * 0.5
            if gl <= row['close'] <= gh and row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02:
                entry = row['close']; sl = entry - row['ATR14'] * 1.5; tp = row['BB_Upper']
                be_act = False; highest = entry; exit_price = entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; hh, ll = r['high'], r['low']
                    if hh > highest: highest = hh
                    if not be_act and highest >= entry * 1.0015: be_act = True; sl = entry
                    if be_act: sl = max(sl, highest * 0.9995)
                    if hh >= tp: exit_price = tp; break
                    if ll <= sl: exit_price = sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
                trades.append({'session':session,'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':ts,'utc_hour':utc_hour})

        # SELL (Visual SL) – all sessions
        if row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98:
            entry = row['close']; sl = entry + row['ATR14'] * 1.5; exit_price = entry; mid_crossed = False

            # TP = Lower BB (ตาม Logic ใหม่)
            tp = row['BB_Lower']
            if tp >= entry or pd.isna(tp):
                tp = row['Swing_L']          # Fallback 1
                if tp >= entry:
                    tp = entry - row['ATR14'] * 2   # Fallback 2

            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if not mid_crossed and ll <= r['BB_Mid']:
                    mid_crossed = True; sl = entry
                if ll <= tp: exit_price = tp; break
                if hh >= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'session':session,'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':ts,'utc_hour':utc_hour})
    return trades

# ------------------------------------------------------------------
# 5. SIMULATION
# ------------------------------------------------------------------
def simulate(trades, initial=10000, risk_pct=0.01, max_contracts=10,
             daily_dd_limit=0.03, max_consec_loss=5):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades':[], 'curve':[initial], 'equity':initial,
                                    'daily_eq_start':initial, 'current_day':None,
                                    'consec_loss':0, 'stop_day':False, 'stopped':0,
                                    'max_dd':0})
    for t in trades:
        sess = t['session']; sd = sessions[sess]
        day = t['time'].date()
        if day != sd['current_day']:
            sd['current_day'] = day; sd['daily_eq_start'] = sd['equity']
            sd['consec_loss'] = 0; sd['stop_day'] = False
        if sd['stop_day']: continue
        sl_dist = abs(t['entry'] - t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        contracts = (sd['equity'] * risk_pct) / (sl_dist * 10)
        contracts = max(0.01, min(contracts, max_contracts))
        pnl_pts = (t['exit'] - t['entry']) if t['dir'] == 'BUY' else (t['entry'] - t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        sd['equity'] += pnl_dollar
        if pnl_dollar <= 0: sd['consec_loss'] += 1
        else: sd['consec_loss'] = 0
        daily_dd = (sd['daily_eq_start'] - sd['equity']) / sd['daily_eq_start']
        if daily_dd >= daily_dd_limit or sd['consec_loss'] >= max_consec_loss:
            sd['stop_day'] = True; sd['stopped'] += 1
        if sd['equity'] <= 0: sd['equity'] = 0; sd['curve'].append(0); break
        peak = max(sd['curve'])
        dd = (peak - sd['equity']) / peak * 100 if peak > 0 else 0
        if dd > sd['max_dd']: sd['max_dd'] = dd
        sd['curve'].append(sd['equity'])
        sd['trades'].append({**t, 'pnl_$':pnl_dollar, 'contracts':contracts})
    stats = {}
    for sess, sd in sessions.items():
        curve = sd['curve']; final_eq = curve[-1]
        ret = (final_eq / initial - 1) * 100 if initial > 0 else 0
        t_list = sd['trades']
        wins = [x for x in t_list if x['pnl_$'] > 0]
        wr = len(wins) / len(t_list) * 100 if t_list else 0
        gp = sum(x['pnl_$'] for x in wins)
        gl = abs(sum(x['pnl_$'] for x in t_list if x['pnl_$'] < 0))
        pf = gp / gl if gl > 0 else float('inf')
        stats[sess] = {'trades':len(t_list),'wr':wr,'return':ret,'dd':sd['max_dd'],'pf':pf,
                       'stopped':sd['stopped'],'final_eq':final_eq}
    return stats

# ------------------------------------------------------------------
# 6. ANALYSIS A: Hourly DD source in NY
# ------------------------------------------------------------------
df = add_indicators(df).dropna()
print(f"\n[BARS] After indicators: {len(df)}")

trades = generate_trades(df, ny_buy_cutoff_hour=19)
print(f"[TRADES] Total: {len(trades)}")

print("\n" + "="*70)
print("ANALYSIS A: NY session — DD source by UTC hour")
print("="*70)
ny_trades = [t for t in trades if t['session'] == 'NY']
hourly = defaultdict(lambda: {'count':0, 'wins':0, 'pnl_pts':0})
for t in ny_trades:
    h = t['utc_hour']
    pnl = (t['exit'] - t['entry']) if t['dir'] == 'BUY' else (t['entry'] - t['exit'])
    hourly[h]['count'] += 1
    hourly[h]['pnl_pts'] += pnl
    if pnl > 0: hourly[h]['wins'] += 1

print(f"{'UTC Hour':<10} {'Trades':<8} {'Win Rate':<10} {'PnL (pts)':<12} {'Verdict':<10}")
print("-"*55)
for h in sorted(hourly.keys()):
    d = hourly[h]
    wr = d['wins']/d['count']*100 if d['count'] else 0
    verdict = "LOSS ZONE" if d['pnl_pts'] < 0 else ("PROFIT" if d['pnl_pts'] > 0 else "FLAT")
    print(f"{h:02d}:00      {d['count']:<8} {wr:<10.1f}% {d['pnl_pts']:<+12.1f} {verdict}")

# ------------------------------------------------------------------
# 7. ANALYSIS B: Parameter tuning
# ------------------------------------------------------------------
print("\n" + "="*70)
print("ANALYSIS B: Parameter tuning to reduce NY DD")
print("="*70)

configs = [
    ("BASELINE (current)",         19, 0.01, 0.03),
    ("Cut BUY @17 UTC",            17, 0.01, 0.03),
    ("Cut BUY @18 UTC",            18, 0.01, 0.03),
    ("Risk 0.75% (tighter)",       19, 0.0075, 0.03),
    ("Daily DD limit 2%",          19, 0.01, 0.02),
    ("Daily DD 2% + Cut BUY@18",   18, 0.01, 0.02),
    ("Risk 0.75% + DD 2%",         19, 0.0075, 0.02),
]

print(f"\n{'Config':<32} {'NY Return':<12} {'NY DD':<10} {'NY PF':<8} {'Total Return':<14} {'Sum DD':<10}")
print("-"*90)

best_config = None
best_score = -999
for label, cutoff, risk, ddlim in configs:
    t = generate_trades(df, ny_buy_cutoff_hour=cutoff)
    s = simulate(t, risk_pct=risk, daily_dd_limit=ddlim, max_consec_loss=5)
    ny = s.get('NY', {})
    total_ret = sum(v.get('return',0) for v in s.values())
    sum_dd = sum(v.get('dd',0) for v in s.values())
    ny_ret = ny.get('return', 0)
    ny_dd = ny.get('dd', 0)
    ny_pf = ny.get('pf', 0)
    score = total_ret / max(sum_dd, 1)   # return per unit risk
    marker = ""
    if score > best_score:
        best_score = score; best_config = label
        marker = " <== BEST"
    print(f"{label:<32} {ny_ret:<12.1f} {ny_dd:<10.2f} {ny_pf:<8.2f} {total_ret:<14.1f} {sum_dd:<10.2f}{marker}")

print(f"\n>>> BEST Return/DD ratio: {best_config}")
