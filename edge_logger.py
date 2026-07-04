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
