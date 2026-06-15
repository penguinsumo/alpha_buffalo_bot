"""
kivanc_vsaob.py — Alpha Buffalo v5.2 (Sprint Clean)
Dynamic Fibo Retrace (Kivanc style) + VSA Order Block Detector

Changes from v5.1:
  [FIX] ลบ VSA score ออกจาก confluence_score
        VSA อยู่ใน score_manager.py (vsa_gate) เท่านั้น
  [FIX] Tautology score: ลบ "ราคาอยู่ใน Golden Zone +2"
        เพราะ entry condition บังคับอยู่แล้ว
  [FIX] Singleton state: reset last_signal ทุก poll cycle
  [FIX] OB detection: ตัด live candle ออกก่อน segment
  [FIX] detect_absorption แยกออกจาก VSA — ไม่นับเป็น vsa_confirmed
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ──────────────────────────────────────────────
FIBO_LEVELS    = [0.0, 0.236, 0.382, 0.500, 0.618, 0.705, 0.786, 1.0]
GOLDEN_LOW     = 0.618
GOLDEN_HIGH    = 0.786
PIVOT_N        = 3
VSA_PERCENTILE = 90
OB_LOOKBACK    = 5


# ── Data Classes ───────────────────────────────────────────
@dataclass
class PivotPoint:
    index: int
    price: float
    kind:  str


@dataclass
class FiboZone:
    anchor_high: float
    anchor_low:  float
    direction:   str
    levels: dict = field(default_factory=dict)
    golden_top:  float = 0.0
    golden_bot:  float = 0.0

    def __post_init__(self):
        rng = self.anchor_high - self.anchor_low
        for lvl in FIBO_LEVELS:
            if self.direction == "BUY":
                self.levels[lvl] = self.anchor_high - rng * lvl
            else:
                self.levels[lvl] = self.anchor_low + rng * lvl
        self.golden_top = self.levels[GOLDEN_LOW]
        self.golden_bot = self.levels[GOLDEN_HIGH]

    def in_golden_zone(self, price: float) -> bool:
        lo = min(self.golden_top, self.golden_bot)
        hi = max(self.golden_top, self.golden_bot)
        return lo <= price <= hi


@dataclass
class OrderBlock:
    index:         int
    ob_high:       float
    ob_low:        float
    ob_mid:        float
    direction:     str
    volume:        float
    vsa_confirmed: bool = False   # True = ultra high volume จาก feed จริง
    absorption:    bool = False   # [NEW] แยกออกมา ไม่ใช่ vsa
    in_golden:     bool = False


@dataclass
class KivancSignal:
    direction:       str
    entry_price:     float
    sl_price:        float
    tp1_price:       float
    tp2_price:       float
    fibo_zone:       FiboZone
    order_block:     OrderBlock
    confluence_score: int        # 0-10 (ไม่รวม VSA — อยู่ใน score_manager)
    label:           str = ""


# ── Pivot Detection ────────────────────────────────────────
def find_pivot_highs(df: pd.DataFrame, n: int = PIVOT_N) -> list[PivotPoint]:
    pivots = []
    # [FIX] ตัด live candle (ไม่รวม iloc[-1])
    safe_df = df.iloc[:-1]
    for i in range(n, len(safe_df) - n):
        high  = safe_df["high"].iloc[i]
        left  = all(safe_df["high"].iloc[i - j] < high for j in range(1, n + 1))
        right = all(safe_df["high"].iloc[i + j] < high for j in range(1, n + 1))
        if left and right:
            pivots.append(PivotPoint(index=i, price=high, kind="high"))
    return pivots


def find_pivot_lows(df: pd.DataFrame, n: int = PIVOT_N) -> list[PivotPoint]:
    pivots = []
    safe_df = df.iloc[:-1]
    for i in range(n, len(safe_df) - n):
        low   = safe_df["low"].iloc[i]
        left  = all(safe_df["low"].iloc[i - j] > low for j in range(1, n + 1))
        right = all(safe_df["low"].iloc[i + j] > low for j in range(1, n + 1))
        if left and right:
            pivots.append(PivotPoint(index=i, price=low, kind="low"))
    return pivots


def get_latest_swing(df: pd.DataFrame, n: int = PIVOT_N):
    highs = find_pivot_highs(df, n)
    lows  = find_pivot_lows(df, n)
    return (highs[-1] if highs else None), (lows[-1] if lows else None)


# ── VSA Volume Analysis ────────────────────────────────────
def is_ultra_high_volume(df: pd.DataFrame, idx: int, window: int = 50) -> bool:
    """True = volume จาก feed จริง และสูง top 10%"""
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return False   # ไม่มี volume data → False เสมอ
    vol       = df["volume"].iloc[idx]
    start     = max(0, idx - window)
    rolling   = df["volume"].iloc[start:idx]
    if len(rolling) < 5:
        return False
    threshold = rolling.quantile(VSA_PERCENTILE / 100)
    return float(vol) >= float(threshold)


def detect_absorption(df: pd.DataFrame, idx: int) -> bool:
    """
    Absorption candle: wide range + wick > 60%
    [NOTE] แยกออกจาก vsa_confirmed แล้ว — ใช้เป็น ob quality flag เท่านั้น
    ไม่นับเป็น VSA score
    """
    candle = df.iloc[idx]
    rng    = candle["high"] - candle["low"]
    if rng == 0:
        return False
    body = abs(candle["close"] - candle["open"])
    wick = rng - body
    return (wick / rng) > 0.60


# ── Order Block Detection ──────────────────────────────────
def find_bullish_ob(df: pd.DataFrame, impulse_start: int) -> Optional[OrderBlock]:
    # [FIX] ตัด live candle และ cap impulse_start
    safe_end = min(impulse_start, len(df) - 1)
    start    = max(0, safe_end - OB_LOOKBACK)
    segment  = df.iloc[start:safe_end]   # ไม่รวม live candle

    bearish = segment[segment["close"] < segment["open"]]
    if bearish.empty:
        return None

    ob_row     = bearish.iloc[-1]
    ob_idx     = bearish.index[-1]
    ob_idx_pos = df.index.get_loc(ob_idx) if isinstance(ob_idx, pd.Timestamp) else ob_idx

    vsa        = is_ultra_high_volume(df, ob_idx_pos)      # volume เท่านั้น
    absorption = detect_absorption(df, ob_idx_pos)          # แยก flag

    return OrderBlock(
        index         = ob_idx_pos,
        ob_high       = float(ob_row["high"]),
        ob_low        = float(ob_row["low"]),
        ob_mid        = (float(ob_row["high"]) + float(ob_row["low"])) / 2,
        direction     = "BUY",
        volume        = float(ob_row.get("volume", 0)),
        vsa_confirmed = vsa,         # ← volume จริงเท่านั้น
        absorption    = absorption,  # ← แยก flag
    )


def find_bearish_ob(df: pd.DataFrame, impulse_start: int) -> Optional[OrderBlock]:
    safe_end = min(impulse_start, len(df) - 1)
    start    = max(0, safe_end - OB_LOOKBACK)
    segment  = df.iloc[start:safe_end]

    bullish = segment[segment["close"] > segment["open"]]
    if bullish.empty:
        return None

    ob_row     = bullish.iloc[-1]
    ob_idx     = bullish.index[-1]
    ob_idx_pos = df.index.get_loc(ob_idx) if isinstance(ob_idx, pd.Timestamp) else ob_idx

    vsa        = is_ultra_high_volume(df, ob_idx_pos)
    absorption = detect_absorption(df, ob_idx_pos)

    return OrderBlock(
        index         = ob_idx_pos,
        ob_high       = float(ob_row["high"]),
        ob_low        = float(ob_row["low"]),
        ob_mid        = (float(ob_row["high"]) + float(ob_row["low"])) / 2,
        direction     = "SELL",
        volume        = float(ob_row.get("volume", 0)),
        vsa_confirmed = vsa,
        absorption    = absorption,
    )


# ── Confluence Score (ไม่รวม VSA) ─────────────────────────
def calc_confluence_score(ob: OrderBlock, fibo: FiboZone) -> int:
    """
    [FIX] ลบ 2 รายการออก:
      - VSA confirmed +3  → ย้ายไป score_manager (vsa_gate bucket D)
      - ราคาใน Golden +2  → tautology (entry gate บังคับอยู่แล้ว)

    เหลือ 2 รายการ max = 5:
      - OB mid ใน Golden Zone : +3
      - OB size เล็ก (precision): +2
    """
    score = 0

    if fibo.in_golden_zone(ob.ob_mid):
        score += 3
        ob.in_golden = True

    ob_size    = ob.ob_high - ob.ob_low
    fibo_range = abs(fibo.golden_top - fibo.golden_bot)
    if fibo_range > 0 and ob_size < fibo_range * 0.5:
        score += 2

    # absorption เป็น quality flag แต่ไม่บวก score
    # (ใช้ใน summary log เท่านั้น)

    return min(score, 5)


# ── Main Engine ────────────────────────────────────────────
class KivancVSAEngine:
    def __init__(self, min_score: int = 3):
        self.min_score    = min_score   # ปรับ threshold ตาม score ใหม่ (max=5)
        self.last_signal: Optional[KivancSignal] = None

    def reset(self):
        """[NEW] เรียกก่อนทุก poll cycle เพื่อ clear state"""
        self.last_signal = None

    def analyze(self, df: pd.DataFrame) -> Optional[KivancSignal]:
        self.reset()   # [FIX] reset ทุกครั้งที่ analyze

        if len(df) < 20:
            return None

        # [FIX] ใช้ confirmed price (iloc[-2])
        current_price = float(df["close"].iloc[-2])
        latest_high, latest_low = get_latest_swing(df)

        if not latest_high or not latest_low:
            return None

        signals = []

        # ── SELL Setup ────────────────────────────────────
        if latest_high.index > latest_low.index:
            fibo = FiboZone(
                anchor_high = latest_high.price,
                anchor_low  = latest_low.price,
                direction   = "SELL",
            )
            ob = find_bearish_ob(df, latest_high.index)
            if ob:
                score = calc_confluence_score(ob, fibo)
                if score >= self.min_score and fibo.in_golden_zone(current_price):
                    rng = latest_high.price - latest_low.price
                    sig = KivancSignal(
                        direction       = "SELL",
                        entry_price     = current_price,
                        sl_price        = ob.ob_high + (rng * 0.05),
                        tp1_price       = current_price - rng * 0.382,
                        tp2_price       = current_price - rng * 0.618,
                        fibo_zone       = fibo,
                        order_block     = ob,
                        confluence_score = score,
                        label = (
                            f"Kivanc SELL | OB:{ob.ob_mid:.2f} | "
                            f"Score:{score} | VSA:{ob.vsa_confirmed} | Abs:{ob.absorption}"
                        ),
                    )
                    signals.append(sig)

        # ── BUY Setup ─────────────────────────────────────
        if latest_low.index > latest_high.index:
            fibo = FiboZone(
                anchor_high = latest_high.price,
                anchor_low  = latest_low.price,
                direction   = "BUY",
            )
            ob = find_bullish_ob(df, latest_low.index)
            if ob:
                score = calc_confluence_score(ob, fibo)
                if score >= self.min_score and fibo.in_golden_zone(current_price):
                    rng = latest_high.price - latest_low.price
                    sig = KivancSignal(
                        direction       = "BUY",
                        entry_price     = current_price,
                        sl_price        = ob.ob_low - (rng * 0.05),
                        tp1_price       = current_price + rng * 0.382,
                        tp2_price       = current_price + rng * 0.618,
                        fibo_zone       = fibo,
                        order_block     = ob,
                        confluence_score = score,
                        label = (
                            f"Kivanc BUY | OB:{ob.ob_mid:.2f} | "
                            f"Score:{score} | VSA:{ob.vsa_confirmed} | Abs:{ob.absorption}"
                        ),
                    )
                    signals.append(sig)

        if not signals:
            return None

        best = max(signals, key=lambda s: s.confluence_score)
        self.last_signal = best
        return best

    def summary(self, sig: KivancSignal) -> str:
        ob  = sig.order_block
        fib = sig.fibo_zone
        return (
            f"🎯 {sig.label}\n"
            f"   Entry  : {sig.entry_price:.2f}\n"
            f"   SL     : {sig.sl_price:.2f}\n"
            f"   TP1    : {sig.tp1_price:.2f}\n"
            f"   TP2    : {sig.tp2_price:.2f}\n"
            f"   OB     : {ob.ob_low:.2f}-{ob.ob_high:.2f}"
            f" | InGolden:{ob.in_golden} | Absorption:{ob.absorption}\n"
            f"   Fibo   : {fib.anchor_low:.2f}-{fib.anchor_high:.2f}"
            f" | Golden:{fib.golden_bot:.2f}-{fib.golden_top:.2f}\n"
            f"   Score  : {sig.confluence_score}/5 (VSA อยู่ใน score_manager)"
        )


# ── Singleton ──────────────────────────────────────────────
kivanc_engine = KivancVSAEngine(min_score=3)


def run_kivanc(df: pd.DataFrame) -> Optional[KivancSignal]:
    return kivanc_engine.analyze(df)

# ============================================================
# 🆕 PHASE 6A: Weighted Score System (แทน AND conditions)
# ============================================================

KIVANC_SIGNAL_WEIGHTS = {
    "stopping_volume": 2.0,
    "absorption": 2.0,
    "high_spread": 1.5,
    "reversal_wick": 1.5,
    "golden_zone": 2.0,
    "volume_confirmation": 1.0,
    "order_block": 1.0,
}

def get_kivanc_score(signals: dict) -> float:
    """
    Weighted Score — ไม่ต้องครบทุก AND
    signals = {"stopping_volume": True, "golden_zone": True, ...}
    Returns: 0.0 - 3.0
    """
    score = 0.0
    for key, weight in KIVANC_SIGNAL_WEIGHTS.items():
        if signals.get(key, False):
            score += weight
    return min(score, 3.0)  # Cap at 3.0

def is_valid_kivanc_signal(signals: dict, min_score: float = 2.0) -> bool:
    """ผ่านเกณฑ์ขั้นต่ำ (2.0 จาก 3.0) = สัญญาณใช้ได้"""
    return get_kivanc_score(signals) >= min_score
