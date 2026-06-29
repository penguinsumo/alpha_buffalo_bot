#!/usr/bin/env python3
"""Debug: เทียบ exit_price ทีละ trade"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from datetime import timezone
from session_clock import SessionClock

# ============ Baseline (เหมือน debug_trades.py) ============
def get_session_v12(ts):
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    clock = SessionClock()
    s = clock.get(ts)
    return s.session, s.liquidity

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
                trades.append({'time':ts,'session':session,'dir':'BUY','entry':entry,'exit':exit_price})
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
            trades.append({'time':ts,'session':session,'dir':'SELL','entry':entry,'exit':exit_price})
    return trades

# ============ Engine V4 (เหมือน backtest_engine_v4.py ที่แก้ไขแล้ว) ============
from engine_v4.router import SignalRouter
from engine_v4.session_gate import SessionGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock as SC2

from data_provider_twelvedata import fetch_twelvedata
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
df_base = add_indicators_base(df).dropna()
df_eng = add_indicators_base(df).dropna()

trades_base = generate_trades_baseline(df_base)

clock = SC2()
gate = SessionGate(clock)
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()
router = SignalRouter(clock, gate, buy_eng, sell_eng)

trades_eng = []
for i in range(20, len(df_eng)-40):
    row = df_eng.iloc[i]; ts = row.name
    if ts.tzinfo is None: ts = ts.tz_localize('UTC')
    session_state = clock.get(ts)
    # BUY
    gate_buy = gate.evaluate(session_state, 'BUY', True, True)
    signal = buy_eng.evaluate(df_eng, i, session_state, gate_buy)
    if signal:
        entry=signal['entry']; sl=signal['sl']; tp=signal['tp']
        be_act=False; highest=entry; exit_price=entry
        for j in range(i+1, min(i+40, len(df_eng))):
            r=df_eng.iloc[j]; hh,ll=r['high'],r['low']
            if hh>highest: highest=hh
            if not be_act and highest>=signal['be_trigger']: be_act=True; sl=entry
            if be_act: sl=max(sl,highest*signal['trail_factor'])
            if hh>=tp: exit_price=tp; break
            if ll<=sl: exit_price=sl; break
        else:
            exit_price=df_eng.iloc[min(i+40-1,len(df_eng)-1)]['close']
        trades_eng.append({'time':row.name,'session':signal['session'],'dir':'BUY','entry':entry,'exit':exit_price})
    # SELL
    gate_sell = gate.evaluate(session_state, 'SELL', True, True)
    signal = sell_eng.evaluate(df_eng, i, session_state, gate_sell)
    if signal:
        entry=signal['entry']; sl=signal['sl']; tp=signal['tp']
        mid_crossed=False; exit_price=entry
        for j in range(i+1, min(i+40, len(df_eng))):
            r=df_eng.iloc[j]; hh,ll=r['high'],r['low']
            # ใช้ Dynamic BB_Mid (r['BB_Mid'])
            if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
            if ll<=tp: exit_price=tp; break
            if hh>=sl: exit_price=sl; break
        else:
            exit_price=df_eng.iloc[min(i+40-1,len(df_eng)-1)]['close']
        trades_eng.append({'time':row.name,'session':signal['session'],'dir':'SELL','entry':entry,'exit':exit_price})

# เทียบ exit_price
df_base_list = sorted(trades_base, key=lambda x: x['time'])
df_eng_list = sorted(trades_eng, key=lambda x: x['time'])

print(f"Baseline trades: {len(df_base_list)}")
print(f"Engine trades: {len(df_eng_list)}")

# จับคู่ทีละตัว (สมมติว่าเรียงตรงกันเพราะ entry logic เหมือนกัน)
diff_count = 0
for i, (b, e) in enumerate(zip(df_base_list, df_eng_list)):
    if b['time'] != e['time'] or b['dir'] != e['dir']:
        print(f"Mismatch at index {i}: Baseline {b['time']} {b['dir']} vs Engine {e['time']} {e['dir']}")
        break
    if abs(b['exit'] - e['exit']) > 0.01:
        diff_count += 1
        if diff_count <= 10:  # show first 10 differences
            print(f"Diff #{diff_count}: {b['time']} {b['session']} {b['dir']} | Baseline exit={b['exit']:.2f} Engine exit={e['exit']:.2f} (diff={e['exit']-b['exit']:.2f})")

print(f"\nTotal trades with exit price difference > 0.01: {diff_count}")
