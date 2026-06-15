# ═══════════════════════════════════════════════
# 🐃 LAYER 5: POSITION SIZER
# ═══════════════════════════════════════════════
import numpy as np

class PositionSizer:
    """Calculate position size with risk management"""
    
    def __init__(self, config):
        self.config = config
    
    def calculate(self, signal, equity, dd_pct=0):
        """Calculate position size"""
        entry = signal['entry']
        atr = signal.get('atr', entry * 0.01)
        
        # SL distance
        if self.config['use_atr_sl']:
            sl_distance = atr * self.config['atr_sl_mult']
        else:
            sl_distance = entry * self.config['sl_pct']
        
        # Risk-based qty
        risk_cash = max(equity * self.config['risk_per_trade'] / 100, equity * 0.0005)
        risk_per_unit = sl_distance * self.config['contract_size']
        qty_risk = risk_cash / risk_per_unit if risk_per_unit > 0 else 0
        
        # Adaptive Leverage (DD-based)
        if dd_pct < 2:
            adaptive_lever = self.config['max_leverage']
        elif dd_pct < 5:
            adaptive_lever = min(self.config['max_leverage'], 2.0)
        else:
            adaptive_lever = 1.0
        
        # Leverage cap
        max_notional = equity * adaptive_lever
        qty_leverage = max_notional / (entry * self.config['contract_size'])
        
        # Final qty
        qty = min(qty_risk, qty_leverage, self.config['max_contracts'])
        
        return {
            'qty': max(qty, 0),
            'risk_cash': risk_cash,
            'sl_distance': sl_distance,
            'adaptive_lever': adaptive_lever,
            'dd_pct': dd_pct,
        }