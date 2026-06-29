#!/usr/bin/env python3
"""
Backtest Filtered Buy by BKK Profit Hours (ASIA/LONDON) + NY >=15 UTC
ใช้ Engine V4 + SessionClock จริง
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from collections import defaultdict
from datetime import timezone

# ====== 1. DATA & INDICATORS ======
from data_provider_twelvedata import fetch_twelvedata
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]

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

# ====== 2. SESSION CLOCK & GATE (Filtered Buy by BKK Profit Hours) ======
from session_clock import SessionClock, SessionState
from engine_v4.session_gate import GateResult

class FilteredBuyGate:
    """
    อนุญาต Buy เฉพาะ:
    - ASIA: BKK 05,06,07,08,12,13
    - LONDON: BKK 15,17
    - NY: UTC >= 15 (BKK >= 22)
    """
    # กำหนด BKK hours ที่อนุญาต Buy สำหรับแต่ละ session
    ASIA_BUY_HOURS_BKK = {5,6,7,8,12,13}
    LONDON_BUY_HOURS_BKK = {15,17}

    def __init__(self, clock):
        self.clock = clock

    def evaluate(self, session_state: SessionState, direction: str,
                 daily_dd_ok=True, consec_loss_ok=True) -> GateResult:
        if session_state.session == 'CLOSED':
            return GateResult(False, "Market closed")
        if not daily_dd_ok:
            return GateResult(False, "Daily DD limit reached")
        if not consec_loss_ok:
            return GateResult(False, "Max consecutive losses reached")

        if direction == 'BUY':
            sess = session_state.session
            bkk_hour = session_state.bkk_hour
            if sess == 'ASIA' and bkk_hour in self.ASIA_BUY_HOURS_BKK:
                return GateResult(True, f"ASIA buy allowed at BKK {bkk_hour}")
            elif sess == 'LONDON' and bkk_hour in self.LONDON_BUY_HOURS_BKK:
                return GateResult(True, f"LONDON buy allowed at BKK {bkk_hour}")
            elif sess == 'NY' and session_state.utc_hour >= 15:
                return GateResult(True, f"NY buy allowed at UTC {session_state.utc_hour}")
            else:
                return GateResult(False, "Buy not allowed in this hour/session")
        else:  # SELL: อนุญาตทุก session ยกเว้น CLOSED
            return GateResult(True, "Sell allowed")
        return GateResult(False, "Unknown")

# ====== 3. ENGINE ======
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from engine_v4.session_gate import SessionGate as OrigGate  # สำหรับ Baseline

clock = SessionClock()
gate_filtered = FilteredBuyGate(clock)
gate_baseline = OrigGate(clock)       # Baseline: Buy เฉพาะ NY >=15 UTC
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()

def generate_trades(gate):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if ts.tzinfo is None:
            ts = ts.tz_localize('UTC')
        session_state = clock.get(ts)

        # BUY
        gate_buy = gate.evaluate(session_state, 'BUY', True, True)
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
            trades.append({'time': ts, 'session': session_state.session, 'dir': 'BUY',
                           'entry': entry, 'exit': exit_price, 'sl': sl})

        # SELL (เหมือนเดิมทุกกรณี)
        gate_sell = gate.evaluate(session_state, 'SELL', True, True)
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
            trades.append({'time': ts, 'session': session_state.session, 'dir': 'SELL',
                           'entry': entry, 'exit': exit_price, 'sl': sl})
    return trades

trades_filtered = generate_trades(gate_filtered)
trades_baseline = generate_trades(gate_baseline)
print(f"Filtered Buy Trades: {len(trades_filtered)}")
print(f"Baseline Trades: {len(trades_baseline)}")

# ====== 4. SIMULATE (function เหมือนเดิม) ======
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

stats_filtered = simulate(trades_filtered)
stats_baseline = simulate(trades_baseline)

# ====== 5. OUTPUT ======
def fmt(v, kind='pct'):
    if v == float('inf'): return 'inf'
    if v is None or (isinstance(v, float) and np.isnan(v)): return '-'
    if kind == 'pct': return f"{v:.2f}%"
    if kind == 'pf':  return f"{v:.2f}"
    if kind == 'int': return f"{int(v)}"
    return str(v)

print("\n" + "="*90)
print(f"{'FILTERED BUY (ASIA/LONDON Profit Hours + NY >=15) vs BASELINE (NY Only)':^90}")
print("="*90)
for sess in ['ASIA', 'LONDON', 'NY']:
    f = stats_filtered.get(sess, {})
    b = stats_baseline.get(sess, {})
    print(f"\n-- {sess} --")
    print(f"{'Metric':<18}{'Filtered Buy':<22}{'Baseline':<22}{'Diff':<12}")
    for key, label, kind in [('trades','Trades','int'),('wr','Win Rate','pct'),
                              ('return','Return','pct'),('dd','Max DD','pct'),
                              ('pf','Profit Factor','pf')]:
        fv = f.get(key, 0); bv = b.get(key, 0)
        diff = fv - bv if kind in ('pct','pf','int') else ''
        print(f"{label:<18}{fmt(fv,kind):<22}{fmt(bv,kind):<22}{diff if diff != '' else '':<12}")

tot_filt = sum(v['return'] for v in stats_filtered.values())
tot_base = sum(v['return'] for v in stats_baseline.values())
print(f"\nTOTAL SUM RETURN: Filtered = {fmt(tot_filt,'pct')}, Baseline = {fmt(tot_base,'pct')}")
