#!/usr/bin/env python3
"""
Analyze BUY performance by UTC hour (Full Buy Logic)
ใช้ SessionClock จริงเพื่อระบุ Session ของแต่ละชั่วโมง
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
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
    df1h['EMA50_1h'] = df1h['close'].ewm(span=50).mean()
    trend_up = (df1h['close'] > df1h['EMA50_1h']).astype(int)
    trend_up = trend_up.reindex(df.index, method='ffill').fillna(0)
    df['Trend_1H_Up'] = trend_up.astype(bool)
    return df

df = add_indicators(df).dropna()

# ====== 2. ENGINE & GATE (Full Buy) ======
from session_clock import SessionClock, SessionState
from engine_v4.session_gate import GateResult
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine

clock = SessionClock()
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()

class FullBuySessionGate:
    def __init__(self, clock):
        self.clock = clock
    def evaluate(self, session_state, direction, daily_dd_ok=True, consec_loss_ok=True):
        if session_state.session == 'CLOSED':
            return GateResult(False, "Market closed")
        if not daily_dd_ok:
            return GateResult(False, "Daily DD limit reached")
        if not consec_loss_ok:
            return GateResult(False, "Max consecutive losses reached")
        return GateResult(True, "OK")

gate_full = FullBuySessionGate(clock)

# ====== 3. GENERATE BUY TRADES ======
buy_trades = []
for i in range(20, len(df)-40):
    row = df.iloc[i]; ts = row.name
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    session_state = clock.get(ts)
    gate_buy = gate_full.evaluate(session_state, 'BUY', True, True)
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
        pnl_pts = exit_price - entry
        buy_trades.append({
            'time': ts,
            'session': signal['session'],
            'utc_hour': ts.hour,
            'entry': entry,
            'exit': exit_price,
            'pnl_pts': pnl_pts,
            'win': pnl_pts > 0
        })

print(f"Total BUY trades: {len(buy_trades)}")

# ====== 4. GROUP BY UTC HOUR ======
hourly = defaultdict(lambda: {'count':0, 'wins':0, 'pnl_sum':0.0, 'session':''})
for t in buy_trades:
    h = t['utc_hour']
    hourly[h]['count'] += 1
    hourly[h]['pnl_sum'] += t['pnl_pts']
    if t['win']:
        hourly[h]['wins'] += 1
    if not hourly[h]['session']:
        hourly[h]['session'] = t['session']

# ====== 5. OUTPUT ======
print(f"\n{'UTC Hr':<8} {'Session':<10} {'Trades':<8} {'Win Rate':<10} {'PnL Sum':<12} {'Avg PnL':<10} {'Verdict'}")
print("-" * 70)
for h in sorted(hourly.keys()):
    d = hourly[h]
    wr = d['wins'] / d['count'] * 100 if d['count'] else 0
    avg = d['pnl_sum'] / d['count'] if d['count'] else 0
    verdict = "PROFIT" if d['pnl_sum'] > 0 else ("LOSS" if d['pnl_sum'] < 0 else "FLAT")
    print(f"{h:02d}:00   {d['session']:<10} {d['count']:<8} {wr:<10.1f}% {d['pnl_sum']:<+12.1f} {avg:<+10.1f} {verdict}")

# ====== 6. SESSION SUMMARY ======
print("\n=== Session Summary (Buy only) ===")
session_sum = defaultdict(lambda: {'count':0, 'wins':0, 'pnl':0.0})
for t in buy_trades:
    sess = t['session']
    session_sum[sess]['count'] += 1
    session_sum[sess]['pnl'] += t['pnl_pts']
    if t['win']: session_sum[sess]['wins'] += 1
for sess in ['ASIA','LONDON','NY']:
    s = session_sum[sess]
    wr = s['wins']/s['count']*100 if s['count'] else 0
    print(f"{sess}: Trades={s['count']}, Win Rate={wr:.1f}%, Total PnL={s['pnl']:+.1f} pts")
