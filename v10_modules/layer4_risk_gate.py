# ═══════════════════════════════════════════════
# 🐃 LAYER 4: RISK GATE (Gatekeeper)
# ═══════════════════════════════════════════════
import pandas as pd
import numpy as np

class RiskGate:
    """Filter signals through risk checks"""
    
    def __init__(self, config):
        self.config = config
        self.consecutive_losses = 0
        self.day_start_equity = None
        self.last_day = None
    
    def check_all(self, signal, df, equity, current_time):
        """Run all risk checks"""
        checks = []
        
        # 1. ATR Regime
        atr_pct = df['ATR14'].iloc[-1] / df['Close'].iloc[-1] * 100
        atr_ok = self.config['min_atr_pct'] <= atr_pct <= self.config['max_atr_pct']
        checks.append(('ATR Regime', atr_ok, f'{atr_pct:.2f}%'))
        
        # 2. Session
        hour = current_time.hour if hasattr(current_time, 'hour') else 12
        session_ok = not self.config['use_session'] or (self.config['session_start'] <= hour <= self.config['session_end'])
        checks.append(('Session', session_ok, f'{hour}:00 UTC'))
        
        # 3. Chop Filter
        bb_width = df['BB_High'].iloc[-1] - df['BB_Low'].iloc[-1]
        bb_width_pct = bb_width / df['BB_Mid'].iloc[-1] * 100
        chop_ok = bb_width_pct > 0.2
        checks.append(('Chop', chop_ok, f'{bb_width_pct:.2f}%'))
        
        # 4. Daily Loss
        day = current_time.date() if hasattr(current_time, 'date') else 1
        if self.last_day != day:
            self.day_start_equity = equity
            self.last_day = day
        
        day_dd = (equity - self.day_start_equity) / self.day_start_equity * 100 if self.day_start_equity else 0
        daily_ok = day_dd > -self.config['daily_loss_pct']
        checks.append(('Daily Loss', daily_ok, f'{day_dd:+.2f}%'))
        
        # 5. Consecutive Loss
        consec_ok = self.consecutive_losses < self.config['max_consec_loss']
        checks.append(('Consec Loss', consec_ok, f'{self.consecutive_losses}/{self.config["max_consec_loss"]}'))
        
        # 6. RR Filter
        if signal:
            sl = signal['entry'] * (1 - self.config['sl_pct']) if signal['direction'] == 'BUY' else signal['entry'] * (1 + self.config['sl_pct'])
            tp = signal['bb_high'] * 1.005 if signal['direction'] == 'BUY' else signal['bb_low'] * 0.995
            rr = (tp - signal['entry']) / (signal['entry'] - sl) if signal['direction'] == 'BUY' else (signal['entry'] - tp) / (sl - signal['entry'])
            rr_ok = rr >= 1.2
            checks.append(('RR', rr_ok, f'{rr:.2f}'))
        else:
            checks.append(('RR', False, 'N/A'))
        
        all_ok = all(c[1] for c in checks)
        return all_ok, checks
    
    def update_loss(self, pnl):
        """Update consecutive loss counter"""
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0