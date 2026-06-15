# ═══════════════════════════════════════════════
# 🐃 LAYER 6: EXECUTION (Order Logic)
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np

class ExecutionEngine:
    """Execute orders with SL/TP"""
    
    def __init__(self, config):
        self.config = config
    
    def open_trade(self, signal, qty_info):
        """Create trade object from signal"""
        direction = signal['direction']
        entry = signal['entry']
        sl_distance = qty_info['sl_distance']
        
        if direction == 'BUY':
            sl = entry - sl_distance
            tp = signal['bb_high'] * 1.005
        else:
            sl = entry + sl_distance
            tp = signal['bb_low'] * 0.995
        
        return {
            'direction': direction,
            'entry': entry,
            'sl': sl,
            'tp': tp,
            'qty': qty_info['qty'],
            'be_activated': False,
            'highest': entry,
            'lowest': entry,
            'bars_held': 0,
            'active': True,
            'exit_price': None,
            'exit_reason': None,
            'pnl_pct': 0.0,
        }
    
    def check_exit(self, trade, bar):
        """Check if trade should exit on this bar"""
        if not trade['active']:
            return trade
        
        trade['bars_held'] += 1
        
        # Time exit
        if trade['bars_held'] >= self.config['max_trade_bars']:
            trade['active'] = False
            trade['exit_reason'] = 'TIME'
            trade['exit_price'] = bar['Close']
            trade['pnl_pct'] = self._calc_pnl(trade, bar['Close'])
            return trade
        
        if trade['direction'] == 'BUY':
            # TP hit
            if bar['High'] >= trade['tp']:
                trade['active'] = False
                trade['exit_reason'] = 'TP'
                trade['exit_price'] = trade['tp']
                trade['pnl_pct'] = self._calc_pnl(trade, trade['tp'])
                return trade
            
            # Update highest
            if bar['High'] > trade['highest']:
                trade['highest'] = bar['High']
            
            # BE activation
            if not trade['be_activated'] and bar['High'] >= trade['entry'] * (1 + self.config['be_trigger_pct']):
                trade['be_activated'] = True
                trade['sl'] = trade['entry']
            
            # Trailing stop
            if trade['be_activated']:
                trail_sl = trade['highest'] * (1 - self.config['trail_pct'])
                trade['sl'] = max(trade['sl'], trail_sl)
            
            # SL hit
            if bar['Low'] <= trade['sl']:
                trade['active'] = False
                trade['exit_reason'] = 'BE' if trade['be_activated'] else 'SL'
                trade['exit_price'] = trade['sl']
                trade['pnl_pct'] = self._calc_pnl(trade, trade['sl'])
                return trade
        
        else:  # SELL
            if bar['Low'] <= trade['tp']:
                trade['active'] = False
                trade['exit_reason'] = 'TP'
                trade['exit_price'] = trade['tp']
                trade['pnl_pct'] = self._calc_pnl(trade, trade['tp'])
                return trade
            
            if bar['Low'] < trade['lowest']:
                trade['lowest'] = bar['Low']
            
            if not trade['be_activated'] and bar['Low'] <= trade['entry'] * (1 - self.config['be_trigger_pct']):
                trade['be_activated'] = True
                trade['sl'] = trade['entry']
            
            if trade['be_activated']:
                trail_sl = trade['lowest'] * (1 + self.config['trail_pct'])
                trade['sl'] = min(trade['sl'], trail_sl)
            
            if bar['High'] >= trade['sl']:
                trade['active'] = False
                trade['exit_reason'] = 'BE' if trade['be_activated'] else 'SL'
                trade['exit_price'] = trade['sl']
                trade['pnl_pct'] = self._calc_pnl(trade, trade['sl'])
                return trade
        
        return trade
    
    def _calc_pnl(self, trade, exit_price):
        """Calculate PnL percentage"""
        if trade['direction'] == 'BUY':
            return (exit_price - trade['entry']) / trade['entry'] * 100
        else:
            return (trade['entry'] - exit_price) / trade['entry'] * 100