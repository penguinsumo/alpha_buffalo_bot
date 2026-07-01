#!/usr/bin/env python3
"""Debug: ใช้ simulate จาก final baseline รันกับ trades ของทั้งสอง"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collections import defaultdict
from datetime import timezone

# ====== Simulate function (จาก final_backtest_v12.py เป๊ะ) ======
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

# ====== Data & Indicators ======
from data_provider_twelvedata import fetch_twelvedata
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]

def add_indicators_base(df):
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

df_full = add_indicators_base(df).dropna()
print(f"Total bars: {len(df_full)}")

# ====== Baseline Trades ======
from session_clock import SessionClock

def get_session_v12(ts):
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    clock = SessionClock()
    s = clock.get(ts)
    return s.session, s.liquidity

def generate_trades_baseline(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        session, _ = get_session_v12(ts)
        if session == 'CLOSED': continue
        utc_hour = ts.hour
        if (session == 'NY' and utc_hour >= 15 and row['EMA20'] > row['EMA50'] and row['Diff'] > 0 and row['Trend_1H_Up']):
            gl = row['Swing_H'] - row['Diff'] * 1.0
            gh = row['Swing_H'] - row['Diff'] * 0.5
            if gl <= row['close'] <= gh and row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02:
                entry = row['close']; sl = entry - row['ATR14'] * 1.5; tp = row['BB_Upper']
                be_act=False; highest=entry; exit_price=entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; hh, ll = r['high'], r['low']
                    if hh > highest: highest = hh
                    if not be_act and highest >= entry * 1.0015: be_act = True; sl = entry
                    if be_act: sl = max(sl, highest * 0.9995)
                    if hh >= tp: exit_price = tp; break
                    if ll <= sl: exit_price = sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
                trades.append({'time':ts,'session':session,'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl})
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98 and not row['Trend_1H_Up']):
            entry = row['close']; sl = entry + row['ATR14'] * 1.5; exit_price=entry; mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed = True; sl = entry
                if ll <= tp: exit_price = tp; break
                if hh >= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'time':ts,'session':session,'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl})
    return trades

trades_baseline = generate_trades_baseline(df_full)
print(f"Baseline trades: {len(trades_baseline)}")

# ====== Engine V4 Trades ======
from engine_v4.router import SignalRouter
from engine_v4.session_gate import SessionGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock as SC2

clock = SC2()
gate = SessionGate(clock)
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()
router = SignalRouter(clock, gate, buy_eng, sell_eng)

trades_eng = []
for i in range(20, len(df_full)-40):
    row = df_full.iloc[i]; ts = row.name
    if ts.tzinfo is None: ts = ts.tz_localize('UTC')
    session_state = clock.get(ts)
    gate_buy = gate.evaluate(session_state, 'BUY', True, True)
    signal = buy_eng.evaluate(df_full, i, session_state, gate_buy)
    if signal:
        entry=signal['entry']; sl=signal['sl']; tp=signal['tp']
        be_act=False; highest=entry; exit_price=entry
        for j in range(i+1, min(i+40, len(df_full))):
            r=df_full.iloc[j]; hh,ll=r['high'],r['low']
            if hh>highest: highest=hh
            if not be_act and highest>=signal['be_trigger']: be_act=True; sl=entry
            if be_act: sl=max(sl,highest*signal['trail_factor'])
            if hh>=tp: exit_price=tp; break
            if ll<=sl: exit_price=sl; break
        else:
            exit_price=df_full.iloc[min(i+40-1,len(df_full)-1)]['close']
        trades_eng.append({'time':row.name,'session':signal['session'],'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl})
    gate_sell = gate.evaluate(session_state, 'SELL', True, True)
    signal = sell_eng.evaluate(df_full, i, session_state, gate_sell)
    if signal:
        entry=signal['entry']; sl=signal['sl']; tp=signal['tp']
        mid_crossed=False; exit_price=entry
        for j in range(i+1, min(i+40, len(df_full))):
            r=df_full.iloc[j]; hh,ll=r['high'],r['low']
            if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
            if ll<=tp: exit_price=tp; break
            if hh>=sl: exit_price=sl; break
        else:
            exit_price=df_full.iloc[min(i+40-1,len(df_full)-1)]['close']
        trades_eng.append({'time':row.name,'session':signal['session'],'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl})

print(f"Engine trades: {len(trades_eng)}")

# ====== Simulate both with same function ======
stats_base = simulate(trades_baseline)
stats_eng = simulate(trades_eng)

print("\n=== Baseline ===")
for sess in ['ASIA','LONDON','NY']:
    s = stats_base[sess]
    print(f"{sess}: Return={s['return']:.2f}%, DD={s['dd']:.2f}%, Trades={s['trades']}")

print("\n=== Engine V4 ===")
for sess in ['ASIA','LONDON','NY']:
    s = stats_eng[sess]
    print(f"{sess}: Return={s['return']:.2f}%, DD={s['dd']:.2f}%, Trades={s['trades']}")
