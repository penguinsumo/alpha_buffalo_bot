from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List


@dataclass
class ScenarioBlueprint:

    timestamp: str
    symbol: str = "XAUUSD"

    session: str = ""
    trend_h4: str = "NEUTRAL"
    trend_h1: str = "NEUTRAL"
    market_mode: str = "SIDEWAYS"

    current_price: float = 0.0

    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    tunnel_upper: float = 0.0
    tunnel_lower: float = 0.0
    tunnel_mid: float = 0.0
    tunnel_slope: float = 0.0
    tunnel_valid: bool = False

    golden_zone_low: float = 0.0
    golden_zone_high: float = 0.0

    swing_L: Optional[float] = None
    swing_H: Optional[float] = None
    swing_HL: Optional[float] = None

    swing_L_idx: int = -1
    swing_H_idx: int = -1
    swing_HL_idx: int = -1

    bos_triggered: bool = False

    plan_a_entry: float = 0.0
    plan_a_tp: float = 0.0
    plan_a_sl: float = 0.0

    plan_b_entry: float = 0.0
    plan_b_tp1: float = 0.0
    plan_b_tp2: float = 0.0
    plan_b_sl: float = 0.0

    harmonic_pattern: str = ""
    prz_current: float = 0.0
    prz_next: float = 0.0

    atr_15m: float = 0.0
    atr_1h: float = 0.0

    max_risk_pct: float = 1.0
    win_rate_est: float = 0.0
    risk_reward_ratio: float = 0.0
    expected_value: float = 0.0

    confidence: str = "LOW"
    historical_win_rate: float = 0.61

    asia_open: str = "01:00 UTC"
    london_open: str = "08:00 UTC"
    ny_open: str = "13:00 UTC"

    base_score: int = 0
    decision_bias: str = "WEAK"

    smc_confirmed: bool = False
    vsa_confirmed: bool = False

    prz_support_top: float = 0.0
    prz_support_bottom: float = 0.0

    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return self.__dict__
