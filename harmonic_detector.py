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
from typing import Optional
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
        "direction": "BUY", "priority": 1, "reliability": "high",
        "AB": (0.558, 0.678),   # 0.618
        "BC": (0.558, 0.678),   # 0.618
        "CD": (1.212, 1.332),   # 1.272
    },
    "Bearish_ABCD": {
        "direction": "SELL", "priority": 1, "reliability": "high",
        "AB": (0.558, 0.678),
        "BC": (0.558, 0.678),
        "CD": (1.212, 1.332),
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


# ── Data Classes ──────────────────────────────────────────
@dataclass
class HarmonicPoint:
    """จุด X, A, B, C, D"""
    x: float; a: float; b: float; c: float; d: float
    x_idx: int; a_idx: int; b_idx: int; c_idx: int; d_idx: int


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


# ── Pattern Validation ────────────────────────────────────
def validate_xabcd(pts: HarmonicPoint, pattern: dict) -> bool:
    """ตรวจ XABCD ratios ทุก leg"""
    XA = pts.a - pts.x
    AB = pts.b - pts.a
    BC = pts.c - pts.b
    CD = pts.d - pts.c

    checks = []

    if "XA" in pattern:
        r = calc_ratio(XA, AB)
        checks.append(ratio_ok(r, *pattern["XA"]))

    if "AB" in pattern:
        r = calc_ratio(AB, BC)
        checks.append(ratio_ok(r, *pattern["AB"]))

    if "BC" in pattern:
        r = calc_ratio(BC, CD)
        checks.append(ratio_ok(r, *pattern["BC"]))

    if "CD" in pattern:
        r = calc_ratio(XA, CD)
        checks.append(ratio_ok(r, *pattern["CD"]))

    return all(checks) if checks else False


# ── PRZ Builder ───────────────────────────────────────────
def build_prz(pts: HarmonicPoint, pattern_name: str, pattern: dict) -> PRZZone:
    """สร้าง PRZ zone จาก D point"""
    direction = pattern["direction"]
    d = pts.d
    xa_range  = abs(pts.a - pts.x)

    # PRZ width = 0.5% ของ XA range หรือ minimum 0.5 USD
    prz_half  = max(xa_range * 0.005, 0.50)

    prz_high = d + prz_half
    prz_low  = d - prz_half

    label = (
        f"🦋 {pattern_name} | D:{d:.2f} "
        f"| PRZ:{prz_low:.2f}-{prz_high:.2f}"
        f"| {direction}"
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

            for name, pattern in HARMONIC_PATTERNS.items():
                if validate_xabcd(pts, pattern):
                    prz = build_prz(pts, name, pattern)
                    # คำนวณ score
                    score = self._score(prz, current_price, pattern)
                    prz.confluence_score = score
                    threshold = SCORE_THRESHOLD.get(prz.priority, 5)
                    if score >= threshold:
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
