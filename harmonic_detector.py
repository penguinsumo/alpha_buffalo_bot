
# ═══ Neutral pattern weights ═══
# No repository-owned, pattern-specific sample currently validates a frequency
# or win-rate ranking.  Keep these neutral until the Newday statistics pipeline
# has a reproducible sample size, date range, symbol and timeframe.
PATTERN_WEIGHTS = {
    "Bat": 1.0,
    "Gartley": 1.0,
    "Butterfly": 1.0,
    "Crab": 1.0,
    "Shark": 1.0,
}
PATTERN_STATISTICS_STATUS = "INSUFFICIENT_SAMPLE"
"""
harmonic_detector.py — Alpha Buffalo v5
Harmonic Pattern Detector: ทุก Pattern + PRZ Zone
Sprint 1C

Patterns: Gartley, Bat, Butterfly, Crab, DeepCrab, Shark, Cypher, ABCD
Direction: Bullish / Bearish แยกทุก pattern
Output: PRZ Zone ส่งให้ micro_engine จับ entry บน 15M
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Any, Optional
from kivanc_vsaob import find_pivot_highs, find_pivot_lows, PivotPoint

# ── Tolerance สำหรับ Fibo ratio matching ──────────────────
TOLERANCE = 0.06   # ±6% ของ ratio

# ── Pattern Definitions ────────────────────────────────────
# format: {leg: (min_ratio, max_ratio)}
# XA, AB, BC, CD คือ Fibo retracement ของ leg ก่อนหน้า
HARMONIC_PATTERNS = {

    # ── Priority 1: เกิดบ่อย Reliable ────────────────────
    "Bullish_Gartley": {
        "direction": "BUY", "priority": 1, "reliability": "high",
        "XA": (0.558, 0.678),   # 0.618 ±tol
        "AB": (0.322, 0.442),   # 0.382 ±tol
        "BC": (0.826, 0.946),   # 0.886 ±tol
        "CD": (0.726, 0.846),   # 0.786 ±tol
    },
    "Bearish_Gartley": {
        "direction": "SELL", "priority": 1, "reliability": "high",
        "XA": (0.558, 0.678),
        "AB": (0.558, 0.678),
        "BC": (0.826, 0.946),
        "CD": (0.726, 0.846),
    },
    "Bullish_Bat": {
        "direction": "BUY", "priority": 1, "reliability": "high",
        "XA": (0.322, 0.442),   # 0.382 ±tol
        "AB": (0.322, 0.502),   # 0.382-0.500
        "BC": (0.826, 0.946),   # 0.886
        "CD": (0.826, 0.946),   # 0.886
    },
    "Bearish_Bat": {
        "direction": "SELL", "priority": 1, "reliability": "high",
        "XA": (0.440, 0.560),   # 0.500
        "AB": (0.440, 0.560),   # 0.500
        "BC": (0.826, 0.946),   # 0.886
        "CD": (0.826, 0.946),   # 0.886
    },
    "Bullish_ABCD": {
        "direction": "BUY", "priority": 1, "reliability": "unvalidated",
        "structure": "ABCD",
        "c_retrace": (0.382, 0.886),
        "bc_projection": (1.130, 2.618),
        "cd_ab": (0.940, 1.060),
    },
    "Bearish_ABCD": {
        "direction": "SELL", "priority": 1, "reliability": "unvalidated",
        "structure": "ABCD",
        "c_retrace": (0.382, 0.886),
        "bc_projection": (1.130, 2.618),
        "cd_ab": (0.940, 1.060),
    },

    # ── Priority 2: เกิดน้อย แต่ Move ใหญ่ ───────────────
    "Bullish_Butterfly": {
        "direction": "BUY", "priority": 2, "reliability": "very_high",
        "XA": (0.726, 0.846),   # 0.786
        "AB": (0.322, 0.442),   # 0.382
        "BC": (0.826, 0.946),   # 0.886
        "CD": (1.558, 1.678),   # 1.618
    },
    "Bearish_Butterfly": {
        "direction": "SELL", "priority": 2, "reliability": "very_high",
        "XA": (0.726, 0.846),
        "AB": (0.558, 0.678),
        "BC": (0.826, 0.946),
        "CD": (1.558, 1.678),
    },
    "Bullish_Crab": {
        "direction": "BUY", "priority": 2, "reliability": "very_high",
        "XA": (0.322, 0.442),   # 0.382
        "AB": (0.322, 0.502),   # 0.382-0.500
        "BC": (0.826, 0.946),   # 0.886
        "CD": (1.558, 1.678),   # 1.618
    },
    "Bearish_Crab": {
        "direction": "SELL", "priority": 2, "reliability": "very_high",
        "XA": (0.322, 0.442),
        "AB": (0.322, 0.502),
        "BC": (0.826, 0.946),
        "CD": (1.558, 1.678),
    },
    "Bullish_Cypher": {
        "direction": "BUY", "priority": 2, "reliability": "medium",
        "XA": (0.322, 0.502),   # 0.382-0.500 (flexible)
        "AB": (0.322, 0.502),
        "BC": (1.070, 1.190),   # 1.130
        "CD": (0.726, 0.846),   # 0.786
    },
    "Bearish_Cypher": {
        "direction": "SELL", "priority": 2, "reliability": "medium",
        "XA": (0.322, 0.502),
        "AB": (0.322, 0.502),
        "BC": (1.070, 1.190),
        "CD": (0.726, 0.846),
    },

    # ── Priority 3: หายาก ต้องมี Confluence สูง ──────────
    "Bullish_Shark": {
        "direction": "BUY", "priority": 3, "reliability": "medium",
        "XA": (0.386, 0.506),   # 0.446
        "AB": (0.386, 0.506),   # 0.446
        "BC": (1.070, 1.190),   # 1.130
        "CD": (0.826, 0.946),   # 0.886
    },
    "Bearish_Shark": {
        "direction": "SELL", "priority": 3, "reliability": "medium",
        "XA": (0.386, 0.506),
        "AB": (0.386, 0.506),
        "BC": (1.070, 1.190),
        "CD": (0.826, 0.946),
    },
    "Bullish_DeepCrab": {
        "direction": "BUY", "priority": 3, "reliability": "extreme",
        "XA": (0.826, 0.946),   # 0.886
        "AB": (0.322, 0.442),   # 0.382
        "BC": (0.826, 0.946),   # 0.886
        "CD": (2.558, 2.678),   # 2.618
    },
    "Bearish_DeepCrab": {
        "direction": "SELL", "priority": 3, "reliability": "extreme",
        "XA": (0.826, 0.946),
        "AB": (0.322, 0.442),
        "BC": (0.826, 0.946),
        "CD": (2.558, 2.678),
    },
}

# Score threshold ตาม priority
SCORE_THRESHOLD = {1: 3, 2: 5, 3: 7}


# XABC projection rules.  These are used before D exists.  XAB and ABC decide
# which families remain possible; XAD and BCD project the future D/PRZ ladder.
# A broad Extended_XABCD route is kept as an honest fallback when the geometry
# is clear but no strict named family matches both projection methods yet.
FORMING_PATTERN_RULES = {
    "Gartley": {
        "xab": (0.558, 0.678),
        "abc": (0.322, 0.946),
        "xad": (0.726, 0.846),
        "bcd": (1.212, 1.678),
        "priority": 1,
    },
    "Bat": {
        "xab": (0.322, 0.558),
        "abc": (0.322, 0.946),
        "xad": (0.826, 0.946),
        "bcd": (1.558, 2.678),
        "priority": 1,
    },
    "Butterfly": {
        "xab": (0.726, 0.846),
        "abc": (0.322, 0.946),
        "xad": (1.212, 1.678),
        "bcd": (1.558, 2.678),
        "priority": 2,
    },
    "Crab": {
        "xab": (0.322, 0.678),
        "abc": (0.322, 0.946),
        "xad": (1.558, 1.678),
        "bcd": (2.180, 3.678),
        "priority": 2,
    },
    "DeepCrab": {
        "xab": (0.826, 0.946),
        "abc": (0.322, 0.946),
        "xad": (1.558, 1.678),
        "bcd": (2.000, 3.678),
        "priority": 3,
    },
    "Extended_XABCD": {
        "xab": (0.300, 0.900),
        "abc": (0.300, 0.950),
        "xad": (1.130, 1.618),
        "bcd": (2.000, 3.618),
        "priority": 4,
        "fallback": True,
    },
}

# AB=CD is the four-point base structure embedded inside five-point harmonic
# families.  The reciprocal projection is the important constraint:
# C=0.618 -> BC projection=1.618, C=0.786 -> BC projection=1.272.
# Source of the ratio model: Scott Carney / HarmonicTrader AB=CD definition.
ABCD_C_RETRACE = (0.382, 0.886)
ABCD_BC_PROJECTION = (1.130, 2.618)


# ── Data Classes ──────────────────────────────────────────
@dataclass
class HarmonicPoint:
    """จุด X, A, B, C, D"""
    x: float; a: float; b: float; c: float; d: float
    x_idx: int; a_idx: int; b_idx: int; c_idx: int; d_idx: int


@dataclass(frozen=True)
class HarmonicProjectionPoint:
    """Four confirmed pivots used to project D before it is printed."""
    x: float; a: float; b: float; c: float
    x_idx: int; a_idx: int; b_idx: int; c_idx: int
    reversal_direction: str


@dataclass
class PRZZone:
    """Potential Reversal Zone"""
    pattern_name: str
    direction: str
    priority: int
    reliability: str
    prz_high: float
    prz_low: float
    prz_mid: float
    d_point: float
    confluence_score: int = 0
    label: str = ""
    x_point: float = 0.0
    a_point: float = 0.0
    b_point: float = 0.0
    c_point: float = 0.0
    x_idx: int = -1
    a_idx: int = -1
    b_idx: int = -1
    c_idx: int = -1
    d_idx: int = -1
    ratios: dict = field(default_factory=dict)

    def in_prz(self, price: float) -> bool:
        return self.prz_low <= price <= self.prz_high


# ── Ratio Check ───────────────────────────────────────────
def ratio_ok(value: float, lo: float, hi: float) -> bool:
    return lo <= value <= hi


def calc_ratio(leg1: float, leg2: float) -> float:
    """ratio = |leg2| / |leg1|"""
    if abs(leg1) < 0.0001:
        return 0.0
    return abs(leg2) / abs(leg1)


def xabcd_ratios(pts: HarmonicPoint) -> dict[str, float]:
    """Return the four ratios displayed by a five-point harmonic route."""
    xa = pts.a - pts.x
    ab = pts.b - pts.a
    bc = pts.c - pts.b
    cd = pts.d - pts.c
    ad = pts.d - pts.a
    return {
        "XAB": calc_ratio(xa, ab),
        "ABC": calc_ratio(ab, bc),
        "BCD": calc_ratio(bc, cd),
        "XAD": calc_ratio(xa, ad),
    }


def embedded_abcd_ratios(pts: HarmonicPoint) -> dict[str, float]:
    """Return reciprocal ratios for the A-B-C-D route inside X-A-B-C-D."""
    ab = pts.b - pts.a
    bc = pts.c - pts.b
    cd = pts.d - pts.c
    return {
        "BC_AB": calc_ratio(ab, bc),
        "CD_BC": calc_ratio(bc, cd),
        "CD_AB": calc_ratio(ab, cd),
    }


def xabc_ratios(pts: HarmonicProjectionPoint) -> dict[str, float]:
    """Ratios already knowable after X-A-B-C are confirmed."""
    xa = pts.a - pts.x
    ab = pts.b - pts.a
    bc = pts.c - pts.b
    return {
        "XAB": calc_ratio(xa, ab),
        "ABC": calc_ratio(ab, bc),
    }


def _project_price_range(
    anchor: float,
    leg_size: float,
    ratios: tuple[float, float],
    reversal_direction: str,
) -> tuple[float, float]:
    lo_ratio, hi_ratio = ratios
    if reversal_direction == "BUY":
        values = (anchor - leg_size * lo_ratio, anchor - leg_size * hi_ratio)
    else:
        values = (anchor + leg_size * lo_ratio, anchor + leg_size * hi_ratio)
    return min(values), max(values)


def _project_embedded_abcd(
    pts: HarmonicProjectionPoint,
    current_price: float,
    *,
    armed_distance_pct: float,
) -> Optional[dict[str, Any]]:
    """Project the A-B-C-D base route contained by an X-A-B-C route.

    AB and CD are projected as equal price legs.  The BC projection must be
    reciprocal to the C retracement, so both calculations converge on one D.
    Harmonic geometry projects a possible post-BOS target. It never grants
    entry permission; V4 owns the PRZ entry and structure independently
    promotes that existing position before this projection may become TP2.
    """
    ab_size = abs(pts.b - pts.a)
    bc_size = abs(pts.c - pts.b)
    if ab_size <= 0 or bc_size <= 0:
        return None

    c_retrace = bc_size / ab_size
    if not ratio_ok(c_retrace, *ABCD_C_RETRACE):
        return None
    reciprocal_projection = 1.0 / c_retrace
    if not ratio_ok(reciprocal_projection, *ABCD_BC_PROJECTION):
        return None

    if pts.reversal_direction == "BUY":
        d_point = pts.c - ab_size
    else:
        d_point = pts.c + ab_size

    prz_half = max(ab_size * 0.005, 0.50)
    prz_low = d_point - prz_half
    prz_high = d_point + prz_half
    in_prz = prz_low <= current_price <= prz_high
    distance = (
        0.0
        if in_prz
        else min(abs(current_price - prz_low), abs(current_price - prz_high))
    )
    distance_pct = distance / d_point if d_point else 999.0
    passed = bool(
        (pts.reversal_direction == "BUY" and current_price < prz_low)
        or (pts.reversal_direction == "SELL" and current_price > prz_high)
    )
    if in_prz:
        state = "ACTIVE"
    elif passed:
        state = "PASSED"
    elif distance_pct <= armed_distance_pct:
        state = "ARMED"
    else:
        state = "FORMING"

    current_cd = abs(float(current_price) - pts.c)
    pattern_side = "Bullish" if pts.reversal_direction == "BUY" else "Bearish"
    approach_direction = "SELL" if pts.reversal_direction == "BUY" else "BUY"
    return {
        "found": True,
        "pattern": f"{pattern_side}_ABCD",
        "family": "ABCD",
        "direction": pts.reversal_direction,
        "approach_direction": approach_direction,
        "state": state,
        "pattern_state": "FORMING_ABC_TO_D",
        "projection_mode": "FORMING_ABC_TO_D",
        "x": pts.x,
        "a": pts.a,
        "b": pts.b,
        "c": pts.c,
        "d_point": d_point,
        "x_idx": pts.x_idx,
        "a_idx": pts.a_idx,
        "b_idx": pts.b_idx,
        "c_idx": pts.c_idx,
        "ratios": {
            "BC_AB": c_retrace,
            "TARGET_CD_AB": 1.0,
            "TARGET_CD_BC": reciprocal_projection,
            "CURRENT_CD_AB": current_cd / ab_size,
            "CURRENT_CD_BC": current_cd / bc_size,
        },
        "current_xad": 0.0,
        "current_bcd": current_cd / bc_size,
        "current_cd_ab": current_cd / ab_size,
        "next_xad": 0.0,
        "prz_low": prz_low,
        "prz_high": prz_high,
        "distance": distance,
        "distance_pct": distance_pct,
        "in_prz": in_prz,
        "priority": 1,
        "fallback": False,
        "execution_authority": False,
        "entry_authority": False,
        "target_authority_after_bos": True,
        "ratio_model": "RECIPROCAL_AB_EQUALS_CD",
        "confirmation_required": [
            "PRZ_REVERSAL_CANDLE",
            "DIRECTIONAL_CANDLE_BREAKOUT",
            "HTF_STRUCTURE_ALIGNMENT",
        ],
        "stop_reference": "CONFIRMATION_CANDLE_EXTREME",
        # The clip's bearish example targets C first, then A. Mirrored for BUY.
        "tp1": pts.c,
        "tp2": pts.a,
        "tp3": 0.0,
        "statistics_status": PATTERN_STATISTICS_STATUS,
        "statistics_sample_size": 0,
        "statistics_source": "NONE",
        "source": "harmonic_detector.project_abcd",
    }


def project_harmonic_from_xabc(
    pts: HarmonicProjectionPoint,
    current_price: float,
    *,
    armed_distance_pct: float = 0.005,
) -> list[dict[str, Any]]:
    """Project every still-possible D family from four confirmed pivots.

    The output is a ladder, not a forced single label.  When price passes a
    shallow completion (for example Gartley 0.786), that candidate becomes
    PASSED and the next deeper Bat/Butterfly/Crab projection remains visible.
    """
    ratios = xabc_ratios(pts)
    xab = ratios["XAB"]
    abc = ratios["ABC"]
    xa_size = abs(pts.a - pts.x)
    bc_size = abs(pts.c - pts.b)
    if xa_size <= 0 or bc_size <= 0:
        return []

    approach_direction = "SELL" if pts.reversal_direction == "BUY" else "BUY"
    pattern_side = "Bullish" if pts.reversal_direction == "BUY" else "Bearish"
    current_xad = abs(float(current_price) - pts.a) / xa_size
    current_bcd = abs(float(current_price) - pts.c) / bc_size
    candidates: list[dict[str, Any]] = []

    embedded_abcd = _project_embedded_abcd(
        pts,
        current_price,
        armed_distance_pct=armed_distance_pct,
    )
    if embedded_abcd is not None:
        candidates.append(embedded_abcd)

    for family, rule in FORMING_PATTERN_RULES.items():
        if not ratio_ok(xab, *rule["xab"]) or not ratio_ok(abc, *rule["abc"]):
            continue

        xa_low, xa_high = _project_price_range(
            pts.a, xa_size, rule["xad"], pts.reversal_direction
        )
        bc_low, bc_high = _project_price_range(
            pts.c, bc_size, rule["bcd"], pts.reversal_direction
        )
        prz_low = max(xa_low, bc_low)
        prz_high = min(xa_high, bc_high)
        if prz_low > prz_high:
            # XAD and BCD do not converge, so it is not a valid PRZ candidate.
            continue

        prz_mid = (prz_low + prz_high) / 2.0
        in_prz = prz_low <= current_price <= prz_high
        distance = (
            0.0
            if in_prz
            else min(abs(current_price - prz_low), abs(current_price - prz_high))
        )
        distance_pct = distance / prz_mid if prz_mid else 999.0
        passed = bool(
            (pts.reversal_direction == "BUY" and current_price < prz_low)
            or (pts.reversal_direction == "SELL" and current_price > prz_high)
        )
        if in_prz:
            status = "ACTIVE"
        elif passed:
            status = "PASSED"
        elif distance_pct <= armed_distance_pct:
            status = "ARMED"
        else:
            status = "FORMING"

        xad_lo, xad_hi = rule["xad"]
        if current_xad < xad_lo:
            next_xad = xad_lo
        elif current_xad < xad_hi:
            next_xad = xad_hi
        else:
            next_xad = 0.0

        candidates.append(
            {
                "found": True,
                "pattern": f"{pattern_side}_{family}",
                "family": family,
                "direction": pts.reversal_direction,
                "approach_direction": approach_direction,
                "state": status,
                "pattern_state": "FORMING_XABC",
                "x": pts.x,
                "a": pts.a,
                "b": pts.b,
                "c": pts.c,
                "d_point": prz_mid,
                "x_idx": pts.x_idx,
                "a_idx": pts.a_idx,
                "b_idx": pts.b_idx,
                "c_idx": pts.c_idx,
                "ratios": {
                    "XAB": xab,
                    "ABC": abc,
                    "CURRENT_XAD": current_xad,
                    "CURRENT_BCD": current_bcd,
                    "TARGET_XAD_MIN": xad_lo,
                    "TARGET_XAD_MAX": xad_hi,
                    "TARGET_BCD_MIN": rule["bcd"][0],
                    "TARGET_BCD_MAX": rule["bcd"][1],
                },
                "current_xad": current_xad,
                "current_bcd": current_bcd,
                "next_xad": next_xad,
                "prz_low": prz_low,
                "prz_high": prz_high,
                "distance": distance,
                "distance_pct": distance_pct,
                "in_prz": in_prz,
                "priority": int(rule.get("priority", 9)),
                "fallback": bool(rule.get("fallback", False)),
                # Named and fallback geometry are target context only.
                "execution_authority": False,
                "entry_authority": False,
                "target_authority_after_bos": True,
                "ratio_model": "XABCD_PRZ_CONVERGENCE",
                "confirmation_required": [
                    "PRZ_REVERSAL_CANDLE",
                    "DIRECTIONAL_CANDLE_BREAKOUT",
                    "HTF_STRUCTURE_ALIGNMENT",
                ],
                "stop_reference": "CONFIRMATION_CANDLE_EXTREME",
                "statistics_status": PATTERN_STATISTICS_STATUS,
                "statistics_sample_size": 0,
                "statistics_source": "NONE",
                "source": "harmonic_detector.project_xabc",
            }
        )

    state_rank = {"ACTIVE": 0, "ARMED": 1, "FORMING": 2, "PASSED": 3}
    candidates.sort(
        key=lambda item: (
            state_rank.get(str(item.get("state")), 9),
            1 if item.get("fallback") else 0,
            int(item.get("priority", 9)),
            float(item.get("distance_pct", 999.0)),
        )
    )

    # Morphing is a measured candidate transition, never a hard-coded story
    # such as Gartley->Butterfly. A deeper candidate is exposed only when the
    # current XABC geometry supports it and price has passed an earlier PRZ.
    passed_named = [
        str(item.get("family", ""))
        for item in candidates
        if item.get("state") == "PASSED" and not item.get("fallback", False)
    ]
    for item in candidates:
        if item.get("state") != "PASSED" and passed_named:
            item["morph_state"] = "ADVANCED_AFTER_PASSED_PRZ"
            item["morph_from"] = list(passed_named)
            item["morph_to"] = str(item.get("family", ""))
            item["morph_reason"] = (
                "PRICE_PASSED_PRIOR_PRZ_AND_CURRENT_GEOMETRY_SUPPORTS_CANDIDATE"
            )
        else:
            item["morph_state"] = "BASE_PROJECTION"
            item["morph_from"] = []
            item["morph_to"] = str(item.get("family", ""))
            item["morph_reason"] = "NONE"
    return candidates


def select_harmonic_projection(
    candidates: list[dict[str, Any]],
) -> Optional[dict[str, Any]]:
    """Select the next named D candidate without letting fallback flip bias."""
    strict_actionable = [
        candidate
        for candidate in candidates
        if candidate.get("state") != "PASSED" and not candidate.get("fallback", False)
    ]
    if strict_actionable:
        return strict_actionable[0]
    actionable = [candidate for candidate in candidates if candidate.get("state") != "PASSED"]
    if actionable:
        return actionable[0]
    return candidates[0] if candidates else None


# ── Pattern Validation ────────────────────────────────────
def validate_xabcd(pts: HarmonicPoint, pattern: dict) -> bool:
    """ตรวจ XABCD ratios ทุก leg"""
    if pattern.get("structure") == "ABCD":
        ratios = embedded_abcd_ratios(pts)
        direction = str(pattern.get("direction", "NONE")).upper()
        if direction == "BUY":
            direction_ok = pts.a > pts.b and pts.c > pts.b and pts.d < pts.c
        elif direction == "SELL":
            direction_ok = pts.a < pts.b and pts.c < pts.b and pts.d > pts.c
        else:
            direction_ok = False
        return bool(
            direction_ok
            and ratio_ok(ratios["BC_AB"], *pattern["c_retrace"])
            and ratio_ok(ratios["CD_BC"], *pattern["bc_projection"])
            and ratio_ok(ratios["CD_AB"], *pattern["cd_ab"])
        )

    ratios = xabcd_ratios(pts)
    checks = []

    if "XA" in pattern:
        checks.append(ratio_ok(ratios["XAB"], *pattern["XA"]))

    if "AB" in pattern:
        checks.append(ratio_ok(ratios["ABC"], *pattern["AB"]))

    if "BC" in pattern:
        checks.append(ratio_ok(ratios["BCD"], *pattern["BC"]))

    if "CD" in pattern:
        checks.append(ratio_ok(ratios["XAD"], *pattern["CD"]))

    return all(checks) if checks else False


def classify_symmetric_xabcd_route(pts: HarmonicPoint):
    """Recognize the mirrored M/W route even when it has no strict pattern name.

    This captures charts where X≈D and A≈C, with a meaningful B retracement
    and CD expansion. It is context-only and never bypasses the entry trigger.
    """
    ratios = xabcd_ratios(pts)
    xa_range = abs(pts.a - pts.x)
    if xa_range < 0.0001:
        return None
    ac_symmetry = abs(pts.c - pts.a) / xa_range
    xd_symmetry = abs(pts.d - pts.x) / xa_range
    route_ok = (
        ac_symmetry <= 0.08
        and xd_symmetry <= 0.08
        and 0.30 <= ratios["XAB"] <= 0.70
        and 1.20 <= ratios["BCD"] <= 2.618
        and 0.90 <= ratios["XAD"] <= 1.08
    )
    if not route_ok:
        return None

    if pts.x > pts.a and pts.d > pts.c:
        direction = "SELL"
        name = "Bearish_Symmetric_XABCD"
    elif pts.x < pts.a and pts.d < pts.c:
        direction = "BUY"
        name = "Bullish_Symmetric_XABCD"
    else:
        return None
    return name, {
        "direction": direction,
        "priority": 3,
        "reliability": "context",
    }


# ── PRZ Builder ───────────────────────────────────────────
def build_prz(pts: HarmonicPoint, pattern_name: str, pattern: dict) -> PRZZone:
    """สร้าง PRZ zone จาก D point"""
    direction = pattern["direction"]
    d = pts.d
    xa_range = (
        abs(pts.b - pts.a)
        if pattern.get("structure") == "ABCD"
        else abs(pts.a - pts.x)
    )

    # PRZ width = 0.5% ของ XA range หรือ minimum 0.5 USD
    prz_half  = max(xa_range * 0.005, 0.50)

    prz_high = d + prz_half
    prz_low  = d - prz_half
    ratios = (
        embedded_abcd_ratios(pts)
        if pattern.get("structure") == "ABCD"
        else xabcd_ratios(pts)
    )

    label = (
        f"🦋 {pattern_name} | D:{d:.2f} "
        f"| PRZ:{prz_low:.2f}-{prz_high:.2f}"
        f"| {direction} | "
        + (
            f"BC/AB:{ratios['BC_AB']:.3f} CD/BC:{ratios['CD_BC']:.3f}"
            if pattern.get("structure") == "ABCD"
            else (
                f"XAB:{ratios['XAB']:.3f}"
                f" BCD:{ratios['BCD']:.3f} XAD:{ratios['XAD']:.3f}"
            )
        )
    )

    return PRZZone(
        pattern_name=pattern_name,
        direction=direction,
        priority=pattern.get("priority", 3),
        reliability=pattern.get("reliability", "medium"),
        prz_high=prz_high,
        prz_low=prz_low,
        prz_mid=d,
        d_point=d,
        x_point=pts.x,
        a_point=pts.a,
        b_point=pts.b,
        c_point=pts.c,
        x_idx=pts.x_idx,
        a_idx=pts.a_idx,
        b_idx=pts.b_idx,
        c_idx=pts.c_idx,
        d_idx=pts.d_idx,
        ratios=ratios,
        label=label,
    )


# ── Swing Point Extractor ─────────────────────────────────
def extract_swings(df: pd.DataFrame, n: int = 3) -> list[PivotPoint]:
    """รวม Pivot High และ Low เรียงตาม index"""
    highs = find_pivot_highs(df, n)
    lows  = find_pivot_lows(df, n)
    all_p = highs + lows
    all_p.sort(key=lambda p: p.index)
    return all_p


def make_xabc(
    swings: list[PivotPoint],
    i: int,
) -> Optional[HarmonicProjectionPoint]:
    """Build one non-repainting XABC route from four confirmed pivots."""
    if i + 3 >= len(swings):
        return None
    points = swings[i:i + 4]
    kinds = [str(point.kind).lower() for point in points]
    if not all(kinds[j] != kinds[j + 1] for j in range(3)):
        return None
    if kinds == ["low", "high", "low", "high"]:
        reversal_direction = "BUY"
    elif kinds == ["high", "low", "high", "low"]:
        reversal_direction = "SELL"
    else:
        return None
    return HarmonicProjectionPoint(
        x=float(points[0].price),
        a=float(points[1].price),
        b=float(points[2].price),
        c=float(points[3].price),
        x_idx=int(points[0].index),
        a_idx=int(points[1].index),
        b_idx=int(points[2].index),
        c_idx=int(points[3].index),
        reversal_direction=reversal_direction,
    )


def scan_forming_harmonic(
    df: pd.DataFrame,
    pivot_n: int = 3,
) -> dict[str, Any]:
    """Return the latest XABC projection and its complete D-level ladder."""
    if df is None or getattr(df, "empty", True) or "close" not in df:
        return {"found": False, "state": "NO_DATA", "candidates": []}
    swings = extract_swings(df, pivot_n)
    if len(swings) < 4:
        return {"found": False, "state": "NO_XABC", "candidates": []}

    current_price = float(df["close"].iloc[-1])
    # Only the newest valid XABC route is authoritative. Older routes must not
    # keep bias locked after a newer C pivot has formed.
    for i in range(len(swings) - 4, -1, -1):
        points = make_xabc(swings, i)
        if not points:
            continue
        candidates = project_harmonic_from_xabc(points, current_price)
        if not candidates:
            continue

        selected = select_harmonic_projection(candidates)
        if selected is None:
            continue
        ladder = sorted(
            candidates,
            key=lambda item: (
                float(item["prz_high"])
                if points.reversal_direction == "BUY"
                else -float(item["prz_low"])
            ),
            reverse=points.reversal_direction == "BUY",
        )
        return {
            **selected,
            "found": True,
            "candidates": ladder,
            "selected_pattern": selected["pattern"],
            "latest_c_idx": points.c_idx,
        }

    return {"found": False, "state": "NO_VALID_XABC", "candidates": []}


def make_xabcd(swings: list[PivotPoint], i: int) -> Optional[HarmonicPoint]:
    """สร้าง XABCD จาก 5 swing points"""
    if i + 4 >= len(swings):
        return None
    pts = swings[i:i+5]
    # ตรวจ alternating High/Low
    kinds = [p.kind for p in pts]
    valid = all(kinds[j] != kinds[j+1] for j in range(4))
    if not valid:
        return None
    return HarmonicPoint(
        x=pts[0].price, a=pts[1].price,
        b=pts[2].price, c=pts[3].price, d=pts[4].price,
        x_idx=pts[0].index, a_idx=pts[1].index,
        b_idx=pts[2].index, c_idx=pts[3].index, d_idx=pts[4].index,
    )


# ── Main Detector ─────────────────────────────────────────
class HarmonicDetector:
    def __init__(self, pivot_n: int = 3):
        self.pivot_n = pivot_n
        self.detected: list[PRZZone] = []

    def scan(self, df: pd.DataFrame) -> list[PRZZone]:
        """
        สแกน df หา Harmonic Patterns ทั้งหมด
        คืน list ของ PRZZone เรียงตาม priority
        """
        self.detected = []
        swings = extract_swings(df, self.pivot_n)

        if len(swings) < 5:
            return []

        current_price = float(df["close"].iloc[-1])

        for i in range(len(swings) - 4):
            pts = make_xabcd(swings, i)
            if not pts:
                continue

            named_pattern_found = False
            for name, pattern in HARMONIC_PATTERNS.items():
                if validate_xabcd(pts, pattern):
                    named_pattern_found = True
                    prz = build_prz(pts, name, pattern)
                    # คำนวณ score
                    score = self._score(prz, current_price, pattern)
                    prz.confluence_score = score
                    threshold = SCORE_THRESHOLD.get(prz.priority, 5)
                    if score >= threshold:
                        self.detected.append(prz)

            # A symmetric M/W route is useful Newday context even when its
            # ratios do not fit one strict named pattern. It is deliberately
            # not passed through a score bypass or an execution path.
            if not named_pattern_found:
                structural_route = classify_symmetric_xabcd_route(pts)
                if structural_route:
                    route_name, route_pattern = structural_route
                    prz = build_prz(pts, route_name, route_pattern)
                    prz.confluence_score = self._score(prz, current_price, route_pattern)
                    self.detected.append(prz)

        # เรียงตาม priority + score
        self.detected.sort(key=lambda z: (z.priority, -z.confluence_score))
        # dedup — เอา PRZ ที่ซ้ำกันออก
        self.detected = self._dedup(self.detected)
        return self.detected

    def _score(self, prz: PRZZone, current_price: float, pattern: dict) -> int:
        score = 0
        # ราคาใกล้ PRZ
        if prz.in_prz(current_price):
            score += 4
        elif abs(current_price - prz.prz_mid) / prz.prz_mid < 0.005:
            score += 2
        # Priority bonus
        score += (4 - prz.priority)
        # Reliability bonus
        rel_map = {"extreme": 3, "very_high": 2, "high": 1, "medium": 0}
        score += rel_map.get(prz.reliability, 0)
        return score

    def _dedup(self, zones: list[PRZZone], threshold: float = 1.0) -> list[PRZZone]:
        """ลบ PRZ ที่ mid ใกล้กันเกิน threshold USD"""
        result = []
        for z in zones:
            overlap = any(abs(z.prz_mid - r.prz_mid) < threshold for r in result)
            if not overlap:
                result.append(z)
        return result

    def get_active_prz(self, current_price: float) -> list[PRZZone]:
        """คืน PRZ ที่ราคาปัจจุบันอยู่ใน zone"""
        return [z for z in self.detected if z.in_prz(current_price)]

    def summary(self) -> str:
        if not self.detected:
            return "🦋 No Harmonic Pattern detected"
        lines = ["🦋 Harmonic Patterns Detected:"]
        for z in self.detected[:5]:  # top 5
            lines.append(
                f"  [{z.priority}] {z.pattern_name} | {z.direction}"
                f" | PRZ:{z.prz_low:.2f}-{z.prz_high:.2f}"
                f" | Score:{z.confluence_score}"
            )
        return "\n".join(lines)


# ── Singleton ──────────────────────────────────────────────
harmonic_detector = HarmonicDetector(pivot_n=3)


def run_harmonic(df: pd.DataFrame) -> list[PRZZone]:
    """Entry point สำหรับ signal_composer เรียกใช้"""
    return harmonic_detector.scan(df)


def get_active_prz(current_price: float) -> list[PRZZone]:
    """คืน PRZ ที่ active ณ ราคาปัจจุบัน"""
    return harmonic_detector.get_active_prz(current_price)


# ═══════════════════════════════════════════════════════
# POST-BOS PRZ CALCULATOR (v5.4)
# Wired into scenario_scanner.py's forming-harmonic invalidation path --
# see FINAL_RUNTIME_MAP.md's "PRZ cycle after BOS" section. Kept as a
# single copy (this file previously had three byte-identical definitions
# of both functions below; only the last one was ever live, per Python's
# override-by-last-definition semantics -- the earlier two were dead code
# and have been removed).
# ═══════════════════════════════════════════════════════

def recalculate_prz_after_bos(L, H, HL, current_price, direction="BUY"):
    """
    คำนวณ PRZ ใหม่หลังจาก BOS เกิดขึ้น
    X = L, A = H, B = HL, C = current_price
    Returns: (PRZZone, pattern_name)
    """
    if L is None or H is None or HL is None:
        return None, "Unknown"
    
    xa = H - L
    ab = H - HL
    if xa <= 0:
        return None, "Unknown"
    
    ab_xa = ab / xa
    
    pattern_name = "Unknown"
    xa_retrace = 0.618
    
    if 0.38 <= ab_xa <= 0.50:
        pattern_name = "Bat"
        xa_retrace = 0.886
    elif 0.58 <= ab_xa <= 0.65:
        pattern_name = "Gartley"
        xa_retrace = 0.786
    elif 0.75 <= ab_xa <= 0.82:
        pattern_name = "Butterfly"
        xa_retrace = 1.272
    elif 1.13 <= ab_xa <= 1.618:
        # 🦈 Shark: B ทะลุ X → Stop Hunt → Reversal ที่ 0.886 XA
        pattern_name = "Shark"
        xa_retrace = 0.886
    elif 0.38 <= ab_xa <= 0.62:
        pattern_name = "Crab"
        xa_retrace = 1.618
    
    if direction == "BUY":
        d_price = L + xa * xa_retrace
    else:
        d_price = H - xa * xa_retrace
    
    prz = PRZZone(
        pattern_name=pattern_name,
        direction=direction,
        priority=3,
        reliability="HIGH" if 0.58 <= ab_xa <= 0.65 else "MEDIUM",
        prz_high=d_price * 1.005 if direction == "BUY" else d_price * 1.005,
        prz_low=d_price * 0.995 if direction == "BUY" else d_price * 0.995,
        prz_mid=d_price,
        d_point=d_price,
        confluence_score=3,
        label=f"{pattern_name} PRZ @ {d_price:.2f}"
    )
    
    return prz, pattern_name


def calc_fib_extension(L, H, HL, direction="BUY"):
    """
    คำนวณ Fibonacci Extension หลัง BOS
    """
    if L is None or H is None or HL is None:
        return {}
    
    xa = abs(H - L)
    retrace = abs(H - HL)
    
    if xa <= 0:
        return {}
    
    if direction == "BUY":
        return {
            "0.000": L,
            "0.618": L + xa * 0.618,
            "0.786": L + xa * 0.786,
            "1.000": H,
            "1.272": H + retrace * 0.272,
            "1.618": H + retrace * 0.618,
            "2.000": H + retrace * 1.000,
            "2.272": H + retrace * 1.272,
            "2.618": H + retrace * 1.618,
        }
    else:
        return {
            "0.000": H,
            "0.618": H - xa * 0.618,
            "0.786": H - xa * 0.786,
            "1.000": L,
            "1.272": L - retrace * 0.272,
            "1.618": L - retrace * 0.618,
            "2.000": L - retrace * 1.000,
            "2.272": L - retrace * 1.272,
            "2.618": L - retrace * 1.618,
        }

# ═══════════════════════════════════════════════════════
# M15 NOISE FILTERS (v5.4)
# ═══════════════════════════════════════════════════════

def validate_timeframe_noise(pts, df, tf_name="1H"):
    """
    กรอง Noise สำหรับ TF เล็ก (M15)
    Returns: (is_valid, reason)
    """
    if tf_name != "15M":
        return True, ""
    
    # 1. Price Range Filter: XA > 2x ATR(14)
    atr = calculate_atr(df, 14)
    xa_range = abs(pts.A.price - pts.X.price)
    if xa_range < atr * 2:
        return False, f"XA too small ({xa_range:.1f} < {atr*2:.1f} ATR)"
    
    # 2. Bar Count Filter: X→D = 15-80 bars
    bars_xd = abs(pts.D.bar_index - pts.X.bar_index)
    if bars_xd < 15:
        return False, f"Too fast ({bars_xd} bars < 15)"
    if bars_xd > 80:
        return False, f"Too slow ({bars_xd} bars > 80)"
    
    # 3. Time Symmetry: AB bars ≈ CD bars (0.618-1.618)
    bars_ab = abs(pts.B.bar_index - pts.A.bar_index)
    bars_cd = abs(pts.D.bar_index - pts.C.bar_index)
    if bars_ab > 0 and bars_cd > 0:
        ratio = bars_ab / bars_cd
        if ratio < 0.618 or ratio > 1.618:
            return False, f"Time asymmetry (AB/CD={ratio:.2f})"
    
    return True, ""

def calculate_atr(df, period=14):
    """คำนวณ ATR"""
    high, low, close = df['high'], df['low'], df['close'].shift(1)
    tr1 = high - low
    tr2 = (high - close).abs()
    tr3 = (low - close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def get_pivot_for_timeframe(tf_name):
    """
    คืนค่า pivot_n ที่เหมาะสมสำหรับแต่ละ TF
    H1: 5 (1 แท่ง = 1 ชม)
    M15: 10-15 (กรองสวิงเล็ก)
    H4: 3 (สวิงใหญ่)
    """
    pivot_map = {
        "1H": 5,
        "4H": 3,
        "15M": 12,  # กรองสวิงเล็ก — ต้อง ≥ 12 แท่ง (3 ชม)
    }
    return pivot_map.get(tf_name, 5)

# ============================================================
# 🆕 PHASE 6A: Lower Threshold + Forming Status
# ============================================================

HARMONIC_THRESHOLD = 0.5       # ลดจาก 1.0 → 0.5 (จับ pattern มากขึ้น)
MIN_PATTERN_SCORE = 0.4        # ลด min_score (จับ pattern ที่ยังไม่สมบูรณ์)
FORMING_EARLY_ALERT = True     # รายงาน "forming" ก่อนถึงจุด D

PATTERN_FORMING_MAP = {
    "gartley": "Gartley forming → PRZ {prz_range}",
    "bat": "Bat forming → PRZ {prz_range}",
    "crab": "Crab forming → PRZ {prz_range}",
    "butterfly": "Butterfly forming → PRZ {prz_range}",
    "cypher": "Cypher forming → PRZ {prz_range}",
}

def get_pattern_status(pattern_name: str, completion: float) -> str:
    """
    ตรวจสอบสถานะ Pattern
    completion < 0.8 → "forming"
    completion >= 0.8 → "complete"
    """
    if completion >= 0.8:
        return "COMPLETE"
    elif completion >= 0.5:
        return "FORMING"
    return "EARLY"

def get_prz_alert(pattern_name: str, prz_low: float, prz_high: float, status: str) -> str:
    """สร้างข้อความแจ้งเตือน PRZ"""
    prz_range = f"{prz_low:.0f}-{prz_high:.0f}"
    if status == "FORMING":
        return PATTERN_FORMING_MAP.get(pattern_name.lower(), "Pattern forming").format(prz_range=prz_range)
    elif status == "COMPLETE":
        return f"{pattern_name.upper()} COMPLETE! PRZ at {prz_range}"
    return f"Early {pattern_name} detection near {prz_range}"

# ============================================================
# 🆕 PHASE 6B: Harmonic Active Status for Bucket F Bypass
# ============================================================

def is_harmonic_active(prz_list: list, current_price: float, tolerance: float = 0.02) -> dict:
    """
    ตรวจสอบว่า Harmonic Pattern active อยู่หรือไม่
    ใช้สำหรับ Bucket F Ultimate Confluence Bypass
    
    Returns:
        {
            "active": True/False,
            "pattern_name": "Gartley/Bat/Crab/Butterfly/Cypher",
            "prz_low": float,
            "prz_high": float,
            "distance_pct": float,  # ราคาห่างจาก PRZ กี่ %
            "bypass": True/False  # Should Bucket F lower threshold?
        }
    """
    if not prz_list:
        return {"active": False, "bypass": False}
    
    # Find nearest PRZ
    nearest = None
    nearest_dist = float('inf')
    
    for prz in prz_list:
        prz_low = prz.get('prz_low', prz.get('low', 0))
        prz_high = prz.get('prz_high', prz.get('high', 0))
        prz_mid = (prz_low + prz_high) / 2
        
        dist = abs(current_price - prz_mid) / current_price
        
        if dist < nearest_dist:
            nearest_dist = dist
            nearest = prz
    
    if nearest is None:
        return {"active": False, "bypass": False}
    
    # Check if price is near PRZ (within 2%)
    is_near = nearest_dist <= tolerance
    
    # Check if pattern is forming or complete
    pattern_name = nearest.get('pattern', nearest.get('name', 'Unknown'))
    completion = nearest.get('completion', nearest.get('confidence', 0.5))
    
    # Bypass: active if near PRZ AND (forming OR complete)
    bypass = is_near and completion >= 0.4  # 40% forming ก็พอ
    
    return {
        "active": is_near,
        "pattern_name": pattern_name,
        "prz_low": nearest.get('prz_low', 0),
        "prz_high": nearest.get('prz_high', 0),
        "distance_pct": round(nearest_dist * 100, 2),
        "completion": round(completion * 100, 0),
        "bypass": bypass
    }


def get_active_harmonic_count(prz_list: list, current_price: float) -> int:
    """นับจำนวน Harmonic Pattern ที่ active ใกล้ราคาปัจจุบัน"""
    if not prz_list:
        return 0
    
    count = 0
    for prz in prz_list:
        prz_low = prz.get('prz_low', prz.get('low', 0))
        prz_high = prz.get('prz_high', prz.get('high', 0))
        prz_mid = (prz_low + prz_high) / 2
        
        dist = abs(current_price - prz_mid) / current_price
        if dist <= 0.02:  # Within 2%
            count += 1
    
    return count

# ============================================================
# 🆕 Bucket F Bridge: harmonic → score_manager
# ============================================================

def get_harmonic_bypass_for_bucket_f(current_price: float, df_4h=None, df_1h=None) -> dict:
    """
    Bridge function: harmonic_detector → score_manager (Bucket F)
    
    เรียกจาก signal_composer หรือ score_manager
    Returns dict พร้อมใช้ใน Bucket F
    """
    try:
        # Try to get active PRZ from harmonic detector
        from harmonic_detector import get_active_prz, is_harmonic_active
        
        prz_list = get_active_prz(current_price)
        if prz_list:
            result = is_harmonic_active(prz_list, current_price)
            return result
    except ImportError:
        pass
    except Exception:
        pass
    
    return {"active": False, "bypass": False}
