#!/usr/bin/env python3
"""
v11.2 mod (Visual SL) on Twelve Data 1H, then find Best 3Hr windows per Session
"""
import pandas as pd, numpy as np
from datetime import datetime, timedelta

# ── 1. Load Twelve Data 1H ──────────────────────
print("📡 Loading Twelve Data 1H...")
try:
    from data_provider_twelvedata import fetch_twelvedata
    df_15m, df_1h, df_4h = fetch_twelvedata()
    if df_1h is not None and len(df_1h) > 10:
        cutoff = df_1h.index.max() - pd.Timedelta(days=60)
        df_1h = df_1h[df_1h.index >= cutoff]
        print(f"✅ Twelve Data 1H: {len(df_1h)} candles")
    else:
        raise ValueError("Not enough 1H data")
except Exception as e:
    print(f"⚠️ Twelve Data failed ({e}), using GC=F resampled to 1H...")
    import yfinance as yf
    end = datetime.now(); start = end - timedelta(days=60)
    df = yf.download("GC=F", start=start, end=end, interval="15m")
    if isinstance(df.columns, pd.MultiIndex): df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if not isinstance(df.index, pd.DatetimeIndex): df.index = pd.to_datetime(df.index)
    for c in ['open','high','low','close','volume']:
        if c in df.columns: df[c] = pd.to_numeric(df[c], errors='coerce')
    df = df[['open','high','low','close','volume']].dropna()
    df_1h = df.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last','volume':'sum'}).dropna()
    print(f"✅ GC=F resampled to 1H: {len(df_1h)} candles")

# ── 2. Indicators ────────────────────────────────
def add_indicators(df):
    df = df.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    h,l,c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()
    df['Low_Prev'] = df['low'].shift(1); df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    return df

# ── 3. Session helper ────────────────────────────
def get_session(ts):
    h = ts.hour
    if 1 <= h < 8: return 'ASIA'
    elif 8 <= h < 13: return 'LONDON'
    elif 13 <= h < 19: return 'NY'
    else: return 'ASIA_LOW'

# ── 4. v11.2 mod trades (no golden zone, visual SL for sell) ──
def generate_trades(df):
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        direction = entry = sl = tp = None
        if row['EMA20'] > row['EMA50']:
            if row['low'] <= row['BB_Lower'] * 1.02: direction='BUY'; entry=row['close']
        elif row['EMA20'] < row['EMA50']:
            if row['high'] >= row['BB_Upper'] * 0.98: direction='SELL'; entry=row['close']
        if direction is None: continue
        sl = (entry - row['ATR14']*1.5) if direction=='BUY' else (entry + row['ATR14']*1.5)
        tp = row['BB_Upper'] if direction=='BUY' else row['BB_Lower']
        exit_price = entry
        if direction == 'BUY':
            be_act=False; hi=entry
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if h>hi: hi=h
                if not be_act and hi>=entry*1.0010: be_act=True; sl=entry
                if be_act: sl=max(sl, hi*0.9995)
                if h>=tp: exit_price=tp; break
                if l<=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        else: # SELL Visual SL
            mid_crossed=False
            for j in range(i+1, min(i+40, len(df))):
                r=df.iloc[j]; h,l=r['high'],r['low']
                if not mid_crossed and l<=r['BB_Mid']: mid_crossed=True; sl=entry
                if l<=r['BB_Lower']: exit_price=r['BB_Lower']; break
                if h>=sl: exit_price=sl; break
            else: exit_price=df.iloc[min(i+40-1, len(df)-1)]['close']
        pnl_pts = (exit_price - entry) if direction=='BUY' else (entry - exit_price)
        trades.append({'dir':direction, 'pnl_pts':pnl_pts, 'time':ts, 'session':get_session(ts)})
    return trades

# ── 5. Analyse Best 3Hr per Session ─────────────
def best_3hr_windows(trades):
    # ใช้ trades list ที่มี timestamp
    sessions = ['ASIA','LONDON','NY','ASIA_LOW']
    results = []
    for ses in sessions:
        ses_trades = [t for t in trades if t['session']==ses]
        if len(ses_trades) < 3: continue
        # Sliding window 3 hours (3 candles in 1H)
        # ใช้เวลา UTC
        ses_df = pd.DataFrame(ses_trades)
        ses_df = ses_df.sort_values('time')
        # sliding window
        best_pnl = -np.inf
        best_window = None
        for i in range(len(ses_df)-2):
            window = ses_df.iloc[i:i+3]
            pnl = window['pnl_pts'].sum()
            if pnl > best_pnl:
                best_pnl = pnl
                best_window = (window['time'].iloc[0], window['time'].iloc[-1])
        # Buy/Sell breakdown for this best window
        buy_pnl = ses_df[(ses_df['dir']=='BUY') & (ses_df['time']>=best_window[0]) & (ses_df['time']<=best_window[1])]['pnl_pts'].sum()
        sell_pnl = ses_df[(ses_df['dir']=='SELL') & (ses_df['time']>=best_window[0]) & (ses_df['time']<=best_window[1])]['pnl_pts'].sum()
        results.append({
            'session': ses,
            'best_window': f"{best_window[0].strftime('%H:%M')}-{best_window[1].strftime('%H:%M')}",
            'total_pnl_pts': round(best_pnl, 2),
            'buy_pnl': round(buy_pnl,2),
            'sell_pnl': round(sell_pnl,2),
            'total_trades_in_window': len(ses_df[(ses_df['time']>=best_window[0]) & (ses_df['time']<=best_window[1])])
        })
    return results

# ── 6. Main ──────────────────────────────────────
df_1h = add_indicators(df_1h).dropna()
trades = generate_trades(df_1h)
print(f"Total trades: {len(trades)}")

# Overall stats
buy_trades = [t for t in trades if t['dir']=='BUY']
sell_trades = [t for t in trades if t['dir']=='SELL']
def stats(tr):
    if not tr: return {}
    pnls = [t['pnl_pts'] for t in tr]
    return {'total':len(tr), 'wr':sum(1 for p in pnls if p>0)/len(tr)*100, 'pnl':sum(pnls)}
s_buy = stats(buy_trades); s_sell = stats(sell_trades); s_all = stats(trades)

print("\n📊 OVERALL")
print(f"Buy: {s_buy['total']} trades, WR={s_buy['wr']:.1f}%, PnL={s_buy['pnl']:+.1f} pts")
print(f"Sell: {s_sell['total']} trades, WR={s_sell['wr']:.1f}%, PnL={s_sell['pnl']:+.1f} pts")
print(f"Total: {s_all['total']} trades, WR={s_all['wr']:.1f}%, PnL={s_all['pnl']:+.1f} pts")

# Best 3Hr windows
windows = best_3hr_windows(trades)
print("\n📊 BEST 3HR WINDOWS PER SESSION (PnL in points)")
print("="*70)
for w in windows:
    print(f"{w['session']:<10} {w['best_window']:<15} PnL={w['total_pnl_pts']:+.1f} | Buy={w['buy_pnl']:+.1f} Sell={w['sell_pnl']:+.1f} | Trades={w['total_trades_in_window']}")
