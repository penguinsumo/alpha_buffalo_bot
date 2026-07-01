#!/usr/bin/env python3
"""เปรียบเทียบ 3 Exit สำหรับ LONDON Sell: Visual SL, Trailing, Visual TP"""
import requests, pandas as pd, numpy as np, os, warnings
from datetime import datetime, timedelta
from collections import defaultdict
warnings.filterwarnings('ignore')

# ── 1. โหลดข้อมูล Twelve Data 15m (60 วัน) ──
env_path = os.path.expanduser('~/alpha_buffalo_bot/.env')
API_KEY = None
with open(env_path) as f:
    for line in f:
        if line.startswith('TWELVEDATA_API_KEY='):
            API_KEY = line.strip().split('=', 1)[1]
            break

end_date = datetime(2026, 6, 17)
start_date = end_date - timedelta(days=60)
url = "https://api.twelvedata.com/time_series"
params = {
    'symbol': 'XAU/USD', 'interval': '15min',
    'start_date': start_date.strftime('%Y-%m-%d'),
    'end_date': end_date.strftime('%Y-%m-%d'),
    'outputsize': 5000, 'apikey': API_KEY
}
r = requests.get(url, params=params)
data = r.json()
if 'values' not in data:
    print(f"❌ API error: {data.get('message', data)}")
    exit()
df = pd.DataFrame(data['values'])
df['datetime'] = pd.to_datetime(df['datetime'])
df = df.set_index('datetime').sort_index()
for col in ['open','high','low','close']:
    df[col] = pd.to_numeric(df[col])
df = df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close'})
print(f"✅ Twelve Data 15m: {len(df)} candles")

# ── 2. Indicators (รวมถึง Swing/Fibo สำหรับ Visual TP) ──
def add_indicators(df):
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
    # 1H Swing for Golden Zone & Visual TP
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
    # Visual TP levels (Fibonacci จาก Swing)
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    df['Fib_086'] = df['Swing_H'] - df['Diff'] * 0.86
    # PRZ next (simplified: ถ้ามี Harmonic PRZ จะใช้ แต่ที่นี่ใช้ 1.0 extension เป็นเป้าหมาย)
    df['PRZ_Next'] = df['Swing_L']  # simplest: 1.0 extension
    return df

df_15m = add_indicators(df).dropna()

def get_session(ts):
    hour = ts.hour
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    return 'OTHER'

# ── 3. Trade Logic แยกตามวิธีการ Exit ──
def generate_trades(df, exit_method='VisualSL'):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if not (1 <= ts.hour < 19): continue
        # BUY (เหมือนเดิมทุกวิธี)
        if (row['EMA20'] > row['EMA50'] and row['Diff'] > 0):
            gl = row['Swing_H'] - row['Diff']*1.0
            gh = row['Swing_H'] - row['Diff']*0.5
            if gl <= row['Close'] <= gh and row['Bull_Sweep'] and row['Low'] <= row['BB_Lower']*1.02:
                entry = row['Close']; sl = entry - row['ATR14']*1.5; tp = row['BB_Upper']
                be_act=False; highest=entry; exit_price=entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; h, l = r['High'], r['Low']
                    if h>highest: highest=h
                    if not be_act and highest >= entry * 1.0015:
                        be_act=True; sl=entry
                    if be_act: sl = max(sl, highest*0.9995)
                    if h >= tp: exit_price=tp; break
                    if l <= sl: exit_price=sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
                trades.append({'session': get_session(ts), 'dir':'BUY', 'entry':entry, 'exit':exit_price, 'sl':sl, 'time':ts})
        # SELL
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and row['High'] >= row['BB_Upper']*0.98):
            entry = row['Close']; sl = entry + row['ATR14']*1.5; exit_price=entry
            if exit_method == 'VisualSL':
                # Original: Visual SL + TP Lower BB
                mid_crossed=False
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; h, l = r['High'], r['Low']
                    if not mid_crossed and l <= r['BB_Mid']:
                        mid_crossed=True; sl=entry
                    if l <= r['BB_Lower']: exit_price=r['BB_Lower']; break
                    if h >= sl: exit_price=sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            elif exit_method == 'Trailing':
                # Trailing Sell + BE 0.10%
                lowest=entry; be_act=False
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; h, l = r['High'], r['Low']
                    if l < lowest: lowest = l
                    if not be_act and lowest <= entry * 0.9990:
                        be_act=True; sl=entry
                    if be_act:
                        sl = min(sl, lowest * 1.0005)
                    if l <= r['BB_Lower']: exit_price=r['BB_Lower']; break
                    if h >= sl: exit_price=sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            else:  # VisualTP
                # Visual SL สำหรับ SL, TP จาก Fibonacci levels
                mid_crossed=False
                # กำหนด TP จาก Fibo 0.72 หรือ PRZ ใกล้สุด
                tp1 = row['Fib_072']
                tp2 = row['PRZ_Next']
                # เลือก TP ที่ใกล้ที่สุดและอยู่ต่ำกว่า entry
                tp = tp1 if tp1 < entry else tp2
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; h, l = r['High'], r['Low']
                    if not mid_crossed and l <= r['BB_Mid']:
                        mid_crossed=True; sl=entry
                    if l <= tp: exit_price=tp; break  # TP ตาม Visual TP
                    if h >= sl: exit_price=sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['Close']
            trades.append({'session': get_session(ts), 'dir':'SELL', 'entry':entry, 'exit':exit_price, 'sl':sl, 'time':ts})
    return trades

