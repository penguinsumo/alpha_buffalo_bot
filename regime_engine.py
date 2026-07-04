from dataclasses import dataclass
from enum import Enum
from typing import Optional


class MarketRegime(str, Enum):
    TRENDING = "TRENDING"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeState:
    regime: MarketRegime
    trend_strength: float      # 0.0 - 1.0
    volatility_score: float    # normalized
    confidence: float          # quality ของ regime classification


def classify_regime(
    atr_ratio: float,
    adx: Optional[float],
    ema_slope: float
) -> RegimeState:
    """
    Pure function: classify current market regime.
    No side effects, no API calls, no orders.
    """
    # Simple rule-based classifier (Phase 4 Lean)
    if adx is not None and adx > 25 and abs(ema_slope) > 0.01:
        regime = MarketRegime.TRENDING
    elif adx is not None and adx < 20:
        regime = MarketRegime.RANGING
    elif atr_ratio > 1.5:
        regime = MarketRegime.HIGH_VOLATILITY
    elif atr_ratio < 0.5:
        regime = MarketRegime.LOW_VOLATILITY
    else:
        regime = MarketRegime.UNKNOWN

    return RegimeState(
        regime=regime,
        trend_strength=min(1.0, max(0.0, abs(ema_slope) * 20)),
        volatility_score=min(1.0, max(0.0, atr_ratio)),
        confidence=0.8 if regime != MarketRegime.UNKNOWN else 0.4
    )
