#!/usr/bin/env python3
"""Backtest with Evidence Logger (Phase 1) — uses Engine V4 logic, logs TradeEvidence"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pandas as pd, numpy as np
from collections import defaultdict
from datetime import timezone
from data_provider_twelvedata import fetch_twelvedata
from session_clock import SessionClock
from edge_logger import EdgeLogger, TradeEvidence

def add_indicators(df):
    df = df.copy()
    df['BB_Mid'] = df['close'].rolling(20).mean()
    df['BB_Std'] = df['close'].rolling(20).std()
    df['BB_Lower'] = df['BB_Mid'] - 2 * df['BB_Std']
    df['BB_Upper'] = df['BB_Mid'] + 2 * df['BB_Std']
    h, l, c = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
    df['ATR14'] = tr.rolling(14).mean()
    df['EMA20'] = df['close'].ewm(span=20, adjust=False).mean()
    df['EMA50'] = df['close'].ewm(span=50, adjust=False).mean()
    df['Low_Prev'] = df['low'].shift(1)
    df['High_Prev'] = df['high'].shift(1)
    df['Bull_Sweep'] = (df['low'] < df['Low_Prev']) & (df['close'] > df['Low_Prev'])
    df['Bear_Sweep'] = (df['high'] > df['High_Prev']) & (df['close'] < df['High_Prev'])
    ha_close = (df['open'] + df['high'] + df['low'] + df['close']) / 4
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (df['open'].iloc[0] + df['close'].iloc[0]) / 2
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i-1] + ha_close.iloc[i-1]) / 2
    df['HA_Close'] = ha_close
    df['HA_Open'] = ha_open
    df['HA_Bullish'] = df['HA_Close'] > df['HA_Open']
    df1h = df.resample('1h').agg({'high': 'max', 'low': 'min', 'close': 'last'}).dropna()
    if len(df1h) >= 5:
        sw_high = df1h['high'].rolling(5).max()
        sw_low = df1h['low'].rolling(5).min()
        sw_high = sw_high.reindex(df.index, method='ffill')
        sw_low = sw_low.reindex(df.index, method='ffill')
    else:
        sw_high = df['high'].rolling(100).max()
        sw_low = df['low'].rolling(100).min()
    df['Swing_H'] = sw_high
    df['Swing_L'] = sw_low
    df['Diff'] = df['Swing_H'] - df['Swing_L']
    df['Fib_072'] = df['Swing_H'] - df['Diff'] * 0.72
    df['PRZ_Next'] = df['Swing_L']
    df1h['EMA50_1h'] = df1h['close'].ewm(span=50, adjust=False).mean()
    trend_up = (df1h['close'] > df1h['EMA50_1h']).astype(int)
    trend_up = trend_up.reindex(df.index, method='ffill').fillna(0)
    df['Trend_1H_Up'] = trend_up.astype(bool)
    return df

def generate_trades_with_evidence(df, logger):
    clock = SessionClock()
    trades = []
    for i in range(20, len(df)-40):
        row = df.iloc[i]; ts = row.name
        if ts.tzinfo is None: ts = ts.tz_localize('UTC')
        state = clock.get(ts)
        sess = state.session; utc_hour = state.utc_hour
        if sess == 'CLOSED': continue

        # BUY (NY >=15 UTC, 1H uptrend, golden zone, sweep, BB touch)
        if (sess == 'NY' and utc_hour >= 15 and
            row['EMA20'] > row['EMA50'] and row['Diff'] > 0 and row['Trend_1H_Up']):
            gl = row['Swing_H'] - row['Diff'] * 1.0
            gh = row['Swing_H'] - row['Diff'] * 0.5
            if gl <= row['close'] <= gh and row['Bull_Sweep'] and row['low'] <= row['BB_Lower'] * 1.02:
                entry = row['close']; sl = entry - row['ATR14'] * 1.5; tp = row['BB_Upper']
                be_act=False; highest=entry; exit_price=entry
                for j in range(i+1, min(i+40, len(df))):
                    r = df.iloc[j]; hh=r['high']; ll=r['low']
                    if hh > highest: highest=hh
                    if not be_act and highest >= entry * 1.0015: be_act=True; sl=entry
                    if be_act: sl = max(sl, highest * 0.9995)
                    if hh >= tp: exit_price=tp; break
                    if ll <= sl: exit_price=sl; break
                else:
                    exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
                pnl = exit_price - entry
                r = pnl / (entry - sl) if (entry - sl) != 0 else 0
                logger.log_trade(TradeEvidence(
                    timestamp=ts.timestamp(), pattern="NY_BUY", direction="BUY",
                    bos=True, vsa_ok=False, atr_value=row['ATR14'],
                    entry_price=entry, exit_price=exit_price, sl=sl, tp=tp,
                    pnl=pnl, r_multiple=r, confidence=0.0, regime=sess))
                trades.append({'session':sess,'dir':'BUY','entry':entry,'exit':exit_price,'sl':sl,'time':ts})

        # SELL (all sessions, 1H downtrend, sweep, BB upper touch, visual SL, fib TP)
        if (row['EMA20'] < row['EMA50'] and row['Bear_Sweep'] and
            row['high'] >= row['BB_Upper'] * 0.98 and not row['Trend_1H_Up']):
            entry = row['close']; sl = entry + row['ATR14'] * 1.5
            tp = row['Fib_072'] if row['Fib_072'] < entry else row['PRZ_Next']
            mid_crossed=False; exit_price=entry
            for j in range(i+1, min(i+40, len(df))):
                r = df.iloc[j]; hh=r['high']; ll=r['low']
                if not mid_crossed and ll <= r['BB_Mid']: mid_crossed=True; sl=entry
                if ll <= tp: exit_price=tp; break
                if hh >= sl: exit_price=sl; break
            else:
                exit_price = df.iloc[min(i+40-1, len(df)-1)]['close']
            pnl = entry - exit_price
            r = pnl / (sl - entry) if (sl - entry) != 0 else 0
            logger.log_trade(TradeEvidence(
                timestamp=ts.timestamp(), pattern="SELL", direction="SELL",
                bos=False, vsa_ok=False, atr_value=row['ATR14'],
                entry_price=entry, exit_price=exit_price, sl=sl, tp=tp,
                pnl=pnl, r_multiple=r, confidence=0.0, regime=sess))
            trades.append({'session':sess,'dir':'SELL','entry':entry,'exit':exit_price,'sl':sl,'time':ts})
    return trades

def simulate(trades, initial=10000, risk_pct=0.0075, max_contracts=10,
             daily_dd_limit=0.03, max_consec_loss=5):
    trades = sorted(trades, key=lambda x: x['time'])
    sessions = defaultdict(lambda: {'trades': [], 'curve': [initial], 'equity': initial,
                                    'daily_eq_start': initial, 'current_day': None,
                                    'consec_loss': 0, 'stop_day': False, 'stopped': 0, 'max_dd': 0})
    for t in trades:
        sess = t['session']; sd = sessions[sess]
        day = t['time'].date()
        if day != sd['current_day']:
            sd['current_day'] = day; sd['daily_eq_start'] = sd['equity']
            sd['consec_loss'] = 0; sd['stop_day'] = False
        if sd['stop_day']: continue
        sl_dist = abs(t['entry'] - t['sl'])
        if sl_dist < 0.5: sl_dist = 0.5
        contracts = (sd['equity'] * risk_pct) / (sl_dist * 10)
        contracts = max(0.01, min(contracts, max_contracts))
        pnl_pts = (t['exit'] - t['entry']) if t['dir'] == 'BUY' else (t['entry'] - t['exit'])
        pnl_dollar = pnl_pts * 10 * contracts
        sd['equity'] += pnl_dollar
        if pnl_dollar <= 0: sd['consec_loss'] += 1
        else: sd['consec_loss'] = 0
        daily_dd = (sd['daily_eq_start'] - sd['equity']) / sd['daily_eq_start']
        if daily_dd >= daily_dd_limit or sd['consec_loss'] >= max_consec_loss:
            sd['stop_day'] = True; sd['stopped'] += 1
        if sd['equity'] <= 0: sd['equity'] = 0; sd['curve'].append(0); break
        peak = max(sd['curve'])
        dd = (peak - sd['equity']) / peak * 100 if peak > 0 else 0
        if dd > sd['max_dd']: sd['max_dd'] = dd
        sd['curve'].append(sd['equity'])
        sd['trades'].append({**t, 'pnl_$': pnl_dollar, 'contracts': contracts})
    stats = {}
    for sess, sd in sessions.items():
        curve = sd['curve']; final_eq = curve[-1]
        ret = (final_eq / initial - 1) * 100 if initial > 0 else 0
        peak = initial; max_dd = 0
        for eq in curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100 if peak > 0 else 0
            if dd > max_dd: max_dd = dd
        t_list = sd['trades']
        wins = [x for x in t_list if x['pnl_$'] > 0]
        wr = len(wins) / len(t_list) * 100 if t_list else 0
        gp = sum(x['pnl_$'] for x in wins)
        gl = abs(sum(x['pnl_$'] for x in t_list if x['pnl_$'] < 0))
        pf = gp / gl if gl > 0 else float('inf')
        stats[sess] = {'trades': len(t_list), 'wr': wr, 'return': ret, 'dd': max_dd,
                       'pf': pf, 'stopped': sd['stopped'], 'final_eq': final_eq}
    return stats

if __name__ == "__main__":
    print("Loading data...")
    df = fetch_twelvedata('XAU/USD', '15min', 90)
    cutoff = df.index.max() - pd.Timedelta(days=60)
    df = df[df.index >= cutoff]
    df = add_indicators(df).dropna()
    print(f"Bars: {len(df)}")
    logger = EdgeLogger()
    trades = generate_trades_with_evidence(df, logger)
    print(f"Trades generated: {len(trades)}")
    stats = simulate(trades, risk_pct=0.0075, daily_dd_limit=0.03, max_consec_loss=5)
    print("\n--- Backtest Results (with Evidence) ---")
    for sess in ['ASIA', 'LONDON', 'NY']:
        s = stats.get(sess, {})
        if s:
            print(f"{sess}: Return={s['return']:.2f}%, DD={s['dd']:.2f}%, WR={s['wr']:.1f}%, PF={s['pf']:.2f}")
    total_ret = sum(s['return'] for s in stats.values() if s)
    print(f"Sum Return: {total_ret:.2f}%")
    logger.export_json("trade_evidence.json")
    print(f"Evidence exported to trade_evidence.json ({len(logger.trades)} records)")
