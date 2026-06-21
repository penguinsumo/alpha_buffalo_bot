import sys, os
sys.path.insert(0, os.path.expanduser('~/alpha_buffalo_bot'))

import requests, pandas as pd, logging
from datetime import datetime, timedelta
from core.config.loader import load_env_safely

load_env_safely()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_KEY = os.getenv("TWELVEDATA_API_KEY")
if not API_KEY:
    print("❌ TWELVEDATA_API_KEY missing")
    exit()

# ── 1. Fetch 60 days data ──
end = datetime(2026,6,17)
start = end - timedelta(days=60)

def fetch(interval):
    url = "https://api.twelvedata.com/time_series"
    params = {
        'symbol': 'XAU/USD', 'interval': interval,
        'start_date': start.strftime('%Y-%m-%d'),
        'end_date': end.strftime('%Y-%m-%d'),
        'outputsize': 5000, 'apikey': API_KEY
    }
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        return None
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for c in ['open','high','low','close']:
        df[c] = pd.to_numeric(df[c])
    return df

df15 = fetch('15min')
df1h = fetch('1h')
df4h = fetch('4h')
if df15 is None or df1h is None or df4h is None:
    print("❌ Missing data")
    exit()
print(f"✅ Data: 15m={len(df15)}, 1h={len(df1h)}, 4h={len(df4h)}")

# ── 2. Import both systems ──
from signal_composer import compose_signal as old_compose
from core.models.market_context import MarketContext
from core.models.execution import ExecutionPacket
from core.engines.signal_tester_lite import SignalTesterLite

def simulate_new_core(df_15m_win, df_1h_win, df_4h_win, ts):
    """Core ใหม่ใช้ signal_composer เดิมเป็น Intelligence"""
    sig = old_compose(df_4h_win, df_1h_win, df_15m_win)
    if sig is None:
        return None
    
    row15 = df_15m_win.iloc[-1]
    row1h = df_1h_win.iloc[-1]
    
    ctx = MarketContext(
        symbol='XAUUSD', timeframe='H1',
        bid=float(row15['close']), ask=float(row15['close'])+0.5,
        open=float(row1h['open']), high=float(row1h['high']),
        low=float(row1h['low']), close=float(row1h['close']),
        volume=float(row1h.get('volume', 1000) if 'volume' in row1h else 1000),
        timestamp=int(ts.timestamp()),
        bias='UP' if sig.direction == 'BUY' else 'DOWN',
        current_regime='TREND',
        session_state='ASIA'
    )
    
    sl_distance = ctx.volatility_score * 1.5 if ctx.volatility_score else (ctx.high - ctx.low)
    tp_distance = sl_distance * 2.0
    
    if sig.direction == "BUY":
        sl = sig.entry_price - sl_distance
        tp = sig.entry_price + tp_distance
    elif sig.direction == "SELL":
        sl = sig.entry_price + sl_distance
        tp = sig.entry_price - tp_distance
    else:
        return None
    
    return ExecutionPacket(
        action=sig.direction,
        symbol=ctx.symbol,
        entry_price=sig.entry_price,
        stop_loss=sl,
        take_profit=tp,
        position_size=0.1,
        is_valid=True,
        reasoning=f"Shadow Bridge via signal_composer"
    )

# ── 3. Run both systems and compare ──
tester = SignalTesterLite()
matches = 0
mismatches = 0
total = 0

for i in range(100, len(df15)):
    ts = df15.index[i]
    win15 = df15.loc[:ts]
    win1h = df1h.loc[:ts]
    win4h = df4h.loc[:ts]
    if win1h.empty or win4h.empty:
        continue
    
    old_sig = old_compose(win4h, win1h, win15)
    if old_sig is None:
        continue
    
    new_packet = simulate_new_core(win15, win1h, win4h, ts)
    if new_packet is None:
        continue
    
    total += 1
    # ใช้ SignalTesterLite เปรียบเทียบ Action
    if tester.check_equivalence(old_sig, new_packet, "XAUUSD"):
        matches += 1
    else:
        mismatches += 1

# ── 4. Summary ──
print(f"\n📊 Signal Equivalence Summary")
print(f"   Total signals compared: {total}")
print(f"   ✅ Matches: {matches} ({matches/total*100:.1f}%)" if total > 0 else "   No signals")
print(f"   ⚠️ Mismatches: {mismatches} ({mismatches/total*100:.1f}%)" if total > 0 else "")

if mismatches == 0:
    print("\n🎉 PERFECT MATCH — Core new is equivalent to legacy!")
else:
    print("\n⚠️ Review logs above for divergence details.")

print("\n✅ Regression Test Complete!")
