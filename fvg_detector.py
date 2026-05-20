"""
fvg_detector.py — Alpha Buffalo V4+
================================================
Fair Value Gap (FVG) + Structure Shift Engine

หน้าที่:
  แยก "Liquidity Hunt" ออกจาก "MSS จริง"
  โดยใช้ FVG เป็นตัวชี้วัดหลัก

Logic:
  ราคาทะลุ Swing Point
    │
    ├── ไม่มี FVG + VSA Climax + Body ใน range
    │   = LIQUIDITY_HUNT → V4 Reversal
    │
    └── มี FVG + Body ปิดนอก + Volume สูง
        = MSS_CONFIRMED → รอ Retest → V5 Sniper

FVG Definition:
  Bullish FVG: แท่ง[i-2].high < แท่ง[i].low
  Bearish FVG: แท่ง[i-2].low  > แท่ง[i].high
  = Gap ที่ราคาไม่เคยผ่าน → เจ้ามือจะกลับมา Fill
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════

FVG_MIN_SIZE_RATIO  = 0.003   # FVG ต้องกว้างอย่างน้อย 0.3% ของราคา
FVG_LOOKBACK        = 10      # มองย้อนหลัง 10 แท่งหา FVG
VOLUME_HIGH_SIGMA   = 1.5     # Volume สูง = mean + 1.5 std
BODY_OUTSIDE_RATIO  = 0.5     # Body ต้องอยู่นอก swing point เกิน 50%
HUNT_WICK_RATIO     = 2.0     # Wick ต้องยาวกว่า Body 2x สำหรับ Hunt
MSS_MIN_BODY_RATIO  = 0.6     # Body ต้องเป็น 60%+ ของ spread สำหรับ MSS


# ════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════

@dataclass
class FVGZone:
    """Fair Value Gap zone"""
    direction:  str    # "bullish" | "bearish"
    top:        float  # บนสุดของ gap
    bottom:     float  # ล่างสุดของ gap
    size:       float  # ขนาด gap
    formed_idx: int    # index แท่งที่ FVG เกิด
    filled:     bool  = False

    @property
    def midpoint(self) -> float:
        return (self.top + self.bottom) / 2

    def is_price_inside(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def check_filled(self, price: float) -> bool:
        """FVG filled เมื่อราคาผ่านกลาง gap"""
        if self.direction == "bullish" and price <= self.midpoint:
            self.filled = True
        elif self.direction == "bearish" and price >= self.midpoint:
            self.filled = True
        return self.filled


@dataclass
class StructureShiftResult:
    """ผลลัพธ์จาก Structure Shift Engine"""
    verdict:    str             # "HUNT" | "MSS" | "WAIT" | "NONE"
    fvg:        Optional[FVGZone] = None
    confidence: int = 0         # 0-100
    reason:     str = ""

    @property
    def is_hunt(self) -> bool:
        return self.verdict == "HUNT"

    @property
    def is_mss(self) -> bool:
        return self.verdict == "MSS"

    @property
    def entry_zone(self) -> Optional[tuple]:
        """คืน (bottom, top) ถ้าเป็น MSS"""
        if self.is_mss and self.fvg:
            return (self.fvg.bottom, self.fvg.top)
        return None


# ════════════════════════════════════════════════════════
# FVG DETECTOR
# ════════════════════════════════════════════════════════

class FVGDetector:
    """
    FVG + Structure Shift Engine
    ใช้แทน _check_bos() ใน pivot_engine_v2

    Usage:
        detector = FVGDetector()
        result = detector.analyze(df, swing_high, swing_low)

        if result.is_hunt:
            → V4 Reversal Entry
        elif result.is_mss:
            → รอ Retest FVG → V5 Sniper
        else:
            → รอต่อ
    """

    def __init__(self):
        self.active_fvgs: list[FVGZone] = []
        self.last_result: Optional[StructureShiftResult] = None

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
        """
        วิเคราะห์ว่าการทะลุ Swing Point เป็น Hunt หรือ MSS

        Parameters:
            df         : OHLCV DataFrame
            swing_high : locked_high จาก PivotEngine
            swing_low  : locked_low จาก PivotEngine
            trend_dir  : "up" | "down" | "sideways"

        Returns:
            StructureShiftResult
        """
        if len(df) < 3 or swing_high is None or swing_low is None:
            return StructureShiftResult(verdict="NONE", reason="ข้อมูลไม่พอ")

        price = float(df["close"].iloc[-1])

        # อัพเดท FVG ที่ถูก fill แล้ว
        self._update_filled_fvgs(price)

        # เช็คว่าราคาทะลุ Swing Point ไหม
        broke_high = price > swing_high
        broke_low  = price < swing_low

        if not broke_high and not broke_low:
            return StructureShiftResult(verdict="NONE", reason="ยังไม่ทะลุ Swing Point")

        # ตรวจ FVG ในแท่งที่เพิ่งเกิด
        fvg = self._detect_recent_fvg(df, broke_high)

        # วิเคราะห์แท่งล่าสุด
        body_outside  = self._body_closes_outside(df, swing_high, swing_low, broke_high)
        volume_high   = self._is_volume_high(df)
        is_hunt_candle = self._is_hunt_candle(df, broke_high)

        # ════════════════════════════════
        # DECISION LOGIC
        # ════════════════════════════════

        # LIQUIDITY HUNT:
        # ไม่มี FVG + ไส้ยาว + Body ใน range
        if is_hunt_candle and not fvg:
            result = StructureShiftResult(
                verdict    = "HUNT",
                fvg        = None,
                confidence = self._hunt_confidence(df, broke_high),
                reason     = (
                    f"Liquidity Hunt: ไส้ยาว Body ใน range "
                    f"{'above High' if broke_high else 'below Low'}"
                ),
            )
            self.last_result = result
            return result

        # MSS CONFIRMED:
        # มี FVG + Body ปิดนอก + Volume สูง
        if fvg and body_outside and volume_high:
            self.active_fvgs.append(fvg)
            result = StructureShiftResult(
                verdict    = "MSS",
                fvg        = fvg,
                confidence = self._mss_confidence(df, fvg, volume_high),
                reason     = (
                    f"MSS Confirmed: FVG {fvg.direction} "
                    f"{fvg.bottom:.2f}-{fvg.top:.2f} "
                    f"รอ Retest"
                ),
            )
            self.last_result = result
            return result

        # มี FVG แต่ยังไม่ครบเงื่อนไข MSS
        if fvg and not body_outside:
            result = StructureShiftResult(
                verdict    = "WAIT",
                fvg        = fvg,
                confidence = 30,
                reason     = "FVG เกิดแล้ว แต่ Body ยังไม่ปิดนอก — รอแท่งถัดไป",
            )
            self.last_result = result
            return result

        # ทะลุแต่ไม่มีหลักฐานชัด
        result = StructureShiftResult(
            verdict    = "WAIT",
            confidence = 20,
            reason     = "ทะลุ Swing Point แต่ยังไม่มี FVG หรือ Hunt pattern",
        )
        self.last_result = result
        return result

    def get_active_fvg_for_price(self, price: float) -> Optional[FVGZone]:
        """ราคาอยู่ใน FVG ไหน — ใช้ตอนหา entry zone"""
        for fvg in self.active_fvgs:
            if not fvg.filled and fvg.is_price_inside(price):
                return fvg
        return None

    def clear_filled_fvgs(self):
        self.active_fvgs = [f for f in self.active_fvgs if not f.filled]

    def summary(self) -> str:
        lines = [
            f"=== FVG Detector ===",
            f"  Active FVGs: {len(self.active_fvgs)}",
        ]
        for fvg in self.active_fvgs:
            lines.append(
                f"  [{fvg.direction}] {fvg.bottom:.2f}-{fvg.top:.2f} "
                f"filled={fvg.filled}"
            )
        if self.last_result:
            lines.append(f"  Last: {self.last_result.verdict} — {self.last_result.reason}")
        return "\n".join(lines)


    # ════════════════════════════════════
    # FVG DETECTION
    # ════════════════════════════════════

    def _detect_recent_fvg(
        self,
        df:         pd.DataFrame,
        broke_high: bool,
    ) -> Optional[FVGZone]:
        """
        หา FVG ในแท่งล่าสุด FVG_LOOKBACK แท่ง
        Bullish FVG: df[i-2].high < df[i].low
        Bearish FVG: df[i-2].low  > df[i].high
        """
        highs  = df["high"].values
        lows   = df["low"].values
        closes = df["close"].values
        n      = len(df)

        lookback = min(FVG_LOOKBACK, n - 2)

        for i in range(n - 1, n - 1 - lookback, -1):
            if i < 2:
                break

            price_ref = closes[i]

            # Bullish FVG (ราคาพุ่งขึ้น)
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
                            formed_idx = i,
                        )

            # Bearish FVG (ราคาดิ่งลง)
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
                            formed_idx = i,
                        )

        return None


    # ════════════════════════════════════
    # CANDLE ANALYSIS
    # ════════════════════════════════════

    def _body_closes_outside(
        self,
        df:         pd.DataFrame,
        swing_high: float,
        swing_low:  float,
        broke_high: bool,
    ) -> bool:
        """
        Body ของแท่งล่าสุดปิดนอก Swing Point เกิน BODY_OUTSIDE_RATIO
        """
        last  = df.iloc[-1]
        open_ = float(last["open"])
        close = float(last["close"])
        body_top    = max(open_, close)
        body_bottom = min(open_, close)
        body_size   = abs(close - open_)

        if body_size == 0:
            return False

        if broke_high:
            # Body ต้องอยู่เหนือ swing_high
            outside = max(0, body_top - swing_high)
            return outside / body_size >= BODY_OUTSIDE_RATIO
        else:
            # Body ต้องอยู่ใต้ swing_low
            outside = max(0, swing_low - body_bottom)
            return outside / body_size >= BODY_OUTSIDE_RATIO

    def _is_hunt_candle(
        self,
        df:         pd.DataFrame,
        broke_high: bool,
    ) -> bool:
        """
        Liquidity Hunt candle:
        - ไส้ยาวมากทางที่ทะลุ
        - Body ปิดกลับเข้า range
        """
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
        """Volume สูงกว่า mean + sigma"""
        if "volume" not in df.columns or len(df) < 20:
            return True  # ถ้าไม่มี volume → assume high
        vol_tail = df["volume"].tail(20)
        mean = vol_tail.mean()
        std  = vol_tail.std()
        last_vol = float(df["volume"].iloc[-1])
        return last_vol > mean + VOLUME_HIGH_SIGMA * std

    def _update_filled_fvgs(self, price: float):
        for fvg in self.active_fvgs:
            fvg.check_filled(price)


    # ════════════════════════════════════
    # CONFIDENCE SCORING
    # ════════════════════════════════════

    def _hunt_confidence(self, df: pd.DataFrame, broke_high: bool) -> int:
        """คำนวณ confidence ของ Liquidity Hunt (0-100)"""
        score = 50  # base

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

            if wick_ratio > 3:
                score += 30
            elif wick_ratio > 2:
                score += 20
            elif wick_ratio > 1.5:
                score += 10

        if self._is_volume_high(df):
            score += 20

        return min(100, score)

    def _mss_confidence(
        self,
        df:         pd.DataFrame,
        fvg:        FVGZone,
        volume_high: bool,
    ) -> int:
        """คำนวณ confidence ของ MSS (0-100)"""
        score = 50  # base

        # FVG ขนาดใหญ่ = confidence สูง
        last_price = float(df["close"].iloc[-1])
        fvg_pct = fvg.size / last_price * 100
        if fvg_pct > 0.5:
            score += 20
        elif fvg_pct > 0.3:
            score += 10

        if volume_high:
            score += 20

        # Body ratio
        last  = df.iloc[-1]
        spread = float(last["high"]) - float(last["low"])
        body   = abs(float(last["close"]) - float(last["open"]))
        if spread > 0 and body / spread >= MSS_MIN_BODY_RATIO:
            score += 10

        return min(100, score)


# ════════════════════════════════════════════════════════
# QUICK TEST
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    import pandas as pd
    import numpy as np

    print("=" * 56)
    print("FVG Detector Test")
    print("=" * 56)

    np.random.seed(42)
    n = 30

    # ── Test 1: Liquidity Hunt ──
    # ราคาพุ่งทะลุ High แต่ปิดกลับลงมา (ไส้บนยาว)
    prices = np.linspace(4400, 4500, n)
    hunt_df = pd.DataFrame({
        "open":   prices - 2,
        "high":   prices + 5,
        "low":    prices - 5,
        "close":  prices,
        "volume": 1000 + np.random.randint(0, 500, n),
    })
    # แท่งสุดท้าย: spike ขึ้นสูง แต่ปิดต่ำ = Hunt
    hunt_df.iloc[-1, hunt_df.columns.get_loc("high")]  = 4550  # spike สูง
    hunt_df.iloc[-1, hunt_df.columns.get_loc("close")] = 4495  # ปิดกลับลงมา
    hunt_df.iloc[-1, hunt_df.columns.get_loc("open")]  = 4498
    hunt_df.iloc[-1, hunt_df.columns.get_loc("volume")] = 3500  # volume สูง

    detector = FVGDetector()
    result = detector.analyze(hunt_df, swing_high=4510, swing_low=4380, trend_dir="up")
    print(f"\n[Test 1] Liquidity Hunt")
    print(f"  Verdict   : {result.verdict}")
    print(f"  Confidence: {result.confidence}")
    print(f"  Reason    : {result.reason}")

    # ── Test 2: MSS Confirmed ──
    # ราคาทะลุ High + FVG เกิด + Body ปิดนอก
    mss_prices = np.linspace(4400, 4520, n)
    mss_df = pd.DataFrame({
        "open":   mss_prices - 2,
        "high":   mss_prices + 3,
        "low":    mss_prices - 3,
        "close":  mss_prices,
        "volume": 1000 + np.random.randint(0, 500, n),
    })
    # สร้าง FVG: แท่ง i-2 high < แท่ง i low
    mss_df.iloc[-3, mss_df.columns.get_loc("high")]  = 4505
    mss_df.iloc[-1, mss_df.columns.get_loc("low")]   = 4510  # FVG: 4505-4510
    mss_df.iloc[-1, mss_df.columns.get_loc("close")] = 4525  # Body ปิดนอก High
    mss_df.iloc[-1, mss_df.columns.get_loc("open")]  = 4508
    mss_df.iloc[-1, mss_df.columns.get_loc("volume")] = 4000

    detector2 = FVGDetector()
    result2 = detector2.analyze(mss_df, swing_high=4510, swing_low=4380, trend_dir="up")
    print(f"\n[Test 2] MSS Confirmed")
    print(f"  Verdict   : {result2.verdict}")
    print(f"  Confidence: {result2.confidence}")
    print(f"  Reason    : {result2.reason}")
    if result2.fvg:
        print(f"  FVG Zone  : {result2.fvg.bottom}-{result2.fvg.top}")
        print(f"  Entry Zone: {result2.entry_zone}")

    # ── Test 3: Price in FVG ──
    if result2.fvg:
        price_in = result2.fvg.midpoint
        fvg_hit  = detector2.get_active_fvg_for_price(price_in)
        print(f"\n[Test 3] Price in FVG")
        print(f"  Test price: {price_in:.2f}")
        print(f"  In FVG    : {fvg_hit is not None}")

    print(f"\n{detector2.summary()}")
    print("\nAll tests done.")
