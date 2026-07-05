from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime


# =========================================================
# SCENARIO BLUEPRINT v5.4 (FROZEN CONTRACT)
# =========================================================
# RULE:
# - ห้ามเพิ่ม field ใหม่ใน runtime โดยไม่ผ่าน migration
# - ห้าม plugin เขียนทับ schema
# - ใช้เป็น single source of truth สำหรับ Phase 1
# =========================================================


@dataclass(frozen=True)
class ScenarioBlueprint:
    """Frozen Market Analysis Output (v5.4 CONTRACT LOCK)"""

    # ─────────────────────────────
    # META
    # ─────────────────────────────
    timestamp: str
    symbol: str = "XAUUSD"

    # ─────────────────────────────
    # MARKET CONTEXT
    # ─────────────────────────────
    session: str = ""
    trend_h4: str = "NEUTRAL"
    trend_h1: str = "NEUTRAL"
    market_mode: str = "SIDEWAYS"

    # ─────────────────────────────
    # PRICE CORE
    # ─────────────────────────────
    current_price: float = 0.0

    # ─────────────────────────────
    # INDICATORS (BASE ONLY)
    # ─────────────────────────────
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    # ─────────────────────────────
    # TUNNEL (STRUCTURE ONLY)
    # ─────────────────────────────
    tunnel_upper: float = 0.0
    tunnel_lower: float = 0.0
    tunnel_mid: float = 0.0
    tunnel_slope: float = 0.0
    tunnel_valid: bool = False

    # ─────────────────────────────
    # GOLDEN ZONE (HTF ONLY)
    # ─────────────────────────────
    golden_zone_low: float = 0.0
    golden_zone_high: float = 0.0

    # ─────────────────────────────
    # SWINGS (STRUCTURE CORE)
    # ─────────────────────────────
    swing_L: Optional[float] = None
    swing_H: Optional[float] = None
    swing_HL: Optional[float] = None

    swing_L_idx: int = -1
    swing_H_idx: int = -1
    swing_HL_idx: int = -1

    bos_triggered: bool = False

    # ─────────────────────────────
    # PLAN A / B ONLY (NO LOGIC HERE)
    # ─────────────────────────────
    plan_a_entry: float = 0.0
    plan_a_tp: float = 0.0
    plan_a_sl: float = 0.0

    plan_b_entry: float = 0.0
    plan_b_tp1: float = 0.0
    plan_b_tp2: float = 0.0
    plan_b_sl: float = 0.0

    # ─────────────────────────────
    # HARMONIC (READ ONLY OUTPUT)
    # ─────────────────────────────
    harmonic_pattern: str = ""
    prz_current: Optional[float] = None
    prz_next: Optional[float] = None

    # ─────────────────────────────
    # RISK CORE (NO DECISION LOGIC)
    # ─────────────────────────────
    atr_15m: float = 0.0
    atr_1h: float = 0.0

    max_risk_pct: float = 1.0
    win_rate_est: float = 0.0
    risk_reward_ratio: float = 0.0
    expected_value: float = 0.0

    confidence: str = "LOW"
    historical_win_rate: float = 0.61

    # ─────────────────────────────
    # SESSION INFO
    # ─────────────────────────────
    asia_open: str = "01:00 UTC"
    london_open: str = "08:00 UTC"
    ny_open: str = "13:00 UTC"

    # ─────────────────────────────
    # VALIDATION (REQUIRED FOR PLUGINS)
    # ─────────────────────────────
    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)

    # =========================================================
    # SAFE SERIALIZATION ONLY
    # =========================================================
    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "session": self.session,
            "trend_h4": self.trend_h4,
            "market_mode": self.market_mode,
            "current_price": self.current_price,

            "bb": {
                "upper": self.bb_upper,
                "middle": self.bb_middle,
                "lower": self.bb_lower,
            },

            "tunnel": {
                "upper": self.tunnel_upper,
                "lower": self.tunnel_lower,
                "mid": self.tunnel_mid,
                "slope": self.tunnel_slope,
                "valid": self.tunnel_valid,
            },

            "golden_zone": {
                "low": self.golden_zone_low,
                "high": self.golden_zone_high,
            },

            "swing": {
                "L": self.swing_L,
                "H": self.swing_H,
                "HL": self.swing_HL,
                "bos": self.bos_triggered,
            },

            "plan_a": {
                "entry": self.plan_a_entry,
                "tp": self.plan_a_tp,
                "sl": self.plan_a_sl,
            },

            "plan_b": {
                "entry": self.plan_b_entry,
                "tp1": self.plan_b_tp1,
                "tp2": self.plan_b_tp2,
                "sl": self.plan_b_sl,
            },

            "harmonic": {
                "pattern": self.harmonic_pattern,
                "prz_current": self.prz_current,
                "prz_next": self.prz_next,
            },

            "risk": {
                "atr_15m": self.atr_15m,
                "atr_1h": self.atr_1h,
                "rr": self.risk_reward_ratio,
                "ev": self.expected_value,
                "confidence": self.confidence,
            },

            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors,
        }
