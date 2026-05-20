import uuid
from datetime import timezone, timedelta

class EquityGuard:
    def __init__(self, starting_equity=10000.0):
        self.starting_equity = starting_equity
        self.is_paused = False
        self._cycle_id = str(uuid.uuid4())
        class S: pass
        self.state = S()
        self.state.mode = "CASHFLOW"
        self.state.mode_reason = ""
        self.state.cycle_id = self._cycle_id
        class T:
            dd_pct=0.0;trades_count=0;win_rate=0.0
            realized_pnl=0.0;floating_pnl=0.0;wins=0;losses=0
        self.state.today = T()
    def ok(self): return not self.is_paused
    def mode(self): return self.state.mode
    def update(self,equity=0,floating_pnl=0,realized_pnl=0): pass
    def new_cycle(self,p=""):
        self._cycle_id=str(uuid.uuid4());self.state.cycle_id=self._cycle_id;return self._cycle_id
    def set_sniper_mode(self,r=""):
        if not self.is_paused:self.state.mode="SNIPER";self.state.mode_reason=r
    def set_cashflow_mode(self,r=""):
        if not self.is_paused:self.state.mode="CASHFLOW";self.state.mode_reason=r
    def reset_pause(self): self.is_paused=False;self.state.mode="CASHFLOW"
    def lot_size(self,base_lot=0.01,stress_level="low",is_sniper=False):
        if not self.ok():return 0.0
        m=2.0 if is_sniper else 1.0
        s={"low":1.0,"medium":0.75,"high":0.5,"critical":0.0}.get(stress_level,1.0)
        return min(round(base_lot*m*s,2),0.5)
    def cashflow_tp_sl(self,price=0.0,side="BUY"):
        p=0.01
        if side=="BUY":return round(price+15*p,2),round(price-10*p,2)
        return round(price-15*p,2),round(price+10*p,2)
    def dd_weekly_pct(self): return 0.0
    def summary(self): return f"EquityGuard|Mode:{self.state.mode}"
