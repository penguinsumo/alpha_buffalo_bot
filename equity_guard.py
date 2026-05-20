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
