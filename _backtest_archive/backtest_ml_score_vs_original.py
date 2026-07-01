#!/usr/bin/env python3
"""Compare Old ScoreManager vs New MLScoreManager (rule‑based) on same 60‑day data"""
import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
from collections import defaultdict
warnings.filterwarnings('ignore')

# ── Load Twelve Data (same as before) ──
env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end_date = datetime(2026, 6, 17)
start_date = end_date - timedelta(days=60)

def fetch(symbol='XAU/USD', interval='15min'):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol': symbol, 'interval': interval,
              'start_date': start_date.strftime('%Y-%m-%d'),
              'end_date': end_date.strftime('%Y-%m-%d'),
              'outputsize': 5000, 'apikey': API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        raise RuntimeError(data.get('message', 'API error'))
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open', 'high', 'low', 'close']:
        df[col] = pd.to_numeric(df[col])
    df['volume'] = 0
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

df_15m = fetch('XAU/USD', '15min')
df_1h  = fetch('XAU/USD', '1h')

# ── Indicators ──
df = df_15m.copy()
df['BB_Mid'] = df['Close'].rolling(20).mean()
df['BB_Std'] = df['Close'].rolling(20).std()
df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
h,l,c = df['High'], df['Low'], df['Close'].shift(1)
tr = pd.concat([h-l, (h-c).abs(), (l-c).abs()], axis=1).max(axis=1)
df['ATR14'] = tr.rolling(14).mean()
df['EMA20'] = df['Close'].ewm(span=20).mean()
df['EMA50'] = df['Close'].ewm(span=50).mean()
df['Low_Prev'] = df['Low'].shift(1); df['High_Prev'] = df['High'].shift(1)
df['Bull_Sweep'] = (df['Low'] < df['Low_Prev']) & (df['Close'] > df['Low_Prev'])
df['Bear_Sweep'] = (df['High'] > df['High_Prev']) & (df['Close'] < df['High_Prev'])

# Swing for Golden Zone
df1h = df_1h.resample('1h').agg({'High':'max','Low':'min'}).dropna()
if len(df1h) >= 5:
    sw_high = df1h['High'].rolling(5).max().reindex(df.index, method='ffill')
    sw_low  = df1h['Low'].rolling(5).min().reindex(df.index, method='ffill')
else:
    sw_high = df['High'].rolling(100).max()
    sw_low  = df['Low'].rolling(100).min()
df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
df['Diff'] = df['Swing_H'] - df['Swing_L']
df = df.dropna()

# ── Session helpers ──
def get_session(hour):
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    else: return 'CLOSED'

SESSION_HOURS = {
    'ASIA':   {'BUY': [1],           'SELL': [3, 5]},
    'LONDON': {'BUY': [],            'SELL': [8, 9, 12]},
    'NY':     {'BUY': [13, 15, 16, 17], 'SELL': [13, 14, 15, 16, 17, 18]}
}
SESSION_V4_THRESHOLD = {'ASIA': 3, 'LONDON': 4, 'NY': 4}

# ── Old ScoreManager ──
from score_manager_v5p3 import ScoreManager
old_mgr = ScoreManager()

def old_score(kivanc_score, bos_detected, vsa_ok):
    res = old_mgr.calculate(kivanc_score=kivanc_score, bos_detected=bos_detected, vsa_ok=vsa_ok)
    return float(res.total)

# ── New MLScoreManager ──
from score_ml import MLScoreManager, ScoreFeatures
new_mgr = MLScoreManager()

def new_score(kivanc_score, bos_detected, vsa_ok, session):
    feats = ScoreFeatures(
        kivanc_score=float(kivanc_score),
        bos_detected=float(bos_detected),
        vsa_ok=float(vsa_ok),
        trend_strength=0.5, momentum=0.3, volatility=0.2,
        session_score=1.0 if session != "CLOSED" else 0.0
    )
    return new_mgr.predict(feats)

# ── Backtest Engine ──
def run_backtest(score_func, use_old=True):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name; h = ts.hour
        session = get_session(h)
        if session == 'CLOSED': continue
        direction = None
        # BUY conditions
        if row['EMA20'] > row['EMA50'] and row['Diff'] > 0:
            gl = row['Swing_H'] - row['Diff']*1.0
            gh = row['Swing_H'] - row['Diff']*0.5
            if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                if h in SESSION_HOURS.get(session, {}).get('BUY', []):
                    direction = 'BUY'
        # SELL conditions
        if row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98:
            if h in SESSION_HOURS.get(session, {}).get('SELL', []):
                direction = 'SELL'
        if direction is None: continue

        # Score
        kivanc_score = 1  # placeholder
        bos_detected = False  # placeholder (no micro engine here)
        vsa_ok = False
        if use_old:
            base_score = score_func(kivanc_score, bos_detected, vsa_ok)
        else:
            base_score = score_func(kivanc_score, bos_detected, vsa_ok, session)
        thresh = SESSION_V4_THRESHOLD.get(session, 4)
        if base_score < thresh: continue

        # Entry & Exit (simplified – same as before)
        entry = row['Close']
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act=False; hi=lo=entry; exit_price=entry
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hl, ll = r['High'], r['Low']
            if direction=='BUY':
                if hl>hi: hi=hl
                if not be_act and hi>=entry*1.0015: be_act=True; sl=entry
                if be_act: sl = max(sl, hi*0.9995)
                if hl>=tp: exit_price=tp; break
                if ll<=sl: exit_price=sl; break
            else:
                if ll<lo: lo=ll
                if not be_act and lo<=entry*0.9990: be_act=True; sl=entry
                if be_act: sl = min(sl, lo*1.0005)
                if ll<=tp: exit_price=tp; break
                if hl>=sl: exit_price=sl; break
        else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
        pnl = (exit_price-entry)/entry*100 if direction=='BUY' else (entry-exit_price)/entry*100
        trades.append(pnl)
    return trades

# ── Run both ──
print("🔄 Running Old ScoreManager...")
trades_old = run_backtest(old_score, use_old=True)
print("🔄 Running New MLScoreManager (rule‑based)...")
trades_new = run_backtest(new_score, use_old=False)

def stats(trades):
    if not trades: return {'trades':0, 'wr':0, 'pnl':0, 'dd':0}
    wins = [t for t in trades if t > 0]
    wr = len(wins)/len(trades)*100
    pnl = sum(trades)
    cum=0; peak=0; dd=0
    for t in trades:
        cum+=t
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':len(trades), 'wr':wr, 'pnl':pnl, 'dd':dd}

s_old = stats(trades_old)
s_new = stats(trades_new)

print("\n📊 Comparison")
print(f"{'Metric':<20} {'Old ScoreManager':<20} {'New MLScoreManager':<20}")
print(f"{'Trades':<20} {s_old['trades']:<20} {s_new['trades']:<20}")
print(f"{'Win Rate':<20} {s_old['wr']:.1f}%{'':<14} {s_new['wr']:.1f}%")
print(f"{'PnL %':<20} {s_old['pnl']:+.2f}%{'':<14} {s_new['pnl']:+.2f}%")
print(f"{'Max DD %':<20} -{s_old['dd']:.2f}%{'':<14} -{s_new['dd']:.2f}%")
