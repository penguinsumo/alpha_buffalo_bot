#!/usr/bin/env python3
"""
V12 Architecture Backtest - SessionClock (Single Source of Truth)

Compare: "old hardcoded session" vs "SessionClock from session_clock.py"
Data: Twelve Data XAU/USD 15m, 60 days
Risk: Daily DD 3%, Consec Loss 5, Position Sizing 1%
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd, numpy as np
from datetime import datetime, timedelta, timezone
from collections import defaultdict

# ----------------------------------------------------------------------
# 1. DATA (Twelve Data via data_provider)
# ----------------------------------------------------------------------
print("[INFO] Loading Twelve Data 15m XAU/USD...")
try:
    from data_provider_twelvedata import fetch_twelvedata
    df = fetch_twelvedata('XAU/USD', '15min', 90)
    cutoff = df.index.max() - pd.Timedelta(days=60)
    df = df[df.index >= cutoff]
    print(f"[OK] Twelve Data 15m: {len(df)} candles ({df.index.min().date()} -> {df.index.max().date()})")
except Exception as e:
    print(f"[FAIL] Twelve Data failed: {e}")
    sys.exit(1)

# ----------------------------------------------------------------------
# 2. SESSION CLOCK (V12 Read-Only Provider)
# ----------------------------------------------------------------------
from session_clock import SessionClock
_clock = SessionClock()

def get_session_v12(ts):
    """Use real SessionClock from session_clock.py"""
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC') if hasattr(ts, 'tz_localize') else ts.replace(tzinfo=timezone.utc)
    s = _clock.get(ts)
    return s.session, s.liquidity

def get_session_old(ts):
    """Old hardcoded session mapping (for comparison)"""
    h = ts.hour
    if 1 <= h < 8: return 'ASIA', 'NORMAL'
    elif 8 <= h < 13: return 'LONDON', 'NORMAL'
    elif 13 <= h < 19: return 'NY', 'NORMAL'
    return 'CLOSED', 'NONE'

# ----------------------------------------------------------------------
# 3. INDICATORS (BB, ATR, EMA, Sweep, 1H Swing)
# ----------------------------------------------------------------------
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
    # 1H Swing for Golden Zone & Visual TP
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
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    return df

# ----------------------------------------------------------------------
# 4. TRADE LOGIC (New V4 + Visual SL)
# ----------------------------------------------------------------------
def generate_trades(df, session_fn, label):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]
        ts = row.name
        session, liquidity = session_fn(ts)
        if session == 'CLOSED':
            continue

        # ---- BUY (New V4) NY only ----
        if session == 'NY' and row['EMA20'] > row['EMA50'] and row['Diff'] > 0:
            gl = row['Swing_H'] - row['Diff'] * 1.0
            gh = row['Swing_H'] - row['Diff'] * 0.5
            if gl <= row['close'] <= gh and row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02:
                entry = row['close']
                sl = entry - row['ATR14'] * 1.5
                tp = row['BB_Upper']
                be_act = False; highest = entry; exit_price = entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; hh, ll = r['high'], r['low']
                    if hh > highest: highest = hh
                    if not be_act and highest >= entry * 1.0015:
                        be_act = True; sl = entry
                    if be_act: sl = max(sl, highest * 0.9995)
                    if hh >= tp: exit_price = tp; break
                    if ll <= sl: exit_price = sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
                trades.append({'session':session, 'dir':'BUY', 'entry':entry,
                              'exit':exit_price, 'sl':sl, 'time':ts})

        # ---- SELL (Visual SL/TP) all sessions ----
        if row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98:
            entry = row['close']
            sl = entry + row['ATR14'] * 1.5
            exit_price = entry
            mid_crossed = False

            tp = row['BB_Lower']
            if tp >= entry or pd.isna(tp):
                tp = row['Swing_L']
                if tp >= entry:
                    tp = entry - row['ATR14'] * 2

            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if not mid_crossed and ll <= r['BB_Mid']:
                    mid_crossed = True; sl = entry
                if ll <= tp: exit_price = tp; break
                if hh >= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'session':session, 'dir':'SELL', 'entry':entry,
                          'exit':exit_price, 'sl':sl, 'time':ts})
    print(f"  [{label}] Generated {len(trades)} trades")
    return trades

# ----------------------------------------------------------------------
# 5. SIMULATION (per session, 1% risk, daily limits)
# ----------------------------------------------------------------------
def simulate_per_session(trades, initial=10000, risk_pct=0.01, max_contracts=10,
                          daily_dd_limit=0.03, max_consec_loss=5):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades':[], 'curve':[initial], 'equity':initial,
                                    'daily_eq_start':initial, 'current_day':None,
                                    'consec_loss':0, 'stop_day':False, 'stopped':0})
    for t in trades:
        sess = t['session']
        sd = sessions[sess]
        trade_day = t['time'].date()
        if trade_day != sd['current_day']:
            sd['current_day'] = trade_day
            sd['daily_eq_start'] = sd['equity']
            sd['consec_loss'] = 0
            sd['stop_day'] = False
        if sd['stop_day']:
            continue
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
        if sd['equity'] <= 0:
            sd['equity'] = 0; sd['curve'].append(0); break
        sd['curve'].append(sd['equity'])
        sd['trades'].append({**t, 'pnl_$':pnl_dollar, 'contracts':contracts, 'equity':sd['equity']})

    stats = {}
    for sess, sd in sessions.items():
        curve = sd['curve']
        final_eq = curve[-1]
        ret = (final_eq / initial - 1) * 100 if initial > 0 else 0
        peak = initial; max_dd = 0
        for eq in curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd: max_dd = dd
        t_list = sd['trades']
        wins = [x for x in t_list if x['pnl_$'] > 0]
        wr = len(wins) / len(t_list) * 100 if t_list else 0
        gp = sum(x['pnl_$'] for x in wins)
        gl = abs(sum(x['pnl_$'] for x in t_list if x['pnl_$'] < 0))
        pf = gp / gl if gl > 0 else float('inf')
        stats[sess] = {'trades':len(t_list), 'wr':wr, 'return':ret, 'dd':max_dd,
                       'pf':pf, 'stopped':sd['stopped'], 'final_eq':final_eq}
    return stats

# ----------------------------------------------------------------------
# 6. RUN BOTH
# ----------------------------------------------------------------------
df = add_indicators(df).dropna()
print(f"\n[BARS] After indicators: {len(df)}\n")

print("-- V12 (SessionClock) --")
trades_v12 = generate_trades(df, get_session_v12, 'V12-SessionClock')
stats_v12 = simulate_per_session(trades_v12)

print("\n-- OLD (hardcoded) --")
trades_old = generate_trades(df, get_session_old, 'OLD-hardcoded')
stats_old = simulate_per_session(trades_old)

# ----------------------------------------------------------------------
# 7. OUTPUT
# ----------------------------------------------------------------------
def fmt(v, kind='pct'):
    if v == float('inf'): return 'inf'
    if v is None or (isinstance(v, float) and np.isnan(v)): return '-'
    if kind == 'pct': return f"{v:.2f}%"
    if kind == 'pf':  return f"{v:.2f}"
    if kind == 'int': return f"{int(v)}"
    if kind == 'usd': return f"${v:,.0f}"
    return str(v)

print("\n" + "="*100)
print(f"{'V12 BACKTEST - Twelve Data 15m 60d - OLD vs NEW (SessionClock) session':^100}")
print("="*100)
for sess in ['ASIA', 'LONDON', 'NY']:
    o = stats_old.get(sess, {}); n = stats_v12.get(sess, {})
    print(f"\n-- {sess} ----")
    print(f"{'Metric':<18}{'OLD (hardcoded)':<22}{'V12 (SessionClock)':<22}{'Delta':<12}")
    print(f"{'-'*18}{'-'*22}{'-'*22}{'-'*12}")
    for key, label, kind in [('trades','Trades','int'),('wr','Win Rate','pct'),
                              ('return','Return','pct'),('dd','Max DD','pct'),
                              ('pf','Profit Factor','pf'),('stopped','Days Stopped','int')]:
        ov = o.get(key, 0); nv = n.get(key, 0)
        delta = ''
        if key in ('wr','return','pf') and ov not in (0, None) and nv not in (0, None):
            d = nv - ov
            delta = f"{'+'if d>0 else ''}{d:.2f}"
        print(f"{label:<18}{fmt(ov,kind):<22}{fmt(nv,kind):<22}{delta:<12}")

# Combined total
def total(s):
    t = sum(v.get('trades',0) for v in s.values())
    r = sum(v.get('return',0) for v in s.values())
    return t, r
ot, oret = total(stats_old); nt, nret = total(stats_v12)
print(f"\n-- TOTAL ----")
print(f"{'Metric':<18}{'OLD (hardcoded)':<22}{'V12 (SessionClock)':<22}")
print(f"{'-'*18}{'-'*22}{'-'*22}")
print(f"{'Total Trades':<18}{fmt(ot,'int'):<22}{fmt(nt,'int'):<22}")
print(f"{'Sum Return':<18}{fmt(oret,'pct'):<22}{fmt(nret,'pct'):<22}")
