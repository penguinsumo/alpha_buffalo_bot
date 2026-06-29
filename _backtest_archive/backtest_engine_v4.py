#!/usr/bin/env python3
"""
Backtest Engine V4 — ใช้ SignalRouter, SessionClock จริง, Logic Final
เปรียบเทียบผลกับ Final Backtest (Sum Return 476.68%, NY DD 1.49%)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from datetime import timezone
from collections import defaultdict

from engine_v4.router import SignalRouter
from engine_v4.session_gate import SessionGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock, SessionState

print("[INFO] Loading Twelve Data 15m XAU/USD...")
from data_provider_twelvedata import fetch_twelvedata
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
print(f"[OK] Twelve Data 15m: {len(df)} candles ({df.index.min().date()} -> {df.index.max().date()})")

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
    df1h = df.resample('1h').agg({'high':'max','low':'min','close':'last'}).dropna()
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
    df['PRZ_Next'] = df['Swing_L']
    df1h['EMA50_1h'] = df1h['close'].ewm(span=50).mean()
    trend_up = (df1h['close'] > df1h['EMA50_1h']).astype(int)
    trend_up = trend_up.reindex(df.index, method='ffill').fillna(0)
    df['Trend_1H_Up'] = trend_up.astype(bool)
    return df

df = add_indicators(df).dropna()
print(f"[BARS] After indicators: {len(df)}")

clock = SessionClock()
gate = SessionGate(clock)
buy_engine = BuySignalEngine()
sell_engine = SellSignalEngine()
router = SignalRouter(clock, gate, buy_engine, sell_engine)

def generate_trades_engine(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]
        ts = row.name
        # localize to UTC (Twelve Data is naive UTC)
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        session_state = clock.get(ts)
        # BUY
        gate_buy = gate.evaluate(session_state, 'BUY', True, True)
        signal_buy = buy_engine.evaluate(df, i, session_state, gate_buy)
        if signal_buy:
            entry = signal_buy['entry']
            sl = signal_buy['sl']
            tp = signal_buy['tp']
            be_act = False; highest = entry; exit_price = entry
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if hh > highest: highest = hh
                if not be_act and highest >= signal_buy['be_trigger']:
                    be_act = True; sl = entry
                if be_act: sl = max(sl, highest * signal_buy['trail_factor'])
                if hh >= tp: exit_price = tp; break
                if ll <= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'session':signal_buy['session'],'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':row.name})
        # SELL
        gate_sell = gate.evaluate(session_state, 'SELL', True, True)
        signal_sell = sell_engine.evaluate(df, i, session_state, gate_sell)
        if signal_sell:
            entry = signal_sell['entry']
            sl = signal_sell['sl']
            tp = signal_sell['tp']
            mid_crossed = False; exit_price = entry
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if not mid_crossed and ll <= signal_sell['visual_sl_mid']:
                    mid_crossed = True; sl = entry
                if ll <= tp: exit_price = tp; break
                if hh >= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'session':signal_sell['session'],'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':row.name})
    return trades

trades = generate_trades_engine(df)
print(f"[TRADES] Total: {len(trades)}")

def simulate(trades, initial=10000, risk_pct=0.0075, max_contracts=10,
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

stats = simulate(trades)

def fmt(v, kind='pct'):
    if v == float('inf'): return 'inf'
    if v is None or (isinstance(v, float) and np.isnan(v)): return '-'
    if kind == 'pct': return f"{v:.2f}%"
    if kind == 'pf':  return f"{v:.2f}"
    if kind == 'int': return f"{int(v)}"
    return str(v)

print("\n" + "="*80)
print(f"{'ENGINE V4 BACKTEST — Test SignalRouter with Final Logic':^80}")
print("="*80)
for sess in ['ASIA', 'LONDON', 'NY']:
    s = stats.get(sess, {})
    print(f"\n-- {sess} ----")
    print(f"{'Metric':<18}{'Value':<22}")
    print(f"{'-'*18}{'-'*22}")
    for key, label, kind in [('trades','Trades','int'),('wr','Win Rate','pct'),
                              ('return','Return','pct'),('dd','Max DD','pct'),
                              ('pf','Profit Factor','pf'),('stopped','Days Stopped','int')]:
        v = s.get(key, 0)
        print(f"{label:<18}{fmt(v, kind):<22}")
tot_ret = sum(s.get('return',0) for s in stats.values())
tot_trades = sum(s.get('trades',0) for s in stats.values())
ny_dd = stats.get('NY', {}).get('dd', 0)
print(f"\n-- TOTAL ----")
print(f"{'Total Trades':<18}{fmt(tot_trades, 'int')}")
print(f"{'Sum Return':<18}{fmt(tot_ret, 'pct')}")
print(f"{'NY Max DD':<18}{fmt(ny_dd, 'pct')}")
print(f"\nTarget: Sum Return 476.68%, NY DD 1.49%")
print(f"Result: Sum Return {tot_ret:.2f}%, NY DD {ny_dd:.2f}%")
if abs(tot_ret - 476.68) < 5 and abs(ny_dd - 1.49) < 0.3:
    print("✅ Engine V4 ผ่านการทดสอบ — พร้อม Shadow Deploy")
else:
    print("⚠️ ผลต่างจาก Baseline — ตรวจสอบ Logic/Data เพิ่มเติม")
