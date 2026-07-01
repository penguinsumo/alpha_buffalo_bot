import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collections import Counter
from data_provider_twelvedata import fetch_twelvedata
from engine_v4.indicators import add_indicators
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from session_clock import SessionClock

df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]
df = add_indicators(df).dropna()

clock = SessionClock()
gate = FinalGate(clock)
buy_eng = BuySignalEngine()
sell_eng = SellSignalEngine()

trades = []
for i in range(20, len(df)-40):
    row = df.iloc[i]; ts = row.name
    if ts.tzinfo is None: ts = ts.tz_localize('UTC')
    session_state = clock.get(ts)
    # BUY
    gate_buy = gate.evaluate(session_state, 'BUY', df=df, idx=i, daily_dd_ok=True, consec_loss_ok=True)
    signal = buy_eng.evaluate(df, i, session_state, gate_buy)
    if signal:
        trades.append({'session': signal['session'], 'dir': 'BUY', 'time': ts})
    # SELL
    gate_sell = gate.evaluate(session_state, 'SELL', daily_dd_ok=True, consec_loss_ok=True)
    signal = sell_eng.evaluate(df, i, session_state, gate_sell)
    if signal:
        trades.append({'session': signal['session'], 'dir': 'SELL', 'time': ts})

cnt = Counter(t['session'] for t in trades)
print("Trade count by session:")
for k, v in cnt.items():
    print(f"  {k}: {v}")
print(f"Total: {sum(cnt.values())}")

# Show mystery sessions
expected = {'ASIA', 'LONDON', 'NY'}
mystery = set(cnt.keys()) - expected
if mystery:
    print(f"\nMystery sessions: {mystery}")
    for t in trades:
        if t['session'] in mystery:
            print(f"  {t['time']} {t['session']} {t['dir']}")
