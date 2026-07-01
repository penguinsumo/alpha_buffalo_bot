#!/usr/bin/env python3
"""
เปรียบเทียบ Original v11.2 vs New V4 (Permission Table + Golden Zone + Sweep)
ข้อมูลจริง: Twelve Data XAU/USD 15m (60 วัน) หรือ yfinance GC=F fallback
"""
import requests, pandas as pd, numpy as np, os, warnings, sys
from datetime import datetime, timedelta
from collections import defaultdict
warnings.filterwarnings('ignore')

# ── 1. โหลดข้อมูลจริง ────────────────────────────
env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end_date = datetime(2026, 6, 17)
start_date = end_date - timedelta(days=60)

def load_twelvedata(symbol='XAU/USD', interval='15min'):
    url = "https://api.twelvedata.com/time_series"
    params = {'symbol': symbol, 'interval': interval,
              'start_date': start_date.strftime('%Y-%m-%d'),
              'end_date': end_date.strftime('%Y-%m-%d'),
              'outputsize': 5000, 'apikey': API_KEY}
    r = requests.get(url, params=params)
    data = r.json()
    if 'values' not in data:
        raise RuntimeError(data.get('message','API error'))
    df = pd.DataFrame(data['values'])
    df['datetime'] = pd.to_datetime(df['datetime'])
    df = df.set_index('datetime').sort_index()
    for col in ['open','high','low','close']:
        df[col] = pd.to_numeric(df[col])
    df['volume'] = 0  # Twelve Data free tier no volume
    return df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})

print("📡 Loading real data (Twelve Data XAU/USD 15m)...")
try:
    df = load_twelvedata()
    print(f"✅ Twelve Data: {len(df)} candles")
except Exception as e:
    print(f"⚠️ Twelve Data failed ({e}), trying yfinance GC=F...")
    import yfinance as yf
    end = datetime.now()
    start = end - timedelta(days=60)
    ticker = "GC=F"
    df = yf.download(ticker, start=start, end=end, interval="15m")
    if df.empty:
        print("❌ No data")
        sys.exit(1)
    df = df.rename(columns={'Open':'Open','High':'High','Low':'Low','Close':'Close'})
    df['Volume'] = df['Volume'] if 'Volume' in df.columns else 0
    df = df[['Open','High','Low','Close','Volume']]
    print(f"✅ yfinance GC=F: {len(df)} candles")

# ── 2. Indicators ──────────────────────────────────
df = df.copy()
df['BB_Mid'] = df['Close'].rolling(20).mean()
df['BB_Std'] = df['Close'].rolling(20).std()
df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
h,l,c = df['High'], df['Low'], df['Close'].shift(1)
tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
df['ATR14'] = tr.rolling(14).mean()
df['EMA20'] = df['Close'].ewm(span=20).mean()
df['EMA50'] = df['Close'].ewm(span=50).mean()
df['Low_Prev'] = df['Low'].shift(1); df['High_Prev'] = df['High'].shift(1)
df['Bull_Sweep'] = (df['Low'] < df['Low_Prev']) & (df['Close'] > df['Low_Prev'])
df['Bear_Sweep'] = (df['High'] > df['High_Prev']) & (df['Close'] < df['High_Prev'])
# Swing for Golden Zone (1H resample)
df1h = df.resample('1h').agg({'High':'max','Low':'min'}).dropna()
if len(df1h) >= 5:
    sw_high = df1h['High'].rolling(5).max()
    sw_low = df1h['Low'].rolling(5).min()
    sw_high = sw_high.reindex(df.index, method='ffill')
    sw_low = sw_low.reindex(df.index, method='ffill')
else:
    sw_high = df['High'].rolling(100).max()
    sw_low = df['Low'].rolling(100).min()
df['Swing_H'] = sw_high; df['Swing_L'] = sw_low
df['Diff'] = df['Swing_H'] - df['Swing_L']
df = df.dropna()

def get_session(hour):
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    else: return 'CLOSED'

# ── 3. Original v11.2 Logic (no golden zone, simple entry, trailing exit) ──
def original_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; h = row.name.hour
        if not (12 <= h <= 22): continue  # v11.2 session
        direction = None
        if row['EMA20'] > row['EMA50']:
            if row['Low'] <= row['BB_Lower'] * 1.02:
                direction = 'BUY'; entry = row['Close']
        elif row['EMA20'] < row['EMA50']:
            if row['High'] >= row['BB_Upper'] * 0.98:
                direction = 'SELL'; entry = row['Close']
        if direction is None: continue
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        be_act=False; hi=lo=entry; exit_price=entry
        for j in range(i+1, min(i+40, len(df))):
            r = df.iloc[j]; hl, ll = r['High'], r['Low']
            if direction=='BUY':
                if hl>hi: hi=hl
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
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
        trades.append({'session':get_session(h), 'pnl':pnl})
    return trades

