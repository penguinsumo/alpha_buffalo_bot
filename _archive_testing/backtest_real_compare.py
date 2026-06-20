#!/usr/bin/env python3
"""
backtest_real_compare.py — ดึงข้อมูลจริงจาก Twelve Data (ถ้ามี) 
หรือ Mock 90 วัน แล้วเปรียบเทียบผลลัพธ์ตาม Session
"""

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# ── 1. ดึงข้อมูลจริงจาก Twelve Data ──────────────────
def load_twelvedata(days=90):
    try:
        from data_provider_twelvedata import fetch_twelvedata
        logger.info("Calling Twelve Data fetch...")
        df_15m, df_1h, df_4h = fetch_twelvedata()
        if df_15m is not None and len(df_15m) > 0:
            cutoff = df_15m.index.max() - pd.Timedelta(days=days)
            df_15m = df_15m[df_15m.index >= cutoff]
            if len(df_15m) < 100:
                raise ValueError("Not enough 15m data after cutoff")
            logger.info(f"✅ Twelve Data: {len(df_15m)} candles loaded")
            return df_15m
    except Exception as e:
        logger.warning(f"Twelve Data failed: {e}")
    return None

def generate_mock_data(days=90, seed=42):
    np.random.seed(seed)
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

# ── 2. Indicators ────────────────────────────────────
def add_indicators(df15):
    df15 = df15.copy()
    df15['BB_Mid'] = df15['close'].rolling(20).mean()
    df15['BB_Std'] = df15['close'].rolling(20).std()
    df15['BB_Lower'] = df15['BB_Mid'] - 2*df15['BB_Std']
    df15['BB_Upper'] = df15['BB_Mid'] + 2*df15['BB_Std']
    h,l,c = df15['high'], df15['low'], df15['close'].shift(1)
    tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
    df15['ATR14'] = tr.rolling(14).mean()
    df15['EMA20'] = df15['close'].ewm(span=20).mean()
    df15['EMA50'] = df15['close'].ewm(span=50).mean()
    
    df1h = df15.resample('1h').agg({'high':'max','low':'min'}).dropna()
    if len(df1h) >= 5:
        highs = df1h['high'].rolling(5).max()
        lows = df1h['low'].rolling(5).min()
        sw_high = highs.max()
        sw_low = lows.min()
    else:
        sw_high = sw_low = 0
    df15['Swing_H'] = sw_high
    df15['Swing_L'] = sw_low
    df15['Diff'] = sw_high - sw_low

    df15['Low_Prev'] = df15['low'].shift(1)
    df15['High_Prev'] = df15['high'].shift(1)
    df15['Bull_Sweep'] = (df15['low'] < df15['Low_Prev']) & (df15['close'] > df15['Low_Prev'])
    df15['Bear_Sweep'] = (df15['high'] > df15['High_Prev']) & (df15['close'] < df15['High_Prev'])
    return df15

# ── 3. Session ───────────────────────────────────────
def get_session(hour_utc):
    if 1 <= hour_utc < 8: return 'ASIA'
    elif 8 <= hour_utc < 13: return 'LONDON'
    elif 13 <= hour_utc < 19: return 'NY'
    return 'ASIA_LOW'

