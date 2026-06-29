#!/usr/bin/env python3
"""
แยกเหตุผลของ 41 trades ที่ถูก Risk Gate ปฏิเสธ
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collections import defaultdict, Counter
from datetime import timezone

from data_provider_twelvedata import fetch_twelvedata
from engine_v4.indicators import add_indicators
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock

print("[INFO] Loading data...")
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
df = add_indicators(df).dropna()
print(f"[OK] {len(df)} bars")

clock = SessionClock()
gate = FinalGate(clock)
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()

# สร้าง trades (พร้อม exit) เหมือนเดิม
all_trades = []
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
        all_trades.append({'time': ts, 'session': signal['session'], 'dir': 'BUY',
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
        all_trades.append({'time': ts, 'session': signal['session'], 'dir': 'SELL',
                           'entry': entry, 'exit': exit_price, 'sl': sl})

print(f"Total trades generated: {len(all_trades)}")

# Simulation with reason tracking
trades_sorted = sorted(all_trades, key=lambda x: x['time'])
executed = []
skipped = []   # จะเก็บ (trade, reason)

initial = 10000; risk_pct = 0.0075; max_contracts = 10
daily_dd_limit = 0.03; max_consec_loss = 5

sessions = defaultdict(lambda: {
    'equity': initial, 'daily_eq_start': initial,
    'current_day': None, 'consec_loss': 0, 'stop_day': False,
    'stop_reason': None   # เก็บเหตุผลที่ทำให้ stop
})

for t in trades_sorted:
    sess = t['session']
    sd = sessions[sess]
    day = t['time'].date()
    if day != sd['current_day']:
        sd['current_day'] = day
        sd['daily_eq_start'] = sd['equity']
        sd['consec_loss'] = 0
        sd['stop_day'] = False
        sd['stop_reason'] = None

    if sd['stop_day']:
        skipped.append((t, sd['stop_reason']))
        continue

    # Execute trade
    sl_dist = abs(t['entry'] - t['sl'])
    if sl_dist < 0.5: sl_dist = 0.5
    contracts = (sd['equity'] * risk_pct) / (sl_dist * 10)
    contracts = max(0.01, min(contracts, max_contracts))
    pnl_pts = (t['exit'] - t['entry']) if t['dir'] == 'BUY' else (t['entry'] - t['exit'])
    pnl_dollar = pnl_pts * 10 * contracts
    sd['equity'] += pnl_dollar

    if pnl_dollar <= 0:
        sd['consec_loss'] += 1
    else:
        sd['consec_loss'] = 0

    daily_dd = (sd['daily_eq_start'] - sd['equity']) / sd['daily_eq_start']
    if daily_dd >= daily_dd_limit:
        sd['stop_day'] = True
        sd['stop_reason'] = 'Daily DD'
    elif sd['consec_loss'] >= max_consec_loss:
        sd['stop_day'] = True
        sd['stop_reason'] = 'Consecutive Loss'
    # If both, Daily DD wins (first condition)

    executed.append(t)

print(f"Executed: {len(executed)}, Skipped: {len(skipped)}")

# วิเคราะห์เหตุผล
reason_counter = Counter()
for trade, reason in skipped:
    reason_counter[reason] += 1

print("\nSkipped trades by reason:")
for reason, count in reason_counter.items():
    print(f"  {reason}: {count}")

# แจกแจงตาม session
print("\nSkipped trades by reason per session:")
reason_session = defaultdict(lambda: Counter())
for trade, reason in skipped:
    reason_session[trade['session']][reason] += 1
for sess in ['ASIA','LONDON','NY']:
    print(f"  {sess}: {dict(reason_session[sess])}")
