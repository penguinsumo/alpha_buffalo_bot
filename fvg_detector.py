"""
fvg_detector.py — Alpha Buffalo v5.2 (Sprint Clean)
================================================
Fair Value Gap (FVG) + Structure Shift Engine

Changes from v5.1:
  [FIX] volume fail-open → fail-closed (False แทน True)
  [FIX] iloc[-1] lookahead → ใช้ confirmed candle iloc[-2]
  [FIX] active_fvgs เพิ่ม max_age expiry (50 แท่ง)
  [FIX] fvg ไม่ return score ตัวเลข — ชัดเจนว่า verdict เท่านั้น
        (score translation อยู่ใน score_manager.py)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════

FVG_MIN_SIZE_RATIO  = 0.003
FVG_LOOKBACK        = 10
VOLUME_HIGH_SIGMA   = 1.5
BODY_OUTSIDE_RATIO  = 0.5
HUNT_WICK_RATIO     = 2.0
MSS_MIN_BODY_RATIO  = 0.6
FVG_MAX_AGE_BARS    = 50     # [NEW] FVG หมดอายุหลัง 50 แท่ง


# ════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════

@dataclass
class FVGZone:
    direction:  str
    top:        float
    bottom:     float
    size:       float
    formed_idx: int
    filled:     bool = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    def is_price_inside(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def is_expired(self, current_idx: int) -> bool:
        """[NEW] FVG หมดอายุถ้าผ่านมานานเกิน FVG_MAX_AGE_BARS"""
        return (current_idx - self.formed_idx) > FVG_MAX_AGE_BARS

    def check_filled(self, price: float) -> bool:
        if self.direction == "bullish" and price <= self.midpoint:
            self.filled = True
        elif self.direction == "bearish" and price >= self.midpoint:
            self.filled = True
        return self.filled


@dataclass
class StructureShiftResult:
    verdict:    str             # "HUNT" | "MSS" | "WAIT" | "NONE"
    fvg:        Optional[FVGZone] = None
    confidence: int = 0
    reason:     str = ""
    # [NEW] ชัดเจนว่าไม่มี score — score อยู่ใน score_manager
    has_volume: bool = False    # flag บอก signal_engine ว่า volume data มีจริง

    @property
    def is_hunt(self) -> bool:
        return self.verdict == "HUNT"

    @property
    def is_mss(self) -> bool:
        return self.verdict == "MSS"

    @property
    def entry_zone(self) -> Optional[tuple]:
        if self.is_mss and self.fvg:
            return (self.fvg.bottom, self.fvg.top)
        return None


# ════════════════════════════════════════════════════════
# FVG DETECTOR
# ════════════════════════════════════════════════════════

class FVGDetector:

    def __init__(self):
        self.active_fvgs: list[FVGZone] = []
        self.last_result: Optional[StructureShiftResult] = None
        self._bar_counter: int = 0   # [NEW] track current bar index

    # ════════════════════════════════════
    # PUBLIC API
    # ════════════════════════════════════

    def analyze(
        self,
        df:          pd.DataFrame,
        swing_high:  Optional[float],
        swing_low:   Optional[float],
        trend_dir:   str = "sideways",
    ) -> StructureShiftResult:

        if len(df) < 4 or swing_high is None or swing_low is None:
            return StructureShiftResult(verdict="NONE", reason="ข้อมูลไม่พอ")

        self._bar_counter = len(df)

        # [FIX] ใช้ confirmed candle iloc[-2] ไม่ใช่ live iloc[-1]
        confirmed = df.iloc[:-1]   # ตัด live candle ออก
        price = float(confirmed["close"].iloc[-1])

        # อัพเดท FVG ที่ fill แล้ว และหมดอายุแล้ว
        self._update_and_expire_fvgs(price, self._bar_counter)

        broke_high = price > swing_high
        broke_low  = price < swing_low

        if not broke_high and not broke_low:
            return StructureShiftResult(verdict="NONE", reason="ยังไม่ทะลุ Swing Point")

        has_vol = self._has_volume_data(confirmed)
        fvg = self._detect_recent_fvg(confirmed, broke_high)
        body_outside   = self._body_closes_outside(confirmed, swing_high, swing_low, broke_high)
        volume_high    = self._is_volume_high(confirmed)
        is_hunt_candle = self._is_hunt_candle(confirmed, broke_high)

        # LIQUIDITY HUNT
        if is_hunt_candle and not fvg:
            result = StructureShiftResult(
                verdict    = "HUNT",
                confidence = self._hunt_confidence(confirmed, broke_high),
                reason     = (
                    f"Liquidity Hunt: ไส้ยาว Body ใน range "
                    f"{'above High' if broke_high else 'below Low'}"
                ),
                has_volume = has_vol,
            )
            self.last_result = result
            return result

        # MSS CONFIRMED
        # [FIX] volume_high ต้องเป็น True จริงๆ ไม่ใช่ fail-open
        if fvg and body_outside and volume_high:
            self.active_fvgs.append(fvg)
            result = StructureShiftResult(
                verdict    = "MSS",
                fvg        = fvg,
                confidence = self._mss_confidence(confirmed, fvg, volume_high),
                reason     = (
                    f"MSS Confirmed: FVG {fvg.direction} "
                    f"{fvg.bottom:.2f}-{fvg.top:.2f} รอ Retest"
                ),
                has_volume = has_vol,
            )
            self.last_result = result
            return result

        # MSS ไม่ครบ volume → WAIT แต่บอกว่าขาด volume
        if fvg and body_outside and not volume_high:
            result = StructureShiftResult(
                verdict    = "WAIT",
                fvg        = fvg,
                confidence = 25,
                reason     = (
                    "FVG + Body ปิดนอก แต่ Volume ไม่ถึง threshold"
                    + (" (ไม่มี volume data)" if not has_vol else "")
                ),
                has_volume = has_vol,
            )
            self.last_result = result
            return result

        if fvg and not body_outside:
            result = StructureShiftResult(
                verdict    = "WAIT",
                fvg        = fvg,
                confidence = 30,
                reason     = "FVG เกิดแล้ว แต่ Body ยังไม่ปิดนอก — รอแท่งถัดไป",
                has_volume = has_vol,
            )
            self.last_result = result
            return result

        result = StructureShiftResult(
            verdict    = "WAIT",
            confidence = 20,
            reason     = "ทะลุ Swing Point แต่ยังไม่มี FVG หรือ Hunt pattern",
            has_volume = has_vol,
        )
        self.last_result = result
        return result

    def get_active_fvg_for_price(self, price: float) -> Optional[FVGZone]:
        for fvg in self.active_fvgs:
            if not fvg.filled and fvg.is_price_inside(price):
                return fvg
        return None

    def summary(self) -> str:
        lines = [
            f"=== FVG Detector ===",
            f"  Active FVGs: {len(self.active_fvgs)}",
        ]
        for fvg in self.active_fvgs:
            age = self._bar_counter - fvg.formed_idx
            lines.append(
                f"  [{fvg.direction}] {fvg.bottom:.2f}-{fvg.top:.2f} "
                f"filled={fvg.filled} age={age}bars"
            )
        if self.last_result:
            lines.append(f"  Last: {self.last_result.verdict} — {self.last_result.reason}")
        return "\n".join(lines)

    # ════════════════════════════════════
    # PRIVATE — FVG LIFECYCLE
    # ════════════════════════════════════

    def _update_and_expire_fvgs(self, price: float, current_idx: int):
        """[NEW] รวม fill check + expiry ในที่เดียว"""
        for fvg in self.active_fvgs:
            fvg.check_filled(price)
        # ลบ FVG ที่ filled หรือหมดอายุ
        self.active_fvgs = [
            f for f in self.active_fvgs
            if not f.filled and not f.is_expired(current_idx)
        ]

    # ════════════════════════════════════
    # PRIVATE — DETECTION
    # ════════════════════════════════════

    def _has_volume_data(self, df: pd.DataFrame) -> bool:
        """[NEW] ตรวจว่า feed มี volume จริงหรือเปล่า"""
        return "volume" in df.columns and df["volume"].sum() > 0

    def _detect_recent_fvg(self, df: pd.DataFrame, broke_high: bool) -> Optional[FVGZone]:
        """ค้นหา FVG ใน lookback window — df ต้องเป็น confirmed candles แล้ว"""
        n = len(df)
        if n < 3:
            return None

        highs = df["high"].values
        lows  = df["low"].values
        price_ref = float(df["close"].iloc[-1])

        start = max(2, n - FVG_LOOKBACK)
        for i in range(n - 1, start - 1, -1):
            if broke_high:
                gap_bottom = highs[i - 2]
                gap_top    = lows[i]
                if gap_top > gap_bottom:
                    size = gap_top - gap_bottom
                    if size / price_ref >= FVG_MIN_SIZE_RATIO:
                        return FVGZone(
                            direction  = "bullish",
                            top        = round(gap_top, 5),
                            bottom     = round(gap_bottom, 5),
                            size       = round(size, 5),
                            formed_idx = self._bar_counter,
                        )
            else:
                gap_top    = lows[i - 2]
                gap_bottom = highs[i]
                if gap_top > gap_bottom:
                    size = gap_top - gap_bottom
                    if size / price_ref >= FVG_MIN_SIZE_RATIO:
                        return FVGZone(
                            direction  = "bearish",
                            top        = round(gap_top, 5),
                            bottom     = round(gap_bottom, 5),
                            size       = round(size, 5),
                            formed_idx = self._bar_counter,
                        )
        return None

    def _body_closes_outside(self, df, swing_high, swing_low, broke_high) -> bool:
        last  = df.iloc[-1]   # confirmed candle (iloc[-2] ของ original df)
        open_ = float(last["open"])
        close = float(last["close"])
        body_top    = max(open_, close)
        body_bottom = min(open_, close)
        body_size   = abs(close - open_)
        if body_size == 0:
            return False
        if broke_high:
            outside = max(0, body_top - swing_high)
            return outside / body_size >= BODY_OUTSIDE_RATIO
        else:
            outside = max(0, swing_low - body_bottom)
            return outside / body_size >= BODY_OUTSIDE_RATIO

    def _is_hunt_candle(self, df, broke_high) -> bool:
        last  = df.iloc[-1]
        open_ = float(last["open"])
        close = float(last["close"])
        high  = float(last["high"])
        low   = float(last["low"])
        body  = abs(close - open_)
        if body == 0:
            return False
        if broke_high:
            wick = high - max(open_, close)
            return wick > body * HUNT_WICK_RATIO
        else:
            wick = min(open_, close) - low
            return wick > body * HUNT_WICK_RATIO

    def _is_volume_high(self, df: pd.DataFrame) -> bool:
        """
        [FIX] fail-closed: ถ้าไม่มี volume data → return False
        ไม่ assume high อีกต่อไป
        """
        if not self._has_volume_data(df) or len(df) < 20:
            return False   # ← เปลี่ยนจาก True เป็น False
        vol_tail = df["volume"].tail(20)
        mean = vol_tail.mean()
        std  = vol_tail.std()
        last_vol = float(df["volume"].iloc[-1])
        return last_vol > mean + VOLUME_HIGH_SIGMA * std

    # ════════════════════════════════════
    # PRIVATE — CONFIDENCE
    # ════════════════════════════════════

    def _hunt_confidence(self, df: pd.DataFrame, broke_high: bool) -> int:
        score = 50
        last  = df.iloc[-1]
        open_ = float(last["open"])
        close = float(last["close"])
        high  = float(last["high"])
        low   = float(last["low"])
        body  = abs(close - open_)
        if body > 0:
            if broke_high:
                wick_ratio = (high - max(open_, close)) / body
            else:
                wick_ratio = (min(open_, close) - low) / body
            if wick_ratio > 3:   score += 30
            elif wick_ratio > 2: score += 20
            elif wick_ratio > 1.5: score += 10
        if self._is_volume_high(df):
            score += 20
        return min(100, score)

    def _mss_confidence(self, df, fvg, volume_high) -> int:
        score = 50
        last_price = float(df["close"].iloc[-1])
        fvg_pct = fvg.size / last_price * 100
        if fvg_pct > 0.5:   score += 20
        elif fvg_pct > 0.3: score += 10
        if volume_high: score += 20
        last   = df.iloc[-1]
        spread = float(last["high"]) - float(last["low"])
        body   = abs(float(last["close"]) - float(last["open"]))
        if spread > 0 and body / spread >= MSS_MIN_BODY_RATIO:
            score += 10
        return min(100, score)
