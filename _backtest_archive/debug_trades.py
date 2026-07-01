#!/usr/bin/env python3
"""
Debug: เปรียบเทียบ trades จาก engine_v4 กับ final_baseline
โดยจำลอง Final Baseline แบบตรงเป๊ะ
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from collections import Counter
from datetime import timezone
from session_clock import SessionClock, SessionState

# ============================================================
# PART 1: Final Baseline Logic (คัดลอกจาก final_backtest_v12.py)
# ============================================================
def get_session_v12(ts):
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC') if hasattr(ts, 'tz_localize') else ts.replace(tzinfo=timezone.utc)
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
    # 1H Trend Filter
    df1h['EMA50_1h'] = df1h['close'].ewm(span=50).mean()
    trend_up = (df1h['close'] > df1h['EMA50_1h']).astype(int)
    trend_up = trend_up.reindex(df.index, method='ffill').fillna(0)
    df['Trend_1H_Up'] = trend_up.astype(bool)
    return df

def generate_trades_baseline(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        session, liquidity = get_session_v12(ts)
        if session == 'CLOSED': continue
        utc_hour = ts.hour

        # BUY (NY >= 15)
        if (session == 'NY' and utc_hour >= 15 and
            row['EMA20'] > row['EMA50'] and row['Diff'] > 0 and row['Trend_1H_Up']):
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
                trades.append({'session':session,'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':ts})

        # SELL (ทุก session)
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and
            row['high'] >= row['BB_Upper'] * 0.98 and not row['Trend_1H_Up']):
            entry = row['close']; sl = entry + row['ATR14'] * 1.5; exit_price=entry; mid_crossed=False
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh, ll = r['high'], r['low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed = True; sl = entry
                if ll <= tp: exit_price = tp; break
                if hh >= sl: exit_price = sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            trades.append({'session':session,'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    return trades

# ============================================================
# PART 2: Engine V4 (เหมือน backtest_engine)
# ============================================================
from engine_v4.router import SignalRouter
from engine_v4.session_gate import SessionGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine

# data
from data_provider_twelvedata import fetch_twelvedata
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
df_base = add_indicators_base(df).dropna()
df_eng = add_indicators_base(df).dropna()

# --- Baseline ---
trades_base = generate_trades_baseline(df_base)
cnt_base = Counter((t['session'], t['dir']) for t in trades_base)
print("=== Final Baseline Trades (session, dir) ===")
for k, v in sorted(cnt_base.items()):
    print(f"  {k}: {v}")

# --- Engine V4 ---
clock = SessionClock()
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
        trades_eng.append({'session':signal['session'],'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':row.name})
    # SELL
    gate_sell = gate.evaluate(session_state, 'SELL', True, True)
    signal = sell_eng.evaluate(df_eng, i, session_state, gate_sell)
    if signal:
        entry=signal['entry']; sl=signal['sl']; tp=signal['tp']
        mid_crossed=False; exit_price=entry
        for j in range(i+1, min(i+40, len(df_eng))):
            r=df_eng.iloc[j]; hh,ll=r['high'],r['low']
            if not mid_crossed and ll<=signal['visual_sl_mid']: mid_crossed=True; sl=entry
            if ll<=tp: exit_price=tp; break
            if hh>=sl: exit_price=sl; break
        else:
            exit_price=df_eng.iloc[min(i+40-1,len(df_eng)-1)]['close']
        trades_eng.append({'session':signal['session'],'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':row.name})

cnt_eng = Counter((t['session'], t['dir']) for t in trades_eng)
print("\n=== Engine V4 Trades (session, dir) ===")
for k, v in sorted(cnt_eng.items()):
    print(f"  {k}: {v}")

print("\n=== Differences ===")
for k in cnt_base.keys() | cnt_eng.keys():
    b = cnt_base.get(k,0); e = cnt_eng.get(k,0)
    if b != e:
        print(f"  {k}: Baseline {b} vs Engine {e} (diff: {e-b:+d})")