# ── 4. Backtest ──────────────────────────────────────
def run_backtest(df15):
    buy_trades = []
    sell_trades = []
    min_bars, max_bars = 20, 40
    for i in range(min_bars, len(df15)-max_bars):
        row = df15.iloc[i]
        session = get_session(row.name.hour)
        
        # BUY (No618+100)
        if (row['EMA20'] > row['EMA50'] and
            row['Swing_H'] > row['Swing_L'] and row['Diff'] > 0):
            golden_low = row['Swing_H'] - row['Diff'] * 1.0
            golden_high = row['Swing_H'] - row['Diff'] * 0.5
            if (golden_low <= row['close'] <= golden_high and
                row['Bull_Sweep'] and
                row['low'] <= row['BB_Lower'] * 1.02):
                entry = row['close']
                sl = entry - row['ATR14'] * 1.5
                tp = row['BB_Upper']
                be_act = False
                highest = entry
                pnl = 0.0
                for j in range(i+1, min(i+max_bars, len(df15))):
                    r = df15.iloc[j]; h, l = r['high'], r['low']
                    if h > highest: highest = h
                    if not be_act and highest >= entry*1.0015:
                        be_act = True; sl = entry
                    if be_act: sl = max(sl, highest*0.9995)
                    if h >= tp: pnl = (tp - entry)/entry*100; break
                    if l <= sl: pnl = (sl - entry)/entry*100; break
                else:
                    last = df15.iloc[min(i+max_bars-1, len(df15)-1)]['close']
                    pnl = (last - entry)/entry*100
                buy_trades.append({'session': session, 'pnl': pnl})
        
        # SELL (Visual SL)
        if (row['EMA20'] < row['EMA50'] and
            row['Bear_Sweep'] and
            row['high'] >= row['BB_Upper'] * 0.98):
            entry = row['close']
            sl = entry + row['ATR14'] * 1.5
            pnl = 0.0
            mid_crossed = False
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

# ── 5. Stats ─────────────────────────────────────────
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
    return {'trades':total, 'wr':wr, 'pnl':pnl, 'dd':dd}

# ── 6. Main ──────────────────────────────────────────
if __name__ == "__main__":
    days = 90
    print(f"📡 Attempting to load real data from Twelve Data ({days} days)...")
    df15 = load_twelvedata(days)
    if df15 is None:
        print("⚠️ Using mock data (Twelve Data not available)")
        df15 = generate_mock_data(days)
    print(f"✅ {len(df15)} 15m candles")
    
    df15 = add_indicators(df15).dropna()
    buy_trades, sell_trades = run_backtest(df15)
    
    overall_buy = stats(buy_trades)
    overall_sell = stats(sell_trades)
    overall_total = stats(buy_trades + sell_trades)
    
    sessions = ['ASIA', 'LONDON', 'NY', 'ASIA_LOW']
    results = {}
    for ses in sessions:
        buy_ses = [t for t in buy_trades if t['session']==ses]
        sell_ses = [t for t in sell_trades if t['session']==ses]
        results[ses] = {
            'buy': stats(buy_ses),
            'sell': stats(sell_ses),
            'total': stats(buy_ses+sell_ses)
        }
    
    print("\n" + "="*80)
    print("📊 OVERALL RESULTS")
    print("="*80)
    print(f"{'':<15} {'Trades':<10} {'Win Rate':<10} {'PnL%':<10} {'Max DD%':<10}")
    print(f"{'BUY':<15} {overall_buy['trades']:<10} {overall_buy['wr']:.1f}%{'':<6} {overall_buy['pnl']:+.2f}%{'':<5} {overall_buy['dd']:.2f}%")
    print(f"{'SELL':<15} {overall_sell['trades']:<10} {overall_sell['wr']:.1f}%{'':<6} {overall_sell['pnl']:+.2f}%{'':<5} {overall_sell['dd']:.2f}%")
    print(f"{'TOTAL':<15} {overall_total['trades']:<10} {overall_total['wr']:.1f}%{'':<6} {overall_total['pnl']:+.2f}%{'':<5} {overall_total['dd']:.2f}%")
    
    print("\n📊 BY SESSION")
    print("="*80)
    for ses in sessions:
        r = results[ses]
        print(f"\n--- {ses} ---")
        print(f"{'':<15} {'Trades':<10} {'Win Rate':<10} {'PnL%':<10} {'Max DD%':<10}")
        print(f"{'BUY':<15} {r['buy']['trades']:<10} {r['buy']['wr']:.1f}%{'':<6} {r['buy']['pnl']:+.2f}%{'':<5} {r['buy']['dd']:.2f}%")
        print(f"{'SELL':<15} {r['sell']['trades']:<10} {r['sell']['wr']:.1f}%{'':<6} {r['sell']['pnl']:+.2f}%{'':<5} {r['sell']['dd']:.2f}%")
        print(f"{'TOTAL':<15} {r['total']['trades']:<10} {r['total']['wr']:.1f}%{'':<6} {r['total']['pnl']:+.2f}%{'':<5} {r['total']['dd']:.2f}%")
    print("="*80)
