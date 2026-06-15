# ═══════════════════════════════════════════════
# 🐃 LAYER 8: PERFORMANCE ANALYTICS
# ═══════════════════════════════════════════════
import numpy as np

class PerformanceTracker:
    """Track and analyze trading performance"""
    
    def __init__(self):
        self.equity_curve = []
        self.current_equity = 0
        self.peak_equity = 0
        self.max_dd = 0
        self.wf_score = 50.0
        self.wf_window_wins = 0
        self.wf_window_count = 0
    
    def update(self, pnl_pct):
        self.current_equity += pnl_pct
        self.equity_curve.append(self.current_equity)
        
        if self.current_equity > self.peak_equity:
            self.peak_equity = self.current_equity
        
        dd = self.peak_equity - self.current_equity
        if dd > self.max_dd:
            self.max_dd = dd
        
        # Walk-Forward update
        self.wf_window_count += 1
        if pnl_pct > 0:
            self.wf_window_wins += 1
        
        if self.wf_window_count >= 20:
            recent_wr = self.wf_window_wins / self.wf_window_count * 100
            self.wf_score = np.clip(recent_wr * 0.7 + self.wf_score * 0.3, 0, 100)
            self.wf_window_wins = 0
            self.wf_window_count = 0
    
    def get_summary(self):
        return {
            'current_equity': self.current_equity,
            'peak_equity': self.peak_equity,
            'max_dd': self.max_dd,
            'wf_score': self.wf_score,
        }