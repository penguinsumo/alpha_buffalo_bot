import pandas as pd
import numpy as np

# ─── 1. โหลด CSV (ไม่มีหัววันที่) ───────────────────
df = pd.read_csv('data/XAUUSD_H1.csv')
print("Columns:", df.columns.tolist())
print("Rows:", len(df))

# CSV มีคอลัมน์: ,open,high,low,close,volume (Unnamed: 0 เป็น index)
# เราใช้คอลัมน์ open/high/low/close โดยตรง
df = df[['open','high','low','close']].rename(columns=str.lower)
# สร้าง datetime index เริ่ม 2026-01-01 00:00, ความถี่ 1H
start_date = pd.Timestamp('2026-01-01 00:00')
df.index = pd.date_range(start=start_date, periods=len(df), freq='1H')
df = df.sort_index()

print("Data from", df.index[0], "to", df.index[-1])

# ─── 2. กรองช่วง 2 มี.ค. 2026 - 15 มิ.ย. 2026 ──────
START = '2026-03-02'
END   = '2026-06-15'
df_period = df.loc[START:END].copy()
print(f"Filtered {START} to {END}: {len(df_period)} candles")
if len(df_period) == 0:
    print("❌ No data in this range. Check if data exists.")
    exit()

# ─── 3. Indicators ─────────────────────────────────
df_period['ema20'] = df_period['close'].ewm(span=20, min_periods=20).mean()
df_period['ema50'] = df_period['close'].ewm(span=50, min_periods=50).mean()
df_period['atr'] = (df_period['high'] - df_period['low']).rolling(14).mean()

# Frozen pivot
df_period['sw_h'] = np.nan
df_period['sw_l'] = np.nan
sh = np.nan; sl = np.nan
for i in range(5, len(df_period)-5):
    if df_period['high'].iloc[i] == df_period['high'].iloc[i-5:i+6].max():
        sh = df_period['high'].iloc[i]
    if df_period['low'].iloc[i] == df_period['low'].iloc[i-5:i+6].min():
        sl = df_period['low'].iloc[i]
    df_period.at[df_period.index[i], 'sw_h'] = sh
    df_period.at[df_period.index[i], 'sw_l'] = sl

df_period['fib618'] = np.where(df_period['sw_h']>df_period['sw_l'], df_period['sw_h']-(df_period['sw_h']-df_period['sw_l'])*0.618, np.nan)
df_period['fib786'] = np.where(df_period['sw_h']>df_period['sw_l'], df_period['sw_h']-(df_period['sw_h']-df_period['sw_l'])*0.786, np.nan)
df_period['in_zone'] = (df_period['close'] >= df_period['fib786']) & (df_period['close'] <= df_period['fib618'])
df_period['v5_buy']  = df_period['in_zone'] & (df_period['ema20'] > df_period['ema50'])
df_period['v5_sell'] = df_period['in_zone'] & (df_period['ema20'] < df_period['ema50'])

# V4 BB(20,2)
df_period['bb_mid'] = df_period['close'].rolling(20).mean()
df_period['bb_std'] = df_period['close'].rolling(20).std()
df_period['bb_up']  = df_period['bb_mid'] + 2*df_period['bb_std']
df_period['bb_lo']  = df_period['bb_mid'] - 2*df_period['bb_std']
df_period['v4_buy']  = (df_period['low'] <= df_period['bb_lo']*1.02) & (df_period['ema20'] > df_period['ema50'])
df_period['v4_sell'] = (df_period['high'] >= df_period['bb_up']*0.98) & (df_period['ema20'] < df_period['ema50'])

