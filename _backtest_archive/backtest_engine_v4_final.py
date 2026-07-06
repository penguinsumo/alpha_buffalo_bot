#!/usr/bin/env python3
"""
Final Backtest — HA-Filtered Buy Gate + Baseline, ใช้ engine_v4.indicators
"""
import sys, os, json
from pathlib import Path
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
        entry = signal['entry']; initial_sl = signal['sl']; sl = signal['sl']; tp = signal['tp']
        be_act = False; highest = entry; exit_price = entry; exit_reason = 'TIME'
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hh, ll = r['high'], r['low']
            if hh > highest: highest = hh
            if not be_act and highest >= signal['be_trigger']: be_act = True; sl = entry
            if be_act: sl = max(sl, highest * signal['trail_factor'])
            if hh >= tp: exit_price = tp; exit_reason = 'TP'; break
            if ll <= sl:
                exit_price = sl
                exit_reason = 'TRAIL' if sl > entry else ('BE' if sl == entry else 'SL')
                break
        else:
            exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'time': ts, 'session': signal['session'], 'dir': 'BUY',
                       'entry': entry, 'exit': exit_price, 'sl': sl,
                       'initial_sl': initial_sl, 'tp': tp,
                       'exit_reason': exit_reason,
                       'entry_mode': signal.get('entry_mode', 'BUY_BASE'),
                       'exit_mode': signal.get('exit_mode', 'BUY_TP')})
    # SELL
    gate_sell = gate.evaluate(session_state, 'SELL', daily_dd_ok=True, consec_loss_ok=True)
    signal = sell_eng.evaluate(df, i, session_state, gate_sell)
    if signal:
        entry = signal['entry']; initial_sl = signal['sl']; sl = signal['sl']; tp = signal['tp']
        mid_crossed = False; exit_price = entry; exit_reason = 'TIME'
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hh, ll = r['high'], r['low']
            if not mid_crossed and ll <= r['BB_Mid']: mid_crossed = True; sl = entry
            if ll <= tp:
                exit_price = tp
                exit_reason = signal.get('exit_mode', 'TP')
                break
            if hh >= sl:
                exit_price = sl
                exit_reason = 'BE' if sl == entry else 'SL'
                break
        else:
            exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
        trades.append({'time': ts, 'session': signal['session'], 'dir': 'SELL',
                       'entry': entry, 'exit': exit_price, 'sl': sl,
                       'initial_sl': initial_sl, 'tp': tp,
                       'exit_reason': exit_reason,
                       'entry_mode': signal.get('entry_mode', 'NONE'),
                       'exit_mode': signal.get('exit_mode', 'NONE'),
                       'v4_session_confirmed': signal.get('v4_session_confirmed', False),
                       'sell_dot_proxy': signal.get('sell_dot_proxy', False),
                       'v5_exit_qualified': signal.get('v5_exit_qualified', False),
                       'recent_micro_bos_down': signal.get('recent_micro_bos_down', False),
                       'recent_sweep_above_100': signal.get('recent_sweep_above_100', False),
                       'recent_sell_reclaim': signal.get('recent_sell_reclaim', False),
                       'ha_bearish': signal.get('ha_bearish', False),
                       'buy_obstacle_policy': signal.get('buy_obstacle_policy', ''),
                       'signal_tp': signal.get('signal_tp'),
                       'bb_lower_tp': signal.get('bb_lower_tp')})

print(f"Total trades: {len(trades)}")

def simulate(trades, initial=10000, risk_pct=0.0075, max_contracts=10,
             daily_dd_limit=0.03, max_consec_loss=5):
    trades = sorted(trades, key=lambda x: x['time'])
    executed_trades = []
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
        executed = {**t, 'pnl_$': pnl_dollar, 'contracts': contracts}
        sd['trades'].append(executed)
        executed_trades.append(executed)
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
    return stats, executed_trades

stats, executed_trades = simulate(trades)

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


def json_safe(v):
    if hasattr(v, 'isoformat'):
        return v.isoformat()
    if isinstance(v, np.bool_):
        return bool(v)
    if isinstance(v, np.integer):
        return int(v)
    if isinstance(v, np.floating):
        return float(v)
    return v

evidence = []
for t in executed_trades:
    evidence.append({
        'entry_time': json_safe(t.get('time')),
        'side': t.get('dir'),
        'session': t.get('session'),
        'entry': json_safe(t.get('entry')),
        'exit': json_safe(t.get('exit')),
        'tp': json_safe(t.get('tp')),
        'sl': json_safe(t.get('sl')),
        'initial_sl': json_safe(t.get('initial_sl')),
        'exit_reason': t.get('exit_reason'),
        'entry_mode': t.get('entry_mode'),
        'exit_mode': t.get('exit_mode'),
        'v4_session_confirmed': json_safe(t.get('v4_session_confirmed', False)),
        'sell_dot_proxy': json_safe(t.get('sell_dot_proxy', False)),
        'v5_exit_qualified': json_safe(t.get('v5_exit_qualified', False)),
        'recent_micro_bos_down': json_safe(t.get('recent_micro_bos_down', False)),
        'recent_sweep_above_100': json_safe(t.get('recent_sweep_above_100', False)),
        'recent_sell_reclaim': json_safe(t.get('recent_sell_reclaim', False)),
        'ha_bearish': json_safe(t.get('ha_bearish', False)),
        'buy_obstacle_policy': t.get('buy_obstacle_policy', ''),
        'be_policy': t.get('be_policy'),
        'trail_policy': t.get('trail_policy'),
        'v5_quality_score': json_safe(t.get('v5_quality_score')),
        'v5_quality_grade': t.get('v5_quality_grade'),
        'v5_basis': t.get('v5_basis'),
        'v5_premium_any': json_safe(t.get('v5_premium_any', False)),
        'v5_premium_micro_bos': json_safe(t.get('v5_premium_micro_bos', False)),
        'v5_premium_reclaim': json_safe(t.get('v5_premium_reclaim', False)),
        'v5_premium_sweep_ha': json_safe(t.get('v5_premium_sweep_ha', False)),
        'close_below_ema20': json_safe(t.get('close_below_ema20', False)),
        'close_below_bb_mid': json_safe(t.get('close_below_bb_mid', False)),
        'ema20_below_ema50': json_safe(t.get('ema20_below_ema50', False)),
        'bb_upper_touch_strength': json_safe(t.get('bb_upper_touch_strength')),
        'sell_rejection_wick_ratio': json_safe(t.get('sell_rejection_wick_ratio')),
        'candle_body_ratio': json_safe(t.get('candle_body_ratio')),
        'atr14_at_entry': json_safe(t.get('atr14_at_entry')),
        'entry_to_sl_points': json_safe(t.get('entry_to_sl_points')),
        'entry_to_tp_points': json_safe(t.get('entry_to_tp_points')),
        'entry_rr': json_safe(t.get('entry_rr')),
        'session_quality_gate': t.get('session_quality_gate'),
        'sell_dot_reason': t.get('sell_dot_reason'),
        'signal_tp': json_safe(t.get('signal_tp')),
        'bb_lower_tp': json_safe(t.get('bb_lower_tp')),
        'pnl_dollar': json_safe(t.get('pnl_$')),
        'contracts': json_safe(t.get('contracts')),
    })

Path('trade_evidence.json').write_text(
    json.dumps(evidence, ensure_ascii=False, indent=2),
    encoding='utf-8',
)
print(f"Evidence exported: trade_evidence.json ({len(evidence)} trades)")
