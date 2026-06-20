#!/usr/bin/env python3
"""
backtest_v11_vs_new_real.py — เปรียบเทียบ V11.2 Original vs NEW (Scenario B)
บนข้อมูลจริง XAUUSD 90 วัน (Twelve Data / Mock)
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ── 1. โหลดข้อมูลจริง ──────────────────────────────
def load_real_data(days=90):
    try:
        from data_provider_twelvedata import get_twelvedata_15m  # อาจมีฟังก์ชันนี้
        df = get_twelvedata_15m()
        if df is not None and len(df) > 0:
            cutoff = df.index.max() - pd.Timedelta(days=days)
            df = df[df.index >= cutoff]
            if len(df) < 100:
                raise ValueError("Not enough data")
            logger.info(f"✅ Twelve Data loaded: {len(df)} candles")
            return df
    except Exception as e:
        logger.warning(f"Twelve Data failed: {e}")

    # fallback mock (แจ้งเตือน)
    logger.warning("Using mock data – Twelve Data not available")
    np.random.seed(42)
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    dates = pd.date_range(start, end, freq='15min')
    n = len(dates)
    r = np.random.randn(n) * 0.3
    close = 2400 + np.cumsum(r)
    df = pd.DataFrame({
        'open': close + np.random.randn(n)*0.1,
        'high': close + abs(np.random.randn(n)*0.6),
        'low': close - abs(np.random.randn(n)*0.6),
        'close': close,
        'volume': np.random.randint(30,150,n)
    }, index=dates)
    df['high'] = df[['open','high','close']].max(axis=1)
    df['low'] = df[['open','low','close']].min(axis=1)
    return df

# ── 2. Indicators ──────────────────────────────────
def add_indicators(df15):
    df = df15.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2*df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2*df['BB_Std']
    h,l,c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20).mean()
    df['EMA50'] = df['close'].ewm(span=50).mean()

    # 1H Swing
    df1h = df.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max()
        lows = df1h['low'].rolling(5).min()
        sw_high = highs.max()
        sw_low = lows.min()
    else:
        sw_high = sw_low = 0
    df['Swing_H'] = sw_high
    df['Swing_L'] = sw_low
    df['Diff'] = sw_high - sw_low

    # Sweep
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    return df

# ── 3. Session ─────────────────────────────────────
def get_session(hour_utc):
    if 1 <= hour_utc < 8: return 'ASIA'
    elif 8 <= hour_utc < 13: return 'LONDON'
    elif 13 <= hour_utc < 19: return 'NY'
    return 'ASIA_LOW'

# ── 4. V11.2 Original Logic ───────────────────────
def backtest_v11_original(df15):
    buy_trades, sell_trades = [], []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        session = get_session(row.name.hour)

        # Buy: Sweep + BB Lower + EMA trend
        if row['EMA20'] > row['EMA50']:
            if row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02:
                entry = row['close']
                sl = entry - row['ATR14'] * 1.5
                tp = row['BB_Upper']
                be_act = False; highest = entry; pnl = 0.0
                for j in range(i+1, min(i+max_bars, len(df15))):
                    r = df15.iloc[j]; h, l = r['high'], r['low']
                    if h > highest: highest = h
                    if not be_act and highest >= entry*1.0015:
                        be_act = True; sl = entry
                    if be_act: sl = max(sl, highest*0.9995)
                    if h >= tp: pnl = (tp-entry)/entry*100; break
                    if l <= sl: pnl = (sl-entry)/entry*100; break
                else:
                    last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
                    pnl = (last-entry)/entry*100
                buy_trades.append({'session': session, 'pnl': pnl})

        # Sell: Sweep + BB Upper + EMA trend
        if row['EMA20'] < row['EMA50']:
            if row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98:
                entry = row['close']
                sl = entry + row['ATR14'] * 1.5
                tp = row['BB_Lower']
                be_act = False; lowest = entry; pnl = 0.0
                for j in range(i+1, min(i+max_bars, len(df15))):
                    r = df15.iloc[j]; h, l = r['high'], r['low']
                    if l < lowest: lowest = l
                    if not be_act and l <= entry*0.9985:
                        be_act = True; sl = entry
                    if be_act: sl = min(sl, lowest*1.0005)
                    if l <= tp: pnl = (entry-tp)/entry*100; break
                    if h >= sl: pnl = (entry-sl)/entry*100; break
                else:
                    last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
                    pnl = (entry-last)/entry*100
                sell_trades.append({'session': session, 'pnl': pnl})
    return buy_trades, sell_trades

# ── 5. NEW Logic (No618+100 + Visual SL) ──────────
def backtest_new(df15):
    buy_trades, sell_trades = [], []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        session = get_session(row.name.hour)

        # Buy No618+100
        if (row['EMA20'] > row['EMA50'] and row['Swing_H'] > row['Swing_L'] and row['Diff'] > 0):
            golden_low = row['Swing_H'] - row['Diff'] * 1.0
            golden_high = row['Swing_H'] - row['Diff'] * 0.5
            if (golden_low <= row['close'] <= golden_high and
                row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02):
                entry = row['close']
                sl = entry - row['ATR14'] * 1.5
                tp = row['BB_Upper']
                be_act = False; highest = entry; pnl = 0.0
                for j in range(i+1, min(i+max_bars, len(df15))):
                    r = df15.iloc[j]; h, l = r['high'], r['low']
                    if h > highest: highest = h
                    if not be_act and highest >= entry*1.0015:
                        be_act = True; sl = entry
                    if be_act: sl = max(sl, highest*0.9995)
                    if h >= tp: pnl = (tp-entry)/entry*100; break
                    if l <= sl: pnl = (sl-entry)/entry*100; break
                else:
                    last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
                    pnl = (last-entry)/entry*100
                buy_trades.append({'session': session, 'pnl': pnl})

        # Sell Visual SL
        if (row['EMA20'] < row['EMA50'] and
            row['Bear_Sweep'] and row['high'] >= row['BB_Upper'] * 0.98):
            entry = row['close']
            sl = entry + row['ATR14'] * 1.5
            pnl = 0.0; mid_crossed = False
            for j in range(i+1, min(i+max_bars, len(df15))):
                r = df15.iloc[j]; h, l = r['high'], r['low']
                if not mid_crossed and l <= r['BB_Mid']:
                    mid_crossed = True
                    sl = entry
                if l <= r['BB_Lower']:
                    pnl = (entry - r['BB_Lower'])/entry*100; break
                if h >= sl:
                    pnl = (entry - sl)/entry*100; break
            else:
                last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
                pnl = (entry - last)/entry*100
            sell_trades.append({'session': session, 'pnl': pnl})
    return buy_trades, sell_trades

# ── Stats ──────────────────────────────────────────
def stats(trades):
    if not trades: return {'trades':0,'wr':0,'pnl':0,'dd':0}
    pnls = [t['pnl'] for t in trades]
    total = len(pnls)
    wins = [p for p in pnls if p > 0]
    wr = len(wins)/total*100
    pnl = sum(pnls)
    cum=0; peak=0; dd=0
    for p in pnls:
        cum+=p
        if cum>peak: peak=cum
        if peak-cum>dd: dd=peak-cum
    return {'trades':total,'wr':wr,'pnl':pnl,'dd':dd}

# ── Main ───────────────────────────────────────────
if __name__ == "__main__":
    print("📡 Loading real data (or mock fallback)...")
    df15 = load_real_data(90)
    print(f"✅ {len(df15)} candles")
    df15 = add_indicators(df15).dropna()

    print("\n🔄 Running V11.2 Original...")
    buy_v11, sell_v11 = backtest_v11_original(df15)
    print("🔄 Running NEW (No618+100 + Visual SL)...")
    buy_new, sell_new = backtest_new(df15)

    # Overall stats
    v11_buy = stats(buy_v11)
    v11_sell = stats(sell_v11)
    v11_total = stats(buy_v11 + sell_v11)
    new_buy = stats(buy_new)
    new_sell = stats(sell_new)
    new_total = stats(buy_new + sell_new)

    # ── Print Report ──────────────────────────────
    print("\n" + "="*90)
    print("📊 XAUUSD — V11.2 Original vs NEW (Scenario B)")
    print("="*90)
    print(f"{'Metric':<20} {'V11.2 Original':<25} {'NEW (Scenario B)':<25}")
    print(f"{'Buy Trades':<20} {v11_buy['trades']:<25} {new_buy['trades']:<25}")
    print(f"{'Sell Trades':<20} {v11_sell['trades']:<25} {new_sell['trades']:<25}")
    print(f"{'Total Trades':<20} {v11_total['trades']:<25} {new_total['trades']:<25}")
    print(f"{'Buy Win Rate':<20} {v11_buy['wr']:.1f}%{'':<21} {new_buy['wr']:.1f}%")
    print(f"{'Sell Win Rate':<20} {v11_sell['wr']:.1f}%{'':<21} {new_sell['wr']:.1f}%")
    print(f"{'Total Win Rate':<20} {v11_total['wr']:.1f}%{'':<21} {new_total['wr']:.1f}%")
    print(f"{'Buy PnL':<20} {v11_buy['pnl']:+.2f}%{'':<20} {new_buy['pnl']:+.2f}%")
    print(f"{'Sell PnL':<20} {v11_sell['pnl']:+.2f}%{'':<20} {new_sell['pnl']:+.2f}%")
    print(f"{'Total PnL':<20} {v11_total['pnl']:+.2f}%{'':<20} {new_total['pnl']:+.2f}%")
    print(f"{'Buy Max DD':<20} -{v11_buy['dd']:.2f}%{'':<20} -{new_buy['dd']:.2f}%")
    print(f"{'Sell Max DD':<20} -{v11_sell['dd']:.2f}%{'':<20} -{new_sell['dd']:.2f}%")
    print(f"{'Total Max DD':<20} -{v11_total['dd']:.2f}%{'':<20} -{new_total['dd']:.2f}%")
    print("="*90)

    # ── Session breakdown for NEW ────────────────
    sessions = ['ASIA','LONDON','NY','ASIA_LOW']
    print("\n📊 NEW System — Session Breakdown")
    for ses in sessions:
        b = stats([t for t in buy_new if t['session']==ses])
        s = stats([t for t in sell_new if t['session']==ses])
        t = stats([t for t in (buy_new+sell_new) if t['session']==ses])
        print(f"\n--- {ses} ---")
        print(f"{'':<15} {'Trades':<10} {'Win Rate':<10} {'PnL%':<10} {'Max DD%':<10}")
        print(f"{'BUY':<15} {b['trades']:<10} {b['wr']:.1f}%{'':<6} {b['pnl']:+.2f}%{'':<5} {b['dd']:.2f}%")
        print(f"{'SELL':<15} {s['trades']:<10} {s['wr']:.1f}%{'':<6} {s['pnl']:+.2f}%{'':<5} {s['dd']:.2f}%")
        print(f"{'TOTAL':<15} {t['trades']:<10} {t['wr']:.1f}%{'':<6} {t['pnl']:+.2f}%{'':<5} {t['dd']:.2f}%")
    print("="*90)