# ─── 4. Backtest engine ────────────────────────────
def backtest(df, entry_buy, entry_sell, exit_type, is_v5_buy=None, is_v5_sell=None):
    trades = []
    pos = 0
    entry_price = sl = tp = 0
    be_act = False
    trail_high = trail_low = 0
    bar_entered = 0
    direction = None

    for i in range(50, len(df)-1):
        row = df.iloc[i]
        if pos == 0:
            if entry_buy.iloc[i] and not np.isnan(row['close']):
                pos = 1; direction = 'BUY'
                entry_price = row['close']
                atr = row['atr'] if not np.isnan(row['atr']) else 0
                if atr <= 0: continue
                sl = entry_price - atr*1.5
                tp = entry_price + atr*2.0
                be_act = False
                trail_high = row['high']
                bar_entered = i
            elif entry_sell.iloc[i] and not np.isnan(row['close']):
                pos = -1; direction = 'SELL'
                entry_price = row['close']
                atr = row['atr'] if not np.isnan(row['atr']) else 0
                if atr <= 0: continue
                sl = entry_price + atr*1.5
                tp = entry_price - atr*2.0
                be_act = False
                trail_low = row['low']
                bar_entered = i
        else:
            exit_px = None
            reason = None
            nxt = df.iloc[i+1]
            h, l, c = nxt['high'], nxt['low'], nxt['close']

            if exit_type == 'fixed':
                if pos == 1:
                    if h >= tp: exit_px, reason = tp, 'TP'
                    elif l <= sl: exit_px, reason = sl, 'SL'
                else:
                    if l <= tp: exit_px, reason = tp, 'TP'
                    elif h >= sl: exit_px, reason = sl, 'SL'
            elif exit_type == 'betrail':
                if pos == 1:
                    if not be_act and h >= entry_price * 1.001:
                        be_act = True; sl = entry_price
                    if be_act:
                        trail_high = max(trail_high, h)
                        sl = trail_high * 0.9995
                    if l <= sl: exit_px, reason = sl, 'Trail'
                else:
                    if not be_act and l <= entry_price * 0.999:
                        be_act = True; sl = entry_price
                    if be_act:
                        trail_low = min(trail_low, l)
                        sl = trail_low * 1.0005
                    if h >= sl: exit_px, reason = sl, 'Trail'
                if reason is None and (i - bar_entered) >= 24:
                    exit_px, reason = c, 'TIME'

            if reason is not None:
                pnl = (exit_px - entry_price) * pos / entry_price * 100
                if direction == 'BUY' and is_v5_buy is not None and is_v5_buy.iloc[bar_entered]:
                    etype = 'V5'
                elif direction == 'SELL' and is_v5_sell is not None and is_v5_sell.iloc[bar_entered]:
                    etype = 'V5'
                else:
                    etype = 'V4'
                trades.append({
                    'entry_time': df.index[bar_entered],
                    'exit_time': df.index[i+1],
                    'dir': direction, 'entry': entry_price, 'exit': exit_px,
                    'pnl%': pnl, 'bars': i+1 - bar_entered,
                    'reason': reason, 'entry_type': etype
                })
                pos = 0
    return pd.DataFrame(trades)

# ─── 5. Run comparison ─────────────────────────────
configs = [
    ("1. XAU FIX v1 (TP/SL)", df_period['v5_buy'], df_period['v5_sell'], 'fixed', df_period['v5_buy'], df_period['v5_sell']),
    ("2. v11.2 V5 only (BE+Trail)", df_period['v5_buy'], df_period['v5_sell'], 'betrail', df_period['v5_buy'], df_period['v5_sell']),
    ("3. v11.2 V4+V5 (BE+Trail)", df_period['v4_buy']|df_period['v5_buy'], df_period['v4_sell']|df_period['v5_sell'], 'betrail', df_period['v5_buy'], df_period['v5_sell']),
]

results = []
for name, buy_s, sell_s, ext, v5b, v5s in configs:
    tdf = backtest(df_period, buy_s, sell_s, ext, v5b, v5s)
    if tdf.empty:
        results.append({'Name': name, 'Trades': 0})
        continue
    wins = tdf[tdf['pnl%'] > 0]
    loss = tdf[tdf['pnl%'] < 0]
    total = len(tdf)
    wr = len(wins)/total*100
    pnl = tdf['pnl%'].sum()
    avg_win = wins['pnl%'].mean() if len(wins) else 0
    avg_loss = loss['pnl%'].mean() if len(loss) else 0
    avg_bars = tdf['bars'].mean()
    cum = (1 + tdf['pnl%']/100).cumprod() * 10000
    dd = ((cum.cummax() - cum) / cum.cummax()).max() * 100
    v5_cnt = (tdf['entry_type'] == 'V5').sum()
    reasons = tdf['reason'].value_counts().to_dict()
    results.append({
        'Name': name, 'Trades': total, 'WR': wr, 'PnL%': pnl, 'MaxDD%': dd,
        'AvgWin%': avg_win, 'AvgLoss%': avg_loss, 'AvgBars': avg_bars,
        'V5': v5_cnt, 'V4': total - v5_cnt,
        'TP': reasons.get('TP',0), 'SL': reasons.get('SL',0),
        'Trail': reasons.get('Trail',0), 'TIME': reasons.get('TIME',0)
    })

# ─── 6. Display ────────────────────────────────────
print("\n" + "="*95)
print("📊 เปรียบเทียบ XAU FIX v1 กับ v11.2 บนข้อมูลเดียวกัน (2 Mar - 12 Jun 2026 H1)")
print("="*95)
print(f"{'Config':<34s} {'Trades':>6s} {'WR':>6s} {'PnL%':>8s} {'DD%':>7s} {'AvgWin%':>8s} {'AvgLoss%':>8s} {'Bars':>5s}")
print("-"*95)
for r in results:
    if r['Trades'] > 0:
        print(f"{r['Name']:<34s} {r['Trades']:6d} {r['WR']:5.1f}% {r['PnL%']:+7.2f}% {r['MaxDD%']:6.2f}% {r['AvgWin%']:7.2f}% {r['AvgLoss%']:7.2f}% {r['AvgBars']:5.1f}")
    else:
        print(f"{r['Name']:<34s} NO TRADES")

print("\n🔎 รายละเอียด Exit & Entry:")
for r in results:
    if r['Trades'] > 0:
        print(f"▶ {r['Name']}:")
        print(f"   V5={r['V5']}  V4={r['V4']}  TP={r['TP']}  SL={r['SL']}  Trail={r['Trail']}  TIME={r['TIME']}")
print("\n✅ DONE")