# ── 4. Simulation per session (เหมือนเดิม) ──
def simulate_per_session(trades, initial=10000):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades': [], 'curve': [initial], 'equity': initial,
                                    'daily_eq_start': initial, 'current_day': None,
                                    'consec_loss': 0, 'stop_day': False, 'stopped': 0})
    for t in trades:
        sess = t['session']
        sd = sessions[sess]
        trade_day = t['time'].date()
        if trade_day != sd['current_day']:
            sd['current_day'] = trade_day
            sd['daily_eq_start'] = sd['equity']
            sd['consec_loss'] = 0
            sd['stop_day'] = False
        if sd['stop_day']: continue
        sl_dist = abs(t['entry'] - t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        risk_amount = sd['equity'] * 0.01
        contracts = risk_amount / (sl_dist * 10)
        contracts = min(contracts, 10.0); contracts = max(contracts, 0.01)
        pnl_pts = (t['exit'] - t['entry']) if t['dir']=='BUY' else (t['entry'] - t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        sd['equity'] += pnl_dollar
        if pnl_dollar <= 0: sd['consec_loss'] += 1
        else: sd['consec_loss'] = 0
        daily_dd = (sd['daily_eq_start'] - sd['equity']) / sd['daily_eq_start']
        if daily_dd >= 0.03 or sd['consec_loss'] >= 5:
            sd['stop_day'] = True; sd['stopped'] += 1
        if sd['equity'] <= 0: sd['equity']=0; sd['curve'].append(0); break
        sd['curve'].append(sd['equity'])
        sd['trades'].append({**t, 'pnl_$': pnl_dollar})
    stats = {}
    for sess, sd in sessions.items():
        curve = sd['curve']; final_eq = curve[-1]
        ret = (final_eq / initial - 1) * 100 if initial > 0 else 0
        peak = initial; max_dd = 0
        for eq in curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd: max_dd = dd
        trades_sess = sd['trades']
        wins = [x for x in trades_sess if x['pnl_$'] > 0]
        total = len(trades_sess)
        wr = len(wins)/total*100 if total else 0
        gross_profit = sum(x['pnl_$'] for x in wins)
        gross_loss = abs(sum(x['pnl_$'] for x in trades_sess if x['pnl_$'] < 0))
        pf = gross_profit/gross_loss if gross_loss > 0 else float('inf')
        stats[sess] = {'trades': total, 'wr': wr, 'return': ret, 'dd': max_dd,
                       'pf': pf, 'stopped': sd['stopped'], 'final_eq': final_eq}
    return stats

# ── 5. Run all three methods ──
print("\n🔄 Testing Visual SL...")
trades_VSL = generate_trades(df_15m, 'VisualSL')
stats_VSL = simulate_per_session(trades_VSL)

print("🔄 Testing Trailing...")
trades_TR = generate_trades(df_15m, 'Trailing')
stats_TR = simulate_per_session(trades_TR)

print("🔄 Testing Visual TP...")
trades_VTP = generate_trades(df_15m, 'VisualTP')
stats_VTP = simulate_per_session(trades_VTP)

# ── 6. Print London comparison ──
print("\n📊 LONDON SELL EXIT COMPARISON (Twelve Data 15m, 60d)")
print("="*90)
print(f"{'Metric':<20} {'Visual SL':<22} {'Trailing':<22} {'Visual TP':<22}")
print(f"{'Trades':<20} {stats_VSL['LONDON']['trades']:<22} {stats_TR['LONDON']['trades']:<22} {stats_VTP['LONDON']['trades']:<22}")
print(f"{'Win Rate':<20} {stats_VSL['LONDON']['wr']:.2f}%{'':<16} {stats_TR['LONDON']['wr']:.2f}%{'':<16} {stats_VTP['LONDON']['wr']:.2f}%")
print(f"{'Return':<20} {stats_VSL['LONDON']['return']:.2f}%{'':<16} {stats_TR['LONDON']['return']:.2f}%{'':<16} {stats_VTP['LONDON']['return']:.2f}%")
print(f"{'Max DD':<20} {stats_VSL['LONDON']['dd']:.2f}%{'':<16} {stats_TR['LONDON']['dd']:.2f}%{'':<16} {stats_VTP['LONDON']['dd']:.2f}%")
print(f"{'Profit Factor':<20} {stats_VSL['LONDON']['pf']:.2f}{'':<16} {stats_TR['LONDON']['pf']:.2f}{'':<16} {stats_VTP['LONDON']['pf']:.2f}")
print(f"{'Days Stopped':<20} {stats_VSL['LONDON']['stopped']:<22} {stats_TR['LONDON']['stopped']:<22} {stats_VTP['LONDON']['stopped']:<22}")
print("="*90)
