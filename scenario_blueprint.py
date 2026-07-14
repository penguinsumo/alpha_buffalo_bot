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

    # ─────────────────────────────
    # Price Action / HA / Delta v12+
    # ─────────────────────────────
    m15_phase: str = "UNKNOWN"
    h1_phase: str = "UNKNOWN"
    h4_phase: str = "UNKNOWN"

    m15_delta: str = "NEUTRAL"
    h1_delta: str = "NEUTRAL"
    h4_delta: str = "NEUTRAL"

    m15_impulse: bool = False
    h1_impulse: bool = False

    ha_m15_bullish: bool = False
    ha_m15_bearish: bool = False
    ha_h1_bullish: bool = False
    ha_h1_bearish: bool = False

    watch_bias: str = "NONE"
    delta_alignment: str = "NONE"
    impulse_direction: str = "NONE"

    current_price: float = 0.0

    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0

    tunnel_upper: float = 0.0
    tunnel_lower: float = 0.0
    tunnel_mid: float = 0.0
    tunnel_slope: float = 0.0
    tunnel_valid: bool = False
    tunnel_timeframe: str = "1H"
    tunnel_source: str = "confirmed_h1_pivots"
    tunnel_anchor_time_1: int = 0
    tunnel_anchor_price_1: float = 0.0
    tunnel_anchor_time_2: int = 0
    tunnel_anchor_price_2: float = 0.0
    tunnel_parallel_time: int = 0
    tunnel_parallel_price: float = 0.0
    tunnel_anchor_version: int = 0

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
    prz_current: Optional[float] = None
    prz_next: Optional[float] = None

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

    # ─────────────────────────────
    # Market-close framework map
    # ─────────────────────────────
    market_map_date: str = ""
    market_map_source: str = "NONE"
    lot0_price: float = 0.0
    lot0_side: str = "NONE"
    lot0_source: str = "NONE"
    lot0_timeframe: str = "NONE"
    kivanc_boundary_high: float = 0.0
    kivanc_boundary_low: float = 0.0
    kivanc_fibo_0618: float = 0.0
    kivanc_fibo_0786: float = 0.0
    kivanc_fibo_0886: float = 0.0

    # ─────────────────────────────
    # PRZ LAYERS v12+
    # ─────────────────────────────
    # HTF / New Day structural PRZ
    htf_prz_support_low: float = 0.0
    htf_prz_support_high: float = 0.0
    htf_prz_resistance_low: float = 0.0
    htf_prz_resistance_high: float = 0.0

    # Harmonic forecast PRZ
    harmonic_prz_low: float = 0.0
    harmonic_prz_high: float = 0.0
    harmonic_completion: float = 0.0
    harmonic_probability: float = 0.0
    harmonic_state: str = "NONE"
    harmonic_source_tf: str = "NONE"
    harmonic_source: str = "NONE"
    harmonic_direction: str = "NONE"
    harmonic_approach_direction: str = "NONE"
    harmonic_pattern_state: str = "NONE"
    harmonic_is_real: bool = False
    harmonic_d_point: float = 0.0
    harmonic_x_price: float = 0.0
    harmonic_a_price: float = 0.0
    harmonic_b_price: float = 0.0
    harmonic_c_price: float = 0.0
    harmonic_ratios: Dict[str, float] = field(default_factory=dict)
    harmonic_tp1: float = 0.0
    harmonic_tp2: float = 0.0
    harmonic_tp3: float = 0.0
    harmonic_invalidation: float = 0.0
    harmonic_projection_mode: str = "COMPLETED_XABCD"
    harmonic_execution_authority: bool = True
    harmonic_tunnel_broken: bool = False
    harmonic_selected_pattern: str = ""
    harmonic_candidate_patterns: List[Dict] = field(default_factory=list)
    harmonic_current_xad: float = 0.0
    harmonic_current_bcd: float = 0.0
    harmonic_next_xad: float = 0.0

    htf_prz_timeframe: str = "1H"
    htf_prz_source: str = "df_1h_prz_zone_proxy"
    harmonic_prz_timeframe: str = "NONE"
    harmonic_prz_source: str = "NONE"
    micro_prz_timeframe: str = "15M"
    micro_prz_source: str = "df_15m_prz_zone_proxy"

    # Micro execution PRZ
    micro_prz_low: float = 0.0
    micro_prz_high: float = 0.0

    # ─────────────────────────────
    # PRZ Forecast Grid / OTE Map
    # ─────────────────────────────
    prz_forecast_timeframe: str = "NONE"
    prz_forecast_source: str = "NONE"
    prz_forecast_swing_high: float = 0.0
    prz_forecast_swing_low: float = 0.0
    prz_forecast_status: str = "NONE"

    prz_a_resistance_low: float = 0.0
    prz_a_resistance_high: float = 0.0
    prz_a_support_low: float = 0.0
    prz_a_support_high: float = 0.0

    prz_b_resistance_low: float = 0.0
    prz_b_resistance_high: float = 0.0
    prz_b_support_low: float = 0.0
    prz_b_support_high: float = 0.0

    nearest_prz_name: str = "NONE"
    nearest_prz_role: str = "NONE"
    nearest_prz_direction: str = "NONE"
    nearest_prz_low: float = 0.0
    nearest_prz_high: float = 0.0
    nearest_prz_distance: float = 0.0
    nearest_prz_distance_pct: float = 0.0

    active_prz_name: str = "NONE"
    active_prz_tier: str = "NONE"

    next_upside_prz_name: str = "NONE"
    next_upside_prz_low: float = 0.0
    next_upside_prz_high: float = 0.0

    next_downside_prz_name: str = "NONE"
    next_downside_prz_low: float = 0.0
    next_downside_prz_high: float = 0.0

    extended_upside_prz_name: str = "NONE"
    extended_upside_prz_low: float = 0.0
    extended_upside_prz_high: float = 0.0

    extended_downside_prz_name: str = "NONE"
    extended_downside_prz_low: float = 0.0
    extended_downside_prz_high: float = 0.0

    # PRZ validation
    inside_htf_prz: bool = False
    inside_micro_prz: bool = False
    bos_confirmed: bool = False
    choch_confirmed: bool = False
    zone_validated: bool = False
    zone_invalidated: bool = False

    # PRZ / Harmonic state
    prz_state: str = "UNKNOWN"
    micro_prz_broken: bool = False
    micro_prz_reclaimed: bool = False
    reversal_allowed: bool = True

    # Tunnel / Parallel channel state
    tunnel_state: str = "NONE"
    inside_tunnel: bool = False
    near_tunnel_upper: bool = False
    near_tunnel_mid: bool = False
    near_tunnel_lower: bool = False
    tunnel_retest_valid: bool = False
    buy_tunnel_sweep: bool = False
    sell_tunnel_sweep: bool = False
    tunnel_sweep_upper: float = 0.0
    tunnel_sweep_lower: float = 0.0

    # Plan routing
    trade_plan: str = "NONE"
    execution_state: str = "WATCH"

    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "timestamp": self.timestamp,
            "symbol": self.symbol,
            "session": self.session,
            "trend_h4": self.trend_h4,
            "trend_h1": self.trend_h1,
            "market_mode": self.market_mode,
            "price_action": {
                "m15_phase": self.m15_phase,
                "h1_phase": self.h1_phase,
                "h4_phase": self.h4_phase,
                "m15_delta": self.m15_delta,
                "h1_delta": self.h1_delta,
                "h4_delta": self.h4_delta,
                "m15_impulse": self.m15_impulse,
                "h1_impulse": self.h1_impulse,
                "ha_m15_bullish": self.ha_m15_bullish,
                "ha_m15_bearish": self.ha_m15_bearish,
                "ha_h1_bullish": self.ha_h1_bullish,
                "ha_h1_bearish": self.ha_h1_bearish,
                "watch_bias": self.watch_bias,
                "delta_alignment": self.delta_alignment,
                "impulse_direction": self.impulse_direction,
            },
            "current_price": self.current_price,
            "bb": {
                "upper": self.bb_upper,
                "middle": self.bb_middle,
                "lower": self.bb_lower,
            },
            "tunnel": {
                "timeframe": self.tunnel_timeframe,
                "source": self.tunnel_source,
                "upper": self.tunnel_upper,
                "lower": self.tunnel_lower,
                "mid": self.tunnel_mid,
                "slope": self.tunnel_slope,
                "valid": self.tunnel_valid,
                "anchor_time_1": self.tunnel_anchor_time_1,
                "anchor_price_1": self.tunnel_anchor_price_1,
                "anchor_time_2": self.tunnel_anchor_time_2,
                "anchor_price_2": self.tunnel_anchor_price_2,
                "parallel_time": self.tunnel_parallel_time,
                "parallel_price": self.tunnel_parallel_price,
                "anchor_version": self.tunnel_anchor_version,
            },
            "golden_zone": {
                "low": self.golden_zone_low,
                "high": self.golden_zone_high,
            },
            "market_close_map": {
                "date": self.market_map_date,
                "source": self.market_map_source,
                "lot0": {
                    "price": self.lot0_price,
                    "side": self.lot0_side,
                    "source": self.lot0_source,
                    "timeframe": self.lot0_timeframe,
                },
                "kivanc": {
                    "boundary_high": self.kivanc_boundary_high,
                    "boundary_low": self.kivanc_boundary_low,
                    "fibo_0618": self.kivanc_fibo_0618,
                    "fibo_0786": self.kivanc_fibo_0786,
                    "fibo_0886": self.kivanc_fibo_0886,
                },
            },
            "swing": {
                "L": self.swing_L,
                "H": self.swing_H,
                "HL": self.swing_HL,
                "L_idx": self.swing_L_idx,
                "H_idx": self.swing_H_idx,
                "HL_idx": self.swing_HL_idx,
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
                "state": self.harmonic_state,
                "pattern_state": self.harmonic_pattern_state,
                "source_tf": self.harmonic_source_tf,
                "source": self.harmonic_source,
                "direction": self.harmonic_direction,
                "approach_direction": self.harmonic_approach_direction,
                "is_real_harmonic": self.harmonic_is_real,
                "x": self.harmonic_x_price,
                "a": self.harmonic_a_price,
                "b": self.harmonic_b_price,
                "c": self.harmonic_c_price,
                "d": self.harmonic_d_point,
                "ratios": self.harmonic_ratios,
                "prz_current": self.prz_current,
                "prz_next": self.prz_next,
                "prz_low": self.harmonic_prz_low,
                "prz_high": self.harmonic_prz_high,
                "tp1": self.harmonic_tp1,
                "tp2": self.harmonic_tp2,
                "tp3": self.harmonic_tp3,
                "invalidation": self.harmonic_invalidation,
                "projection_mode": self.harmonic_projection_mode,
                "execution_authority": self.harmonic_execution_authority,
                "tunnel_broken": self.harmonic_tunnel_broken,
                "selected_pattern": self.harmonic_selected_pattern,
                "candidate_patterns": self.harmonic_candidate_patterns,
                "current_xad": self.harmonic_current_xad,
                "current_bcd": self.harmonic_current_bcd,
                "next_xad": self.harmonic_next_xad,
                "support_top": self.prz_support_top,
                "support_bottom": self.prz_support_bottom,
            },
            "risk": {
                "atr_15m": self.atr_15m,
                "atr_1h": self.atr_1h,
                "max_risk_pct": self.max_risk_pct,
                "win_rate_est": self.win_rate_est,
                "rr": self.risk_reward_ratio,
                "ev": self.expected_value,
                "confidence": self.confidence,
                "historical_win_rate": self.historical_win_rate,
            },
            "engine": {
                "base_score": self.base_score,
                "bias": self.decision_bias,
                "smc": self.smc_confirmed,
                "vsa": self.vsa_confirmed,
            },
            "prz_layers": {
                "htf": {
                    "timeframe": self.htf_prz_timeframe,
                    "source": self.htf_prz_source,
                    "support_low": self.htf_prz_support_low,
                    "support_high": self.htf_prz_support_high,
                    "resistance_low": self.htf_prz_resistance_low,
                    "resistance_high": self.htf_prz_resistance_high,
                },
                "harmonic_forecast": {
                    "timeframe": self.harmonic_prz_timeframe,
                    "source": self.harmonic_prz_source,
                    "pattern": self.harmonic_pattern,
                    "direction": self.harmonic_direction,
                    "is_real_harmonic": self.harmonic_is_real,
                    "low": self.harmonic_prz_low,
                    "high": self.harmonic_prz_high,
                    "d_point": self.harmonic_d_point,
                    "completion": self.harmonic_completion,
                    "probability": self.harmonic_probability,
                    "state": self.harmonic_state,
                },
                "micro": {
                    "timeframe": self.micro_prz_timeframe,
                    "source": self.micro_prz_source,
                    "low": self.micro_prz_low,
                    "high": self.micro_prz_high,
                },
                "forecast": {
                    "timeframe": self.prz_forecast_timeframe,
                    "source": self.prz_forecast_source,
                    "status": self.prz_forecast_status,
                    "swing": {
                        "high": self.prz_forecast_swing_high,
                        "low": self.prz_forecast_swing_low,
                    },
                    "classic_ote": {
                        "name": "PRZ-A",
                        "ratio_low": 0.618,
                        "ratio_high": 0.705,
                        "role": "PRIMARY_WATCH",
                        "resistance": {
                            "low": self.prz_a_resistance_low,
                            "high": self.prz_a_resistance_high,
                        },
                        "support": {
                            "low": self.prz_a_support_low,
                            "high": self.prz_a_support_high,
                        },
                    },
                    "deep_ote": {
                        "name": "PRZ-B",
                        "ratio_low": 0.786,
                        "ratio_high": 0.886,
                        "role": "LIQUIDITY_SWEEP_WATCH",
                        "resistance": {
                            "low": self.prz_b_resistance_low,
                            "high": self.prz_b_resistance_high,
                        },
                        "support": {
                            "low": self.prz_b_support_low,
                            "high": self.prz_b_support_high,
                        },
                    },
                    "nearest_zone": {
                        "name": self.nearest_prz_name,
                        "role": self.nearest_prz_role,
                        "direction": self.nearest_prz_direction,
                        "low": self.nearest_prz_low,
                        "high": self.nearest_prz_high,
                        "distance": self.nearest_prz_distance,
                        "distance_pct": self.nearest_prz_distance_pct,
                    },
                    "active_zone": {
                        "name": self.active_prz_name,
                        "tier": self.active_prz_tier,
                    },
                    "next_upside_zone": {
                        "name": self.next_upside_prz_name,
                        "low": self.next_upside_prz_low,
                        "high": self.next_upside_prz_high,
                    },
                    "next_downside_zone": {
                        "name": self.next_downside_prz_name,
                        "low": self.next_downside_prz_low,
                        "high": self.next_downside_prz_high,
                    },
                    "extended_upside_zone": {
                        "name": self.extended_upside_prz_name,
                        "low": self.extended_upside_prz_low,
                        "high": self.extended_upside_prz_high,
                    },
                    "extended_downside_zone": {
                        "name": self.extended_downside_prz_name,
                        "low": self.extended_downside_prz_low,
                        "high": self.extended_downside_prz_high,
                    },
                },
                "validation": {
                    "inside_htf_prz": self.inside_htf_prz,
                    "inside_micro_prz": self.inside_micro_prz,
                    "bos_confirmed": self.bos_confirmed,
                    "choch_confirmed": self.choch_confirmed,
                    "zone_validated": self.zone_validated,
                    "zone_invalidated": self.zone_invalidated,
                },
                "state": {
                    "prz_state": self.prz_state,
                    "micro_prz_broken": self.micro_prz_broken,
                    "micro_prz_reclaimed": self.micro_prz_reclaimed,
                    "reversal_allowed": self.reversal_allowed,
                },
                "tunnel_state": {
                    "timeframe": self.tunnel_timeframe,
                    "source": self.tunnel_source,
                    "state": self.tunnel_state,
                    "inside_tunnel": self.inside_tunnel,
                    "near_upper": self.near_tunnel_upper,
                    "near_mid": self.near_tunnel_mid,
                    "near_lower": self.near_tunnel_lower,
                    "retest_valid": self.tunnel_retest_valid,
                    "buy_sweep_armed": self.buy_tunnel_sweep,
                    "sell_sweep_armed": self.sell_tunnel_sweep,
                    "sweep_upper": self.tunnel_sweep_upper,
                    "sweep_lower": self.tunnel_sweep_lower,
                    "upper": self.tunnel_upper,
                    "middle": self.tunnel_mid,
                    "lower": self.tunnel_lower,
                    "slope": self.tunnel_slope,
                    "valid": self.tunnel_valid,
                    "anchor_time_1": self.tunnel_anchor_time_1,
                    "anchor_price_1": self.tunnel_anchor_price_1,
                    "anchor_time_2": self.tunnel_anchor_time_2,
                    "anchor_price_2": self.tunnel_anchor_price_2,
                    "parallel_time": self.tunnel_parallel_time,
                    "parallel_price": self.tunnel_parallel_price,
                    "anchor_version": self.tunnel_anchor_version,
                },
                "bb_15m": {
                    "timeframe": "15M",
                    "source": "df_15m_bollinger",
                    "upper": self.bb_upper,
                    "middle": self.bb_middle,
                    "lower": self.bb_lower,
                    "width": round(self.bb_upper - self.bb_lower, 3) if self.bb_upper and self.bb_lower else 0.0,
                    "width_pct": round(((self.bb_upper - self.bb_lower) / self.current_price) * 100, 4) if self.current_price and self.bb_upper and self.bb_lower else 0.0,
                    "touch_side": "UPPER" if self.current_price >= self.bb_upper else "LOWER" if self.current_price <= self.bb_lower else "NONE",
                    "position": round((self.current_price - self.bb_lower) / (self.bb_upper - self.bb_lower), 4) if self.bb_upper and self.bb_lower and self.bb_upper != self.bb_lower else 0.0,
                },
                "routing": {
                    "trade_plan": self.trade_plan,
                    "execution_state": self.execution_state,
                },
            },
            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors,
        }
