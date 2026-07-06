from dataclasses import dataclass, asdict
from typing import Optional, Dict, List
import json
import time

@dataclass
class TradeEvidence:
    timestamp: float
    pattern: str
    direction: str
    bos: bool
    vsa_ok: bool
    atr_value: Optional[float]
    entry_price: float
    exit_price: float
    sl: float
    tp: float
    pnl: float
    r_multiple: float
    confidence: float
    regime: Optional[str] = None

    # v12 Evidence Context
    session_name: str = ""
    newday_bias: str = ""
    screen_trend: str = ""

    # PRZ / Harmonic context
    prz_zone_type: str = ""
    inside_deep_prz: bool = False
    deep_prz_low: float = 0.0
    deep_prz_high: float = 0.0
    nearest_prz_type: str = ""
    nearest_prz_low: float = 0.0
    nearest_prz_high: float = 0.0
    next_prz_type: str = ""
    next_prz_low: float = 0.0
    next_prz_high: float = 0.0

    # Structure confirmation
    bos_break_close: bool = False
    bos_direction: str = "NONE"
    choch_state: str = "NONE"
    sweep_state: str = "NONE"
    swing_break_level: float = 0.0

    # Mega trend
    ema50_htf: float = 0.0
    ema200_htf: float = 0.0
    mega_trend: str = "NEUTRAL"

    # Micro entry confirmation
    micro_trend: str = "NEUTRAL"
    micro_reclaim: bool = False
    micro_bos_close: bool = False
    micro_sweep_price: float = 0.0
    micro_reclaim_price: float = 0.0
    entry_mode: str = "NONE"
    entry_quality: str = "LOW"
    sl_hunt_risk: str = "UNKNOWN"
    sl_reference: str = "NONE"

    # V4 micro scalp engine
    v4_active: bool = False
    v4_state: str = "IDLE"
    v4_target: float = 0.0
    v4_partial_pct: float = 0.0

    # V5 cycle runner
    v5_active: bool = False
    v5_state: str = "WAIT_PRZ"
    v5_prz_qualified: bool = False
    v5_bos_qualified: bool = False
    v5_be_armed: bool = False
    v5_trailing_active: bool = False
    v5_cycle_target: float = 0.0

    # Plugin pressure
    dxy_bias: str = "UNKNOWN"
    news_risk: str = "UNKNOWN"
    orderbook_bias: str = "UNKNOWN"
    cme_bias: str = "UNKNOWN"
    vsa_bias: str = "UNKNOWN"

    # Protection
    breaker_state: str = "NORMAL"
    hedge_used: bool = False
    hedge_reason: str = ""

class EdgeLogger:
    def __init__(self):
        self.trades: List[TradeEvidence] = []

    def log_trade(self, trade: TradeEvidence):
        self.trades.append(trade)

    def export_json(self, path: str):
        with open(path, "w") as f:
            json.dump([asdict(t) for t in self.trades], f, indent=2)

    def summary(self) -> Dict:
        if not self.trades:
            return {}
        total = len(self.trades)
        wins = len([t for t in self.trades if t.pnl > 0])
        avg_r = sum(t.r_multiple for t in self.trades) / total
        win_rate = wins / total
        return {"total_trades": total, "win_rate": win_rate, "avg_r_multiple": avg_r}