# ── 4. New V4 Logic (Permission Table + Golden Zone + Sweep) ──
SESSION_HOURS = {
    'ASIA':   {'BUY': [1],           'SELL': [3, 5]},
    'LONDON': {'BUY': [],            'SELL': [8, 9, 12]},
    'NY':     {'BUY': [13, 15, 16, 17], 'SELL': [13, 14, 15, 16, 17, 18]}
}
def newv4_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; h = row.name.hour
        session = get_session(h)
        if session == 'CLOSED': continue
        # BUY (Golden Zone)
        if row['EMA20'] > row['EMA50'] and row['Diff'] > 0:
            gl = row['Swing_H'] - row['Diff']*1.0; gh = row['Swing_H'] - row['Diff']*0.5
            if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                if h in SESSION_HOURS.get(session, {}).get('BUY', []):
                    entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
                    be_act=False; highest=entry; exit_price=entry
                    for j in range(i+1, min(i+40, len(df))):
                        r = df.iloc[j]; hl, ll = r['High'], r['Low']
                        if hl>highest: highest=hl
                        if not be_act and highest >= entry*1.0015: be_act=True; sl=entry
                        if be_act: sl = max(sl, highest*0.9995)
                        if hl>=tp: exit_price=tp; break
                        if ll<=sl: exit_price=sl; break
                    else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
                    pnl = (exit_price-entry)/entry*100
                    trades.append({'session':session, 'pnl':pnl})
        # SELL (Golden Zone? no, original entry but with permission)
        if row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98:
            if h in SESSION_HOURS.get(session, {}).get('SELL', []):
                entry = row['Close']; sl = entry + row['ATR14']*1.5; tp = row['BB_Lower']
                be_act=False; lowest=entry; exit_price=entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; hl, ll = r['High'], r['Low']
                    if ll<lowest: lowest=ll
                    if not be_act and lowest <= entry*0.9990: be_act=True; sl=entry
                    if be_act: sl = min(sl, lowest*1.0005)
                    if ll<=tp: exit_price=tp; break
                    if hl>=sl: exit_price=sl; break
                else: exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
                pnl = (entry-exit_price)/entry*100
                trades.append({'session':session, 'pnl':pnl})
    return trades

def stats(trades):
    if not trades: return {'trades':0,'wr':0,'pnl':0,'dd':0}
    pnls = [t['pnl'] for t in trades]
    total = len(pnls); wins = [p for p in pnls if p>0]
    wr = len(wins)/total*100
    pnl = sum(pnls)
    cum=0; peak=0; dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':total, 'wr':wr, 'pnl':pnl, 'dd':dd}

print("\n🔄 Running original v11.2...")
trades_orig = original_trades(df)
print("🔄 Running New V4...")
trades_new = newv4_trades(df)

s_orig = stats(trades_orig)
s_new = stats(trades_new)

print("\n📊 OVERALL COMPARISON (Real Data 60d)")
print(f"{'Metric':<20} {'v11.2':<15} {'New V4':<15}")
print(f"{'Trades':<20} {s_orig['trades']:<15} {s_new['trades']:<15}")
print(f"{'Win Rate':<20} {s_orig['wr']:.1f}%{'':<10} {s_new['wr']:.1f}%")
print(f"{'Total PnL %':<20} {s_orig['pnl']:+.2f}%{'':<10} {s_new['pnl']:+.2f}%")
print(f"{'Max DD %':<20} -{s_orig['dd']:.2f}%{'':<10} -{s_new['dd']:.2f}%")

# Per session breakdown
for ses in ['ASIA','LONDON','NY']:
    o_ses = stats([t for t in trades_orig if t['session']==ses])
    n_ses = stats([t for t in trades_new if t['session']==ses])
    print(f"\n--- {ses} ---")
    print(f"  v11.2: Trades:{o_ses['trades']} WR:{o_ses['wr']:.1f}% PnL:{o_ses['pnl']:+.2f}% DD:{o_ses['dd']:.2f}%")
    print(f"  NewV4: Trades:{n_ses['trades']} WR:{n_ses['wr']:.1f}% PnL:{n_ses['pnl']:+.2f}% DD:{n_ses['dd']:.2f}%")
