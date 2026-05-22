class EquityGuard:
    def __init__(self, starting_equity: float = 10000.0, max_dd_pct: float = 1.8):
        self.starting_equity = starting_equity
        self.current_equity = starting_equity
        self.highest_equity = starting_equity
        self.max_dd_pct = max_dd_pct
        self.is_paused = False

    def check_drawdown(self, current_balance: float) -> bool:
        self.current_equity = current_balance
        if self.current_equity > self.highest_equity:
            self.highest_equity = self.current_equity
        
        dd_pct = ((self.highest_equity - self.current_equity) / self.highest_equity) * 100
        
        if dd_pct >= self.max_dd_pct:
            self.is_paused = True
            return True
        return False

    def ok(self): return not self.is_paused
    def mode(self): return 'PAUSED' if self.is_paused else 'CASHFLOW'
    def new_cycle(self, p=''): return ''
    def set_sniper_mode(self, r=''): pass
    def set_cashflow_mode(self, r=''): pass
    def lot_size(self, base_lot=0.01, stress_level='low', is_sniper=False):
        if not self.ok(): return 0.0
        return base_lot * (2.0 if is_sniper else 1.0)
    def cashflow_tp_sl(self, price=0.0, side='BUY'):
        p=0.01
        if side=='BUY': return round(price+15*p,2), round(price-10*p,2)
        return round(price-15*p,2), round(price+10*p,2)

    class _state:
        mode='CASHFLOW'; mode_reason=''; cycle_id=''
        class today:
            dd_pct=0.0; trades_count=0; win_rate=0.0
