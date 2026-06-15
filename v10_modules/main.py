import sys
sys.path.insert(0, 'v10_modules')
from config import CONFIG
from layer1_data import DataProvider
from layer2_indicators import Indicators
from layer3_signals import SignalEngine
from layer4_risk_gate import RiskGate
from layer5_position_sizer import PositionSizer
from layer6_execution import ExecutionEngine
from layer7_trade_mgmt import TradeManager
from layer8_performance import PerformanceTracker
from layer9_adaptive import AdaptiveEngine
from layer10_meta_learning import MetaLearningEngine
from datetime import datetime
import pandas as pd
import numpy as np

def run_backtest(symbol='GC=F', start='2026-01-01', end='2026-06-13'):
    print(f'🐃 Alpha Buffalo v10 Backtest')
    print(f'   {symbol} | {start} -> {end}')
    
    print('Layer 1: Fetching data...')
    df = DataProvider.from_yfinance(symbol, start, end, '1h')
    if len(df) == 0:
        print('No data')
        return
    
    print('Layer 2: Calculating indicators...')
    df = Indicators.add_all(df)
    
    signal_engine = SignalEngine(CONFIG)
    risk_gate = RiskGate(CONFIG)
    position_sizer = PositionSizer(CONFIG)
    execution = ExecutionEngine(CONFIG)
    trade_mgr = TradeManager()
    perf = PerformanceTracker()
    adaptive = AdaptiveEngine(CONFIG)
    meta = MetaLearningEngine()
    
    adaptive.update_regime(df.iloc[:50])
    
    print('Running backtest...')
    MIN_BARS = 50
    
    for i in range(MIN_BARS, len(df) - 24):
        if i % 500 == 0:
            print(f'   {i}/{len(df)}')
        
        current_df = df.iloc[:i+1]
        current_bar = df.iloc[i]
        current_time = df.index[i]
        
        current_df = current_df.copy(); current_df["ADX"] = 20
        regime = adaptive.update_regime(current_df)
        
        if trade_mgr.has_open_trade():
            trade = execution.check_exit(trade_mgr.open_trade, current_bar)
            if not trade['active']:
                closed = trade_mgr.close(i)
                perf.update(closed['pnl_pct'])
                risk_gate.update_loss(closed['pnl_pct'])
                meta.update_regime_performance(regime, closed['pnl_pct'], closed['pnl_pct'] > 0)
        
        if not trade_mgr.can_enter(i, CONFIG['cooldown_bars']):
            continue
        
        fib_data = Indicators.get_fib_levels(current_df)
        signal = signal_engine.generate(current_df, fib_data, regime)
        if signal is None:
            continue
        
        equity = 100 + perf.current_equity
        ok, checks = risk_gate.check_all(signal, current_df, equity, current_time)
        if not ok:
            continue
        
        dd_pct = perf.max_dd
        qty_info = position_sizer.calculate(signal, equity, dd_pct)
        if qty_info['qty'] <= 0:
            continue
        
        trade = execution.open_trade(signal, qty_info)
        trade_mgr.open(trade)
        trade_mgr.mark_entry(i)
    
    stats = trade_mgr.get_stats()
    summary = perf.get_summary()
    
    print('\nRESULTS:')
    print(f'  Trades: {stats.get("total", 0)}')
    print(f'  Win Rate: {stats.get("wr", 0):.1f}%')
    print(f'  PF: {stats.get("pf", 0):.2f}')
    print(f'  Net PnL: {stats.get("net_pnl", 0):+.2f}%')
    print(f'  Max DD: -{summary.get("max_dd", 0):.2f}%')
    print(f'  WF Score: {summary.get("wf_score", 0):.0f}')
    
    meta_summary = meta.get_summary()
    print("\nMETA-LEARNING:")
    for regime, ms in meta_summary.items():
        t = ms["trades"]; w = ms["wr"]; a = ms["avg_pnl"]; wt = ms["weight"]
        print(f"  {regime}: {t}T, WR={w}%, Avg={a}%, Weight={wt}")
    return stats, summary

if __name__ == '__main__':
    run_backtest()