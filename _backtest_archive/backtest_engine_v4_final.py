#!/usr/bin/env python3
"""
Final Backtest — HA-Filtered Buy Gate + Baseline, ใช้ engine_v4.indicators
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from collections import defaultdict
from datetime import timezone

from data_provider_twelvedata import fetch_twelvedata
from engine_v4.indicators import add_indicators
from engine_v4.router import SignalRouter
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock

print("[INFO] Loading Twelve Data 15m XAU/USD...")
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
print(f"[OK] {len(df)} candles ({df.index.min().date()} -> {df.index.max().date()})")

df = add_indicators(df).dropna()
print(f"[BARS] After indicators: {len(df)}")

clock = SessionClock()
gate = FinalGate(clock)
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()
router = SignalRouter(clock, gate, buy_eng, sell_eng)

trades = []
for i in range(20, len(df)-40):
    row = df.iloc[i]; ts = row.name
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    session_state = clock.get(ts)
    # BUY
    gate_buy = gate.evaluate(session_state, 'BUY', df=df, idx=i, daily_dd_ok=True, consec_loss_ok=True)
    signal = buy_eng.evaluate(df, i, session_state, gate_buy)
    if signal:
        entry = signal['entry']; sl = signal['sl']; tp = signal['tp']
        be_act = False; highest = entry; exit_price = entry
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hh, ll = r['high'], r['low']
            if hh > highest: highest = hh
            if not be_act and highest >= signal['be_trigger']: be_act = True; sl = entry
            if be_act: sl = max(sl, highest * signal['trail_factor'])
            if hh >= tp: exit_price = tp; break
            if ll <= sl: exit_price = sl; break
        else:
            exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'time': ts, 'session': signal['session'], 'dir': 'BUY',
                       'entry': entry, 'exit': exit_price, 'sl': sl})
    # SELL
    gate_sell = gate.evaluate(session_state, 'SELL', daily_dd_ok=True, consec_loss_ok=True)
    signal = sell_eng.evaluate(df, i, session_state, gate_sell)
    if signal:
        entry = signal['entry']; sl = signal['sl']; tp = signal['tp']
        mid_crossed = False; exit_price = entry
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hh, ll = r['high'], r['low']
            if not mid_crossed and ll <= r['BB_Mid']: mid_crossed = True; sl = entry
            if ll <= tp: exit_price = tp; break
            if hh >= sl: exit_price = sl; break
        else:
            exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'time': ts, 'session': signal['session'], 'dir': 'SELL',
                       'entry': entry, 'exit': exit_price, 'sl': sl})

print(f"Total trades: {len(trades)}")

def simulate(trades, initial=10000, risk_pct=0.0075, max_contracts=10,
             daily_dd_limit=0.03, max_consec_loss=5):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades':[], 'curve':[initial], 'equity':initial,
                                    'daily_eq_start':initial, 'current_day':None,
                                    'consec_loss':0, 'stop_day':False, 'stopped':0, 'max_dd':0})
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

stats = simulate(trades)

def fmt(v, kind='pct'):
    if v == float('inf'): return 'inf'
    if v is None or (isinstance(v, float) and np.isnan(v)): return '-'
    if kind == 'pct': return f"{v:.2f}%"
    if kind == 'pf':  return f"{v:.2f}"
    if kind == 'int': return f"{int(v)}"
    return str(v)

print("\n" + "="*80)
print(f"{'FINAL PRODUCTION BACKTEST — HA-Filtered Buy + Baseline':^80}")
print("="*80)
for sess in ['ASIA', 'LONDON', 'NY']:
    s = stats.get(sess, {})
    print(f"\n-- {sess} --")
    for key, label, kind in [('trades','Trades','int'),('wr','Win Rate','pct'),
                              ('return','Return','pct'),('dd','Max DD','pct'),
                              ('pf','Profit Factor','pf')]:
        print(f"{label:<18}{fmt(s.get(key,0), kind):<22}")
tot_ret = sum(v['return'] for v in stats.values())
print(f"\nTOTAL SUM RETURN: {fmt(tot_ret,'pct')}")
print(f"NY Max DD: {fmt(stats.get('NY', {}).get('dd', 0), 'pct')}")
