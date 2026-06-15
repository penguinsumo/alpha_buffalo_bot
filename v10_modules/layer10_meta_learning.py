import numpy as np
from collections import defaultdict

class MetaLearningEngine:
    """Hedge Fund Grade Meta-Learning"""
    
    def __init__(self):
        self.regime_stats = defaultdict(lambda: {
            'trades': 0, 'wins': 0, 'pnl': 0.0, 'weight': 1.0
        })
        self.score_weights = {
            'bb_touch': 1.0, 'kivanc_golden': 2.0,
            'vsa_stopping': 2.0, 'liquidity_sweep': 1.0,
        }
        self.alpha = 0.1
        self.min_trades = 5
    
    def update_regime_performance(self, regime, pnl_pct, win):
        stats = self.regime_stats[regime]
        stats['trades'] += 1
        if win: stats['wins'] += 1
        stats['pnl'] += pnl_pct
        if stats['trades'] >= self.min_trades:
            wr = stats['wins'] / stats['trades'] * 100
            avg_pnl = stats['pnl'] / stats['trades']
            perf_score = (wr / 100) * (1 + avg_pnl)
            target = np.clip(perf_score, 0.5, 2.0)
            stats['weight'] = (1 - self.alpha) * stats['weight'] + self.alpha * target
    
    def get_regime_weight(self, regime):
        return self.regime_stats[regime]['weight']
    
    def should_trade_regime(self, regime):
        stats = self.regime_stats[regime]
        if stats['trades'] < 10: return True
        wr = stats['wins'] / stats['trades'] * 100
        if wr < 40 and stats['pnl'] / stats['trades'] < -0.5: return False
        return True
    
    def get_adaptive_threshold(self, regime, base_threshold):
        return base_threshold / self.get_regime_weight(regime)
    
    def get_summary(self):
        summary = {}
        for regime, stats in self.regime_stats.items():
            if stats['trades'] > 0:
                summary[regime] = {
                    'trades': stats['trades'],
                    'wr': round(stats['wins'] / stats['trades'] * 100, 1),
                    'avg_pnl': round(stats['pnl'] / stats['trades'], 2),
                    'weight': round(stats['weight'], 2),
                }
        return summary