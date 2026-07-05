#!/usr/bin/env python3
"""
Backtest Fallback Signal Composer (clean gates, correct OHLC resample)
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd
from collections import defaultdict
from datetime import timezone

from data_provider_twelvedata import fetch_twelvedata
from session_clock import SessionClock
from score_manager_v5p3 import ScoreManager, THRESHOLD_V4
from kivanc_vsaob import run_kivanc

clock = SessionClock()
score_mgr = ScoreManager()

def generate_signals(df_15m):
    # Ensure index tz-aware UTC
    if df_15m.index.tz is None:
        df_15m = df_15m.tz_localize('UTC')
    trades = []
    for i in range(100, len(df_15m)-1):
        row = df_15m.iloc[i]
        ts = row.name  # tz-aware UTC
        session_state = clock.get(ts)
        session = session_state.session
        if session == 'CLOSED':
            continue

        # Build 1h data with complete OHLC (and volume if exists)
        agg_dict = {'open':'first','high':'max','low':'min','close':'last'}
        if 'volume' in df_15m.columns:
            agg_dict['volume'] = 'sum'
        df_1h = df_15m.loc[:ts].resample('1h').agg(agg_dict).dropna()
        if len(df_1h) < 2:
            continue
        # Forward fill any missing (should not happen after dropna)
        df_1h = df_1h.ffill()

        # Ensure tz-aware
        if df_1h.index.tz is None:
            df_1h = df_1h.tz_localize('UTC')

        # Run Kivanc
        kivanc_sig = run_kivanc(df_1h)
        kivanc_score = 1 if kivanc_sig else 0
        bos_detected = False
        vsa_ok = False

        score_result = score_mgr.calculate(
            kivanc_score=kivanc_score,
            bos_detected=bos_detected,
            vsa_ok=vsa_ok
        )
        if not score_result.is_tradable:
            continue

        # Direction
        direction = None
        if kivanc_sig and kivanc_sig['direction'] in ('BUY','SELL'):
            direction = kivanc_sig['direction']
        else:
            if row['close'] > row['EMA20']:
                direction = 'BUY'
            elif row['close'] < row['EMA20']:
                direction = 'SELL'
            else:
                continue

        entry = row['close']
        sl = entry - 2.0 if direction == 'BUY' else entry + 2.0
        tp = entry + 3.0 if direction == 'BUY' else entry - 3.0
        trades.append({'time': ts, 'session': session, 'dir': direction,
                       'entry': entry, 'sl': sl, 'tp': tp})
    return trades

print("Loading data...")
df = fetch_twelvedata('XAU/USD', '15min', 90)
cutoff = df.index.max() - pd.Timedelta(days=60)
df = df[df.index >= cutoff]

df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
df = df.dropna()
print(f"Data: {len(df)} bars")
print("Columns:", df.columns.tolist())

trades = generate_signals(df)
print(f"Signals: {len(trades)}")

cnt = {}
for t in trades:
    sess = t['session']
    cnt[sess] = cnt.get(sess, 0) + 1
print("\nTrade count by session:")
for k,v in cnt.items():
    print(f"  {k}: {v}")
print(f"Total: {sum(cnt.values())}")
