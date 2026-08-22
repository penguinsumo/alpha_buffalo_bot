"""Adaptive hourly win-rate tracker.

Ported from clean v5's trade_manager.py (`AlphaTradeManager v4.4`)
HourlyStats class -- the one piece of clean v5 that was already doing
primitive adaptive learning (rolling win-rate by UTC hour of day) that
v12-core had nothing equivalent to.

This is diagnostic/telemetry only, same as newday.py and
fundamental/context.py: nothing in engine_v4 reads it, so it cannot become
an entry gate. It exists so real closed-trade outcomes start accumulating
into a queryable shape now, ahead of any future decision to feed it back
into risk_adjustment or an actual AI-learning model.
"""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Deque, Dict


class HourlyStats:
    """Rolling window of per-UTC-hour trade outcomes (in R multiples)."""

    def __init__(self, maxlen: int = 60):
        self.maxlen = maxlen
        self.pnls: Dict[int, Deque[float]] = defaultdict(lambda: deque(maxlen=maxlen))

    def record(self, hour: int, pnl_r: float) -> None:
        hour = int(hour) % 24
        self.pnls[hour].append(float(pnl_r))

    def wr(self, hour: int, min_samples: int = 5) -> float:
        """Win rate for `hour` (0.0-1.0). Returns 0.5 (neutral) until there
        are at least `min_samples` closed trades in that hour bucket -- an
        empty or thin bucket must never look like a confident 0% or 100%.
        """
        vals = self.pnls[int(hour) % 24]
        if len(vals) < min_samples:
            return 0.5
        wins = sum(1 for v in vals if v > 0)
        return wins / len(vals)

    def avg_pnl(self, hour: int) -> float:
        vals = self.pnls[int(hour) % 24]
        return sum(vals) / len(vals) if vals else 0.0

    def sample_count(self, hour: int) -> int:
        return len(self.pnls[int(hour) % 24])

    def summary(self, min_samples: int = 5) -> Dict[str, dict]:
        """JSON-safe snapshot for diagnostics, one entry per hour that has
        at least one recorded trade."""
        out: Dict[str, dict] = {}
        for hour, vals in sorted(self.pnls.items()):
            if not vals:
                continue
            out[str(hour)] = {
                "samples": len(vals),
                "win_rate": self.wr(hour, min_samples=min_samples),
                "avg_r": round(self.avg_pnl(hour), 4),
            }
        return out

    def to_json(self) -> Dict[str, list]:
        """Serialize raw per-hour value lists for persistence."""
        return {str(hour): list(vals) for hour, vals in self.pnls.items() if vals}

    @classmethod
    def from_json(cls, payload: Dict[str, list] | None, maxlen: int = 60) -> "HourlyStats":
        stats = cls(maxlen=maxlen)
        for hour_str, vals in (payload or {}).items():
            try:
                hour = int(hour_str) % 24
            except (TypeError, ValueError):
                continue
            for v in vals[-maxlen:]:
                try:
                    stats.pnls[hour].append(float(v))
                except (TypeError, ValueError):
                    continue
        return stats
