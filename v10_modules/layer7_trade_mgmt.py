# ═══════════════════════════════════════════════
# 🐃 LAYER 7: TRADE MANAGEMENT
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np

class TradeManager:
    """Manage open trades and trade history"""
    
    def __init__(self):
        self.open_trade = None
        self.trade_history = []
        self.last_entry_bar = None
    
    def has_open_trade(self):
        return self.open_trade is not None and self.open_trade['active']
    
    def open(self, trade):
        self.open_trade = trade
        return trade
    
    def close(self, bar_index):
        if self.open_trade:
            self.open_trade['close_bar'] = bar_index
            self.trade_history.append(self.open_trade.copy())
            closed = self.open_trade
            self.open_trade = None
            return closed
        return None
    
    def can_enter(self, bar_index, cooldown_bars=5):
        if self.has_open_trade():
            return False
        if self.last_entry_bar is None:
            return True
        return (bar_index - self.last_entry_bar) > cooldown_bars
    
    def mark_entry(self, bar_index):
        self.last_entry_bar = bar_index
    
    def get_stats(self):
        if not self.trade_history:
            return {}
        
        wins = [t for t in self.trade_history if t['pnl_pct'] > 0]
        losses = [t for t in self.trade_history if t['pnl_pct'] <= 0]
        
        return {
            'total': len(self.trade_history),
            'wins': len(wins),
            'losses': len(losses),
            'wr': len(wins) / len(self.trade_history) * 100,
            'avg_win': sum(t['pnl_pct'] for t in wins) / len(wins) if wins else 0,
            'avg_loss': sum(t['pnl_pct'] for t in losses) / len(losses) if losses else 0,
            'pf': sum(t['pnl_pct'] for t in wins) / abs(sum(t['pnl_pct'] for t in losses)) if losses else float('inf'),
            'net_pnl': sum(t['pnl_pct'] for t in self.trade_history),
            'exit_reasons': {r: len([t for t in self.trade_history if t['exit_reason']==r]) for r in set(t['exit_reason'] for t in self.trade_history)},
        }