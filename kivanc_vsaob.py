"""
kivanc_vsaob.py — Alpha Buffalo v5
Dynamic Fibo Retrace (Kivanc style) + VSA Order Block Detector
Sprint 1B

Logic:
1. หา Pivot High/Low ล่าสุดแบบ Dynamic (N แท่งซ้าย/ขวา)
2. กาง Fibo อัตโนมัติ หา Golden Zone 0.618-0.786
3. หา Order Block ใน Impulse Wave ที่มี VSA confirm
4. Confluence = OB อยู่ใน Golden Zone → สัญญาณ Sniper
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional

# ── Constants ─────────────────────────────────────────────
FIBO_LEVELS   = [0.0, 0.236, 0.382, 0.500, 0.618, 0.705, 0.786, 1.0]
GOLDEN_LOW    = 0.618
GOLDEN_HIGH   = 0.786
PIVOT_N       = 3        # แท่งซ้าย/ขวา สำหรับ confirm pivot
VSA_PERCENTILE = 90      # top 10% volume = Ultra High
OB_LOOKBACK   = 5        # มองหา OB ย้อนหลัง N แท่งก่อน impulse


# ── Data Classes ──────────────────────────────────────────
@dataclass
class PivotPoint:
    index: int
    price: float
    kind: str            # "high" or "low"


@dataclass
class FiboZone:
    anchor_high: float
    anchor_low: float
    direction: str       # "BUY" or "SELL"
    levels: dict = field(default_factory=dict)
    golden_top: float = 0.0
    golden_bot: float = 0.0

    def __post_init__(self):
        rng = self.anchor_high - self.anchor_low
        for lvl in FIBO_LEVELS:
            if self.direction == "BUY":
                # Retrace จาก High ลงมา
                self.levels[lvl] = self.anchor_high - rng * lvl
            else:
                # Retrace จาก Low ขึ้นไป
                self.levels[lvl] = self.anchor_low + rng * lvl
        self.golden_top = self.levels[GOLDEN_LOW]
        self.golden_bot = self.levels[GOLDEN_HIGH]

    def in_golden_zone(self, price: float) -> bool:
        lo = min(self.golden_top, self.golden_bot)
        hi = max(self.golden_top, self.golden_bot)
        return lo <= price <= hi


@dataclass
class OrderBlock:
    index: int
    ob_high: float
    ob_low: float
    ob_mid: float
    direction: str       # "BUY" = bullish OB, "SELL" = bearish OB
    volume: float
    vsa_confirmed: bool = False
    in_golden: bool = False


@dataclass
class KivancSignal:
    direction: str           # "BUY" or "SELL"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    fibo_zone: FiboZone
    order_block: OrderBlock
    confluence_score: int    # 0-10
    label: str = ""


# ── Pivot Detection ────────────────────────────────────────
def find_pivot_highs(df: pd.DataFrame, n: int = PIVOT_N) -> list[PivotPoint]:
    pivots = []
    for i in range(n, len(df) - n):
        high = df["high"].iloc[i]
        left  = all(df["high"].iloc[i - j] < high for j in range(1, n + 1))
        right = all(df["high"].iloc[i + j] < high for j in range(1, n + 1))
        if left and right:
            pivots.append(PivotPoint(index=i, price=high, kind="high"))
    return pivots


def find_pivot_lows(df: pd.DataFrame, n: int = PIVOT_N) -> list[PivotPoint]:
    pivots = []
    for i in range(n, len(df) - n):
        low = df["low"].iloc[i]
        left  = all(df["low"].iloc[i - j] > low for j in range(1, n + 1))
        right = all(df["low"].iloc[i + j] > low for j in range(1, n + 1))
        if left and right:
            pivots.append(PivotPoint(index=i, price=low, kind="low"))
    return pivots


def get_latest_swing(df: pd.DataFrame, n: int = PIVOT_N) -> tuple[Optional[PivotPoint], Optional[PivotPoint]]:
    """หา Swing High และ Swing Low ล่าสุด"""
    highs = find_pivot_highs(df, n)
    lows  = find_pivot_lows(df, n)
    latest_high = highs[-1] if highs else None
    latest_low  = lows[-1]  if lows  else None
    return latest_high, latest_low


# ── VSA Volume Analysis ────────────────────────────────────
def is_ultra_high_volume(df: pd.DataFrame, idx: int, window: int = 50) -> bool:
    """Top 10% volume = Ultra High (VSA)"""
    if "volume" not in df.columns:
        return False
    vol     = df["volume"].iloc[idx]
    start   = max(0, idx - window)
    rolling = df["volume"].iloc[start:idx]
    if len(rolling) < 5:
        return False
    threshold = rolling.quantile(VSA_PERCENTILE / 100)
    return vol >= threshold


def detect_absorption(df: pd.DataFrame, idx: int) -> bool:
    """
    Absorption = แท่งที่มี range กว้าง แต่ close กลับมากลางแท่ง
    หมายถึง Smart Money ดูดซับแรงขาย
    """
    candle = df.iloc[idx]
    rng    = candle["high"] - candle["low"]
    if rng == 0:
        return False
    body   = abs(candle["close"] - candle["open"])
    wick   = rng - body
    # wick > 60% ของ range = absorption
    return (wick / rng) > 0.60


# ── Order Block Detection ──────────────────────────────────
def find_bullish_ob(df: pd.DataFrame, impulse_start: int) -> Optional[OrderBlock]:
    """
    Bullish OB = แท่ง Bearish สุดท้ายก่อน Impulse ขึ้น
    ต้องมี VSA confirm
    """
    start = max(0, impulse_start - OB_LOOKBACK)
    segment = df.iloc[start:impulse_start]

    # หาแท่ง Bearish (close < open)
    bearish = segment[segment["close"] < segment["open"]]
    if bearish.empty:
        return None

    ob_row = bearish.iloc[-1]
    ob_idx = bearish.index[-1]
    if isinstance(ob_idx, pd.Timestamp):
        ob_idx_pos = df.index.get_loc(ob_idx)
    else:
        ob_idx_pos = ob_idx

    vsa = is_ultra_high_volume(df, ob_idx_pos) or detect_absorption(df, ob_idx_pos)

    return OrderBlock(
        index=ob_idx_pos,
        ob_high=ob_row["high"],
        ob_low=ob_row["low"],
        ob_mid=(ob_row["high"] + ob_row["low"]) / 2,
        direction="BUY",
        volume=ob_row.get("volume", 0),
        vsa_confirmed=vsa,
    )


def find_bearish_ob(df: pd.DataFrame, impulse_start: int) -> Optional[OrderBlock]:
    """
    Bearish OB = แท่ง Bullish สุดท้ายก่อน Impulse ลง
    ต้องมี VSA confirm
    """
    start = max(0, impulse_start - OB_LOOKBACK)
    segment = df.iloc[start:impulse_start]

    # หาแท่ง Bullish (close > open)
    bullish = segment[segment["close"] > segment["open"]]
    if bullish.empty:
        return None

    ob_row = bullish.iloc[-1]
    ob_idx = bullish.index[-1]
    if isinstance(ob_idx, pd.Timestamp):
        ob_idx_pos = df.index.get_loc(ob_idx)
    else:
        ob_idx_pos = ob_idx

    vsa = is_ultra_high_volume(df, ob_idx_pos) or detect_absorption(df, ob_idx_pos)

    return OrderBlock(
        index=ob_idx_pos,
        ob_high=ob_row["high"],
        ob_low=ob_row["low"],
        ob_mid=(ob_row["high"] + ob_row["low"]) / 2,
        direction="SELL",
        volume=ob_row.get("volume", 0),
        vsa_confirmed=vsa,
    )


# ── Confluence Score ───────────────────────────────────────
def calc_confluence_score(ob: OrderBlock, fibo: FiboZone, current_price: float) -> int:
    score = 0

    # OB อยู่ใน Golden Zone
    if fibo.in_golden_zone(ob.ob_mid):
        score += 3
        ob.in_golden = True

    # VSA confirmed
    if ob.vsa_confirmed:
        score += 3

    # ราคาปัจจุบันอยู่ใน Golden Zone
    if fibo.in_golden_zone(current_price):
        score += 2

    # OB ขนาดเล็ก (zone แน่น = precision สูง)
    ob_size = ob.ob_high - ob.ob_low
    fibo_range = abs(fibo.golden_top - fibo.golden_bot)
    if ob_size < fibo_range * 0.5:
        score += 2

    return min(score, 10)


# ── Main: Kivanc VSA OB Engine ────────────────────────────
class KivancVSAEngine:
    def __init__(self, min_score: int = 5):
        self.min_score   = min_score
        self.last_signal: Optional[KivancSignal] = None

    def analyze(self, df: pd.DataFrame) -> Optional[KivancSignal]:
        """
        รับ df (1H หรือ 4H) → คืน KivancSignal หรือ None
        """
        if len(df) < 20:
            return None

        current_price = float(df["close"].iloc[-1])
        latest_high, latest_low = get_latest_swing(df)

        if not latest_high or not latest_low:
            return None

        signals = []

        # ── SELL Setup: High → Low retrace ────────────────
        if latest_high.index > latest_low.index:
            # Impulse = High → Low (Bearish impulse)
            # Retrace = ราคากำลัง retrace กลับขึ้นไป
            fibo = FiboZone(
                anchor_high=latest_high.price,
                anchor_low=latest_low.price,
                direction="SELL",
            )
            ob = find_bearish_ob(df, latest_high.index)
            if ob:
                score = calc_confluence_score(ob, fibo, current_price)
                if score >= self.min_score and fibo.in_golden_zone(current_price):
                    rng = latest_high.price - latest_low.price
                    sig = KivancSignal(
                        direction="SELL",
                        entry_price=current_price,
                        sl_price=ob.ob_high + (rng * 0.05),
                        tp1_price=current_price - rng * 0.382,
                        tp2_price=current_price - rng * 0.618,
                        fibo_zone=fibo,
                        order_block=ob,
                        confluence_score=score,
                        label=f"Kivanc SELL | OB:{ob.ob_mid:.2f} | Score:{score}",
                    )
                    signals.append(sig)

        # ── BUY Setup: Low → High retrace ─────────────────
        if latest_low.index > latest_high.index:
            # Impulse = Low → High (Bullish impulse)
            # Retrace = ราคากำลัง retrace ลงมา
            fibo = FiboZone(
                anchor_high=latest_high.price,
                anchor_low=latest_low.price,
                direction="BUY",
            )
            ob = find_bullish_ob(df, latest_low.index)
            if ob:
                score = calc_confluence_score(ob, fibo, current_price)
                if score >= self.min_score and fibo.in_golden_zone(current_price):
                    rng = latest_high.price - latest_low.price
                    sig = KivancSignal(
                        direction="BUY",
                        entry_price=current_price,
                        sl_price=ob.ob_low - (rng * 0.05),
                        tp1_price=current_price + rng * 0.382,
                        tp2_price=current_price + rng * 0.618,
                        fibo_zone=fibo,
                        order_block=ob,
                        confluence_score=score,
                        label=f"Kivanc BUY | OB:{ob.ob_mid:.2f} | Score:{score}",
                    )
                    signals.append(sig)

        if not signals:
            return None

        # เลือก signal ที่ score สูงสุด
        best = max(signals, key=lambda s: s.confluence_score)
        self.last_signal = best
        return best

    def summary(self, sig: KivancSignal) -> str:
        ob  = sig.order_block
        fib = sig.fibo_zone
        return (
            f"🎯 {sig.label}\n"
            f"   Entry : {sig.entry_price:.2f}\n"
            f"   SL    : {sig.sl_price:.2f}\n"
            f"   TP1   : {sig.tp1_price:.2f} (38.2%)\n"
            f"   TP2   : {sig.tp2_price:.2f} (61.8%)\n"
            f"   OB    : {ob.ob_low:.2f} - {ob.ob_high:.2f}"
            f" | VSA:{ob.vsa_confirmed} | InGolden:{ob.in_golden}\n"
            f"   Fibo  : {fib.anchor_low:.2f} - {fib.anchor_high:.2f}"
            f" | Golden:{fib.golden_bot:.2f}-{fib.golden_top:.2f}\n"
            f"   Score : {sig.confluence_score}/10"
        )


# ── Singleton ──────────────────────────────────────────────
kivanc_engine = KivancVSAEngine(min_score=5)


def run_kivanc(df: pd.DataFrame) -> Optional[KivancSignal]:
    """Entry point สำหรับ signal_composer เรียกใช้"""
    return kivanc_engine.analyze(df)
