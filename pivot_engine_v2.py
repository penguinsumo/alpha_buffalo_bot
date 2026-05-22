"""
pivot_engine_v2.py — Alpha Buffalo v5
================================================
Real Pivot Detection + Confluence Scoring + Basket Risk Management

Layer stack:
  A. Pivot Detection   — structural (left/right) + macro (144 bars)
  B. Confluence Score  — Fibo D_max rescale + Deep Zone + Institutional bonus + VSA
  C. Basket State      — zone tracking, partial close, Fibo-based trailing SL
  D. TP Extension      — −118% TP1 (70%) / −146% TP2 (30%) + ATR SL

Design principle:
  "ระบบป้องกันก่อน กำไรทีหลัง"
  - BOS reset ทันที ไม่รอ
  - Partial close ทุกครั้งที่ Fibo checkpoint ผ่าน
  - Trailing SL lock ทุน ก่อนไล่ TP2
  - ไม่เพิ่ม lot ถ้า basket stress สูง

Usage:
    from pivot_engine_v2 import PivotEngine
    engine = PivotEngine()
    state  = engine.update(df, basket)   # ส่ง DataFrame OHLC + BasketState
"""

from __future__ import annotations
from fvg_detector import FVGDetector
from dataclasses import dataclass, field
from typing import Optional
import pandas as pd
import numpy as np


# ════════════════════════════════════════════════════════
# CONSTANTS
# ════════════════════════════════════════════════════════

FIBO_RATIOS      = [0.236, 0.382, 0.500, 0.618, 0.786]
LOT_MULTIPLIERS  = [1, 2, 4, 8]          # Martingale sequence per zone
DEEP_RATIOS      = [0.786, 1.272, 1.618] # extension ต่ำกว่า Swing Low

D_MAX            = 0.05   # normal zone: ห่าง 5% ของ R → score 0
D_DEEP           = 0.20   # deep zone:   ห่าง 20% ของ R → score 0
INST_ZONE_LOW    = 0.720  # institutional SL-sweep band ล่าง
INST_ZONE_HIGH   = 0.786  # institutional SL-sweep band บน
INST_BONUS       = 20     # bonus score เมื่ออยู่ใน institutional zone
VSA_BONUS        = 15     # bonus score เมื่อ VSA spike confirm
VSA_SIGMA        = 2.0    # volume spike threshold (std devs)
VSA_WICK_RATIO   = 1.5    # wick ต้องยาวกว่า body กี่เท่า
ATR_PERIOD       = 14
ATR_SL_MULT      = 1.5    # SL = locked_low − 1.5 × ATR
TP1_EXT          = 0.18   # −118% extension จาก locked_low
TP2_EXT          = 0.46   # −146% extension จาก locked_low
TP1_CLOSE_PCT    = 0.70   # ปิด 70% ที่ TP1
TP2_CLOSE_PCT    = 0.30   # ปิด 30% ที่ TP2
SCORE_THRESHOLD  = 70     # minimum score ก่อนยิง signal
MACRO_LOOKBACK   = 144    # bars สำหรับ macro pivot
BOS_BUFFER       = 0.001  # 0.1% tolerance ก่อน trigger BOS


# ════════════════════════════════════════════════════════
# DATA CLASSES
# ════════════════════════════════════════════════════════

@dataclass
class Zone:
    """Fibo zone หนึ่งระดับ"""
    index:          int
    fibo_ratio:     float
    price:          float
    lot_multiplier: int
    label:          str
    triggered:      bool  = False   # เคยแตะแล้วหรือยัง (ใช้ใน trailing)


@dataclass
class BasketState:
    """
    State ของ Martingale Basket ทั้งหมด
    engine อ่าน/เขียน object นี้ทุก tick
    consumer (bot) ดึง action_queue ไปส่ง order
    """
    # ── position tracking ──
    open_orders:    list  = field(default_factory=list)  # [{id, lot, price, side}]
    total_lot:      float = 0.0
    avg_entry:      float = 0.0
    floating_pnl:   float = 0.0
    basket_open:    bool  = False

    # ── trailing state ──
    trailing_active:   bool  = False
    trailing_sl:       Optional[float] = None   # SL ปัจจุบันของ basket รวม
    tp1_hit:           bool  = False
    tp2_hit:           bool  = False
    partial_closed_pct: float = 0.0             # % ที่ปิดไปแล้ว

    # ── stress indicator ──
    layers_open:    int   = 0     # จำนวน add ที่เปิดอยู่
    max_layers:     int   = 4     # hard limit
    stress_level:   str   = "low" # "low" | "medium" | "high" | "critical"

    # ── action queue (bot จะ pop ไปทำ) ──
    action_queue:   list  = field(default_factory=list)

    def add_action(self, action_type: str, **kwargs):
        self.action_queue.append({"type": action_type, **kwargs})

    def clear_actions(self):
        self.action_queue.clear()

    def stress_ok_to_add(self) -> bool:
        """ตรวจว่าเพิ่ม lot ได้ไหม — หลักป้องกันสำคัญ"""
        return (
            self.layers_open < self.max_layers and
            self.stress_level not in ("high", "critical")
        )


@dataclass
class PivotState:
    """State ของ Pivot + Scoring ทั้งหมด"""
    # ── structural pivot ──
    locked_high:    Optional[float] = None
    locked_low:     Optional[float] = None
    high_time:      Optional[pd.Timestamp] = None
    low_time:       Optional[pd.Timestamp] = None
    trend_dir:      str   = "sideways"

    # ── macro pivot (144 bars) ──
    macro_high:     Optional[float] = None
    macro_low:      Optional[float] = None

    # ── scoring ──
    fibo_score:     int   = 0
    fibo_level:     Optional[float] = None
    fibo_ratio:     Optional[float] = None
    in_deep_zone:   bool  = False
    in_inst_zone:   bool  = False
    vsa_signal:     str   = "none"   # "stopping_volume" | "upthrust" | "none"
    confluence:     int   = 0        # final capped score

    # ── structure ──
    bos_detected:   bool  = False
    basket_range:   Optional[float] = None
    zones:          list  = field(default_factory=list)

    # ── risk output ──
    sl:             Optional[float] = None
    tp1:            Optional[float] = None
    tp2:            Optional[float] = None
    atr:            Optional[float] = None

    def is_ready(self) -> bool:
        return self.locked_high is not None and self.locked_low is not None

    def signal_valid(self) -> bool:
        return (
            self.is_ready() and
            not self.bos_detected and
            self.trend_dir != "sideways" and
            self.confluence >= SCORE_THRESHOLD
        )

    def to_dict(self) -> dict:
        return {
            "locked_high":   self.locked_high,
            "locked_low":    self.locked_low,
            "macro_high":    self.macro_high,
            "macro_low":     self.macro_low,
            "trend_dir":     self.trend_dir,
            "confluence":    self.confluence,
            "fibo_score":    self.fibo_score,
            "fibo_ratio":    self.fibo_ratio,
            "fibo_level":    self.fibo_level,
            "in_deep_zone":  self.in_deep_zone,
            "in_inst_zone":  self.in_inst_zone,
            "vsa_signal":    self.vsa_signal,
            "bos_detected":  self.bos_detected,
            "basket_range":  self.basket_range,
            "sl":            self.sl,
            "tp1":           self.tp1,
            "tp2":           self.tp2,
            "atr":           self.atr,
            "signal_valid":  self.signal_valid(),
        }


# ════════════════════════════════════════════════════════
# PIVOT ENGINE v2
# ════════════════════════════════════════════════════════

class PivotEngine:
    """
    Alpha Buffalo v5 — Pivot Engine v2
    ใช้แทน rolling window ทั้งหมด

    Parameters
    ----------
    left, right   : structural pivot bars (default 5/5)
    macro_lookback: bars สำหรับ macro boundary (default 144)
    fibo_ratios   : levels ที่ใช้แบ่ง zone
    lot_multipliers: Martingale lot sequence
    """

    def __init__(
        self,
        left:  int = 5,
        right: int = 5,
        macro_lookback: int = MACRO_LOOKBACK,
        fibo_ratios:     list = None,
        lot_multipliers: list = None,
    ):
        self.left            = left
        self.right           = right
        self.macro_lookback  = macro_lookback
        self.fibo_ratios     = fibo_ratios     or FIBO_RATIOS
        self.lot_multipliers = lot_multipliers or LOT_MULTIPLIERS

        self.state  = PivotState()
        self.basket = BasketState()
        self.fvg    = FVGDetector()

    # ════════════════════════════════════
    # PUBLIC API
    # ════════════════════════════════════

    def update(
        self,
        df:     pd.DataFrame,
        basket: Optional[BasketState] = None,
    ) -> tuple[PivotState, BasketState]:
        """
        เรียกทุก tick / bar ใหม่
        df: columns [time, open, high, low, close, volume]
        basket: ส่ง BasketState จากภายนอก หรือใช้ internal

        คืน (PivotState, BasketState) พร้อม action_queue
        """
        if basket is not None:
            self.basket = basket

        min_bars = max(self.left + self.right + 1, ATR_PERIOD + 1)
        if len(df) < min_bars:
            return self.state, self.basket

        self.basket.clear_actions()
        self.state.bos_detected = False

        # ── A. Pivot Detection ──
        self._scan_structural_pivot(df)
        self._scan_macro_pivot(df)
        self._update_trend()

        # ── B. Confluence Scoring ──
        price = float(df['close'].iloc[-1])
        self._calculate_atr(df)
        self._calculate_fibo_score(price)
        self._check_institutional_zone(price)
        self._check_vsa(df)
        self._build_confluence()
        self._build_zones()
        self._calculate_risk_levels(df)

        # ── C. BOS Check (หลัง score เพื่อให้ consumer เห็น state สุดท้าย) ──
        self._check_bos(df)

        # ── D. Basket Risk Management ──
        if self.basket.basket_open:
            self._manage_basket(price)

        return self.state, self.basket

    def reset(self):
        """Manual reset — /reset_basket command"""
        self.state  = PivotState()
        self.basket = BasketState()
        self.fvg    = FVGDetector()

    def get_zone_for_price(self, price: float, tolerance_pct: float = 0.05) -> Optional[Zone]:
        """ราคาอยู่ใน zone ไหน — ใช้ใน bot ตอนตัดสิน add lot"""
        if not self.state.zones or not self.state.is_ready():
            return None
        R = self.state.locked_high - self.state.locked_low
        tol = R * tolerance_pct
        for z in self.state.zones:
            if abs(price - z.price) <= tol:
                return z
        return None

    def summary(self) -> str:
        s = self.state
        b = self.basket
        if not s.is_ready():
            return "PivotEngine v2: รอ pivot confirm..."
        lines = [
            f"=== Pivot State | trend: {s.trend_dir.upper()} ===",
            f"  Structural  H: {s.locked_high}  L: {s.locked_low}  Range: {s.basket_range}",
            f"  Macro       H: {s.macro_high}   L: {s.macro_low}",
            f"  Confluence  : {s.confluence}/100  (fibo:{s.fibo_score} inst:{s.in_inst_zone} vsa:{s.vsa_signal})",
            f"  Fibo level  : {s.fibo_ratio} @ {s.fibo_level}",
            f"  Deep zone   : {s.in_deep_zone}",
            f"  BOS         : {'YES — RESET' if s.bos_detected else 'No'}",
            f"  SL/TP1/TP2  : {s.sl} / {s.tp1} / {s.tp2}",
            f"  Signal valid: {s.signal_valid()}",
            f"--- Basket ---",
            f"  Open: {b.basket_open}  Layers: {b.layers_open}/{b.max_layers}",
            f"  Stress: {b.stress_level}  Trailing: {b.trailing_active}",
            f"  Trailing SL: {b.trailing_sl}",
            f"  TP1 hit: {b.tp1_hit}  TP2 hit: {b.tp2_hit}",
            f"  Closed: {b.partial_closed_pct*100:.0f}%",
        ]
        if b.action_queue:
            lines.append(f"  Actions: {[a['type'] for a in b.action_queue]}")
        return "\n".join(lines)


    # ════════════════════════════════════
    # A. PIVOT DETECTION
    # ════════════════════════════════════

    def _scan_structural_pivot(self, df: pd.DataFrame):
        highs = df['high'].values
        lows  = df['low'].values
        times = df.index if 'time' not in df.columns else df['time']

        idx = len(df) - self.right - 1
        if idx < self.left:
            return

        # ── Swing High ──
        h_val    = highs[idx]
        h_window = highs[idx - self.left : idx + self.right + 1]
        if h_val == h_window.max() and np.sum(h_window == h_val) == 1:
            t = times.iloc[idx] if hasattr(times, 'iloc') else times[idx]
            if self.state.locked_high is None or h_val > self.state.locked_high:
                self.state.locked_high = float(h_val)
                self.state.high_time   = t

        # ── Swing Low ──
        l_val    = lows[idx]
        l_window = lows[idx - self.left : idx + self.right + 1]
        if l_val == l_window.min() and np.sum(l_window == l_val) == 1:
            t = times.iloc[idx] if hasattr(times, 'iloc') else times[idx]
            if self.state.locked_low is None or l_val < self.state.locked_low:
                self.state.locked_low = float(l_val)
                self.state.low_time   = t

    def _scan_macro_pivot(self, df: pd.DataFrame):
        n = min(self.macro_lookback, len(df))
        self.state.macro_high = float(df['high'].tail(n).max())
        self.state.macro_low  = float(df['low'].tail(n).min())

    def _update_trend(self):
        if not self.state.is_ready():
            self.state.trend_dir = "sideways"
            return
        if self.state.high_time is None or self.state.low_time is None:
            self.state.trend_dir = "sideways"
            return
        self.state.trend_dir = (
            "up"   if self.state.high_time > self.state.low_time
            else "down"
        )


    # ════════════════════════════════════
    # B. CONFLUENCE SCORING
    # ════════════════════════════════════

    def _calculate_atr(self, df: pd.DataFrame):
        tr = pd.concat([
            df['high'] - df['low'],
            (df['high'] - df['close'].shift()).abs(),
            (df['low']  - df['close'].shift()).abs(),
        ], axis=1).max(axis=1)
        self.state.atr = float(tr.tail(ATR_PERIOD).mean())

    def _calculate_fibo_score(self, price: float):
        """
        Normal zone: score = max(0, (1 − dist_pct / D_MAX) × 100)
        Deep zone  : score = max(0, (1 − deep_dist / D_DEEP) × 100)
        Final      : max(fibo_score, deep_score)
        """
        if not self.state.is_ready():
            self.state.fibo_score   = 0
            self.state.in_deep_zone = False
            return

        R = self.state.locked_high - self.state.locked_low
        if R <= 0:
            return

        self.state.basket_range = round(R, 5)

        # ── Normal Fibo zones ──
        levels = {
            r: self.state.locked_high - R * r
            for r in self.fibo_ratios
        }
        nearest_r     = min(levels, key=lambda r: abs(price - levels[r]))
        nearest_price = levels[nearest_r]
        dist_pct      = abs(price - nearest_price) / R
        fibo_score    = max(0, round((1 - dist_pct / D_MAX) * 100))

        # ── Deep Zone (ราคาต่ำกว่า Swing Low) ──
        deep_score = 0
        in_deep    = price < self.state.locked_low

        if in_deep:
            deep_dist  = abs(price - self.state.locked_low) / R
            deep_score = max(0, round((1 - deep_dist / D_DEEP) * 100))

            # หา deep extension ที่ใกล้ที่สุด
            deep_levels = {
                r: self.state.locked_low - R * (r - 1.0)
                for r in DEEP_RATIOS
            }
            nearest_deep_r = min(
                deep_levels,
                key=lambda r: abs(price - deep_levels[r])
            )
            nearest_r     = nearest_deep_r
            nearest_price = round(deep_levels[nearest_deep_r], 5)

        self.state.in_deep_zone = in_deep
        self.state.fibo_score   = max(fibo_score, deep_score)
        self.state.fibo_ratio   = nearest_r
        self.state.fibo_level   = round(nearest_price, 5)

    def _check_institutional_zone(self, price: float):
        """72–78.6% band → SL sweep zone → bonus +20"""
        if not self.state.is_ready():
            self.state.in_inst_zone = False
            return
        R          = self.state.locked_high - self.state.locked_low
        band_low   = self.state.locked_high - R * INST_ZONE_HIGH
        band_high  = self.state.locked_high - R * INST_ZONE_LOW
        self.state.in_inst_zone = band_low <= price <= band_high

    def _check_vsa(self, df: pd.DataFrame):
        """
        Stopping Volume → Buy setup  (+15 bonus)
        Upthrust        → Sell setup (+15 bonus)
        ต้องมี column 'volume' ใน df
        """
        self.state.vsa_signal = "none"
        if 'volume' not in df.columns or len(df) < 20:
            return

        last     = df.iloc[-1]
        vol_tail = df['volume'].tail(20)
        vol_mean = vol_tail.mean()
        vol_std  = vol_tail.std()

        if vol_std == 0:
            return

        is_spike = float(last['volume']) > vol_mean + VSA_SIGMA * vol_std
        if not is_spike:
            return

        body    = abs(float(last['close']) - float(last['open']))
        lo_wick = float(last['open'] - last['low']   if last['close'] > last['open']
                        else last['close'] - last['low'])
        hi_wick = float(last['high'] - last['close'] if last['close'] > last['open']
                        else last['high'] - last['open'])

        if body > 0:
            if lo_wick > body * VSA_WICK_RATIO:
                self.state.vsa_signal = "stopping_volume"
            elif hi_wick > body * VSA_WICK_RATIO:
                self.state.vsa_signal = "upthrust"

    def _build_confluence(self):
        """รวม score ทุก layer — cap ที่ 100"""
        score = self.state.fibo_score
        if self.state.in_inst_zone:
            score += INST_BONUS
        if self.state.vsa_signal != "none":
            score += VSA_BONUS
        self.state.confluence = min(100, score)

    def _build_zones(self):
        if not self.state.is_ready():
            self.state.zones = []
            return
        R     = self.state.locked_high - self.state.locked_low
        zones = []
        for i, ratio in enumerate(self.fibo_ratios[:len(self.lot_multipliers)]):
            price = self.state.locked_high - R * ratio
            zones.append(Zone(
                index          = i + 1,
                fibo_ratio     = ratio,
                price          = round(price, 5),
                lot_multiplier = self.lot_multipliers[i],
                label          = f"Zone {i+1} — {ratio:.3f} (lot x{self.lot_multipliers[i]})",
            ))
        self.state.zones = zones

    def _calculate_risk_levels(self, df: pd.DataFrame):
        """
        SL  = locked_low − ATR × ATR_SL_MULT
        TP1 = locked_low − R × TP1_EXT   (−118% extension)
        TP2 = locked_low − R × TP2_EXT   (−146% extension)
        """
        if not self.state.is_ready() or self.state.atr is None:
            return
        R = self.state.locked_high - self.state.locked_low
        self.state.sl  = round(self.state.locked_low - self.state.atr * ATR_SL_MULT, 5)
        self.state.tp1 = round(self.state.locked_low - R * TP1_EXT, 5)
        self.state.tp2 = round(self.state.locked_low - R * TP2_EXT, 5)


    # ════════════════════════════════════
    # BOS CHECK
    # ════════════════════════════════════

    def _check_bos(self, df: pd.DataFrame):
        if not self.state.is_ready():
            return
        price = float(df['close'].iloc[-1])

        broke = False
        if self.state.trend_dir == "up":
            threshold = self.state.locked_low * (1 - BOS_BUFFER)
            broke = price < threshold
        elif self.state.trend_dir == "down":
            threshold = self.state.locked_high * (1 + BOS_BUFFER)
            broke = price > threshold

        if not broke:
            return

        result = self.fvg.analyze(
            df,
            swing_high = self.state.locked_high,
            swing_low  = self.state.locked_low,
            trend_dir  = self.state.trend_dir,
        )

        if result.verdict == "HUNT":
            self.state.bos_detected = False
            self.basket.add_action(
                "LIQUIDITY_HUNT",
                trigger_price = price,
                confidence    = result.confidence,
                note          = result.reason,
            )
        elif result.verdict == "MSS":
            self.state.bos_detected = True
            self._on_bos(price, result)
        else:
            self.state.bos_detected = False
            self.basket.add_action(
                "STRUCTURE_WAIT",
                trigger_price = price,
                note          = result.reason,
            )

    def _on_bos(self, trigger_price: float, shift_result=None):
        fvg_zone = None
        if shift_result and shift_result.fvg:
            fvg_zone = {
                "top":       shift_result.fvg.top,
                "bottom":    shift_result.fvg.bottom,
                "direction": shift_result.fvg.direction,
            }
        self.basket.add_action(
            "MSS_CONFIRMED",
            trigger_price = trigger_price,
            fvg_zone      = fvg_zone,
            confidence    = shift_result.confidence if shift_result else 0,
            note          = shift_result.reason if shift_result else "MSS detected",
        )
        self.basket.stress_level = "critical"
        self.state.locked_high  = None
        self.state.locked_low   = None
        self.state.high_time    = None
        self.state.low_time     = None
        self.state.zones        = []
        self.state.basket_range = None
        self.state.trend_dir    = "sideways"


    # ════════════════════════════════════
    # C. BASKET RISK MANAGEMENT
    # "ระบบป้องกันก่อน กำไรทีหลัง"
    # ════════════════════════════════════

    def _manage_basket(self, price: float):
        """
        เรียกทุก tick ถ้า basket_open = True
        ลำดับ priority:
          1. TP2 check (กำไรเต็ม)
          2. TP1 check + partial close 70%
          3. Trailing SL update
          4. Fibo partial de-risk (แก้เกม Martingale)
          5. Stress assessment
        """
        if not self.state.is_ready():
            return

        self._update_stress()
        self._check_tp2(price)
        self._check_tp1(price)
        self._update_trailing_sl(price)
        self._check_fibo_partial_close(price)
        self._check_fibo_trailing_step(price)

    def _update_stress(self):
        """คำนวณ stress level จาก layers ที่เปิดอยู่"""
        n = self.basket.layers_open
        if n == 0:
            self.basket.stress_level = "low"
        elif n == 1:
            self.basket.stress_level = "low"
        elif n == 2:
            self.basket.stress_level = "medium"
        elif n == 3:
            self.basket.stress_level = "high"
        else:
            self.basket.stress_level = "critical"

    # ── TP2: ปิดที่เหลือทั้งหมด ──
    def _check_tp2(self, price: float):
        if self.basket.tp2_hit or self.state.tp2 is None:
            return
        if not self.basket.tp1_hit:
            return
        if price <= self.state.tp2:
            self.basket.tp2_hit = True
            self.basket.add_action(
                "CLOSE_ALL",
                reason    = "TP2 hit",
                price     = price,
                target    = self.state.tp2,
                close_pct = 1.0,
                note      = f"ปิดทั้งหมดที่ {price} — −146% extension",
            )

    # ── TP1: partial close 70% + เปิด trailing ──
    def _check_tp1(self, price: float):
        if self.basket.tp1_hit or self.state.tp1 is None:
            return
        if price <= self.state.tp1:
            self.basket.tp1_hit          = True
            self.basket.trailing_active  = True
            self.basket.partial_closed_pct += TP1_CLOSE_PCT

            # trailing SL เริ่มที่ locked_low (จุดเดิม = 0%)
            self.basket.trailing_sl = self.state.locked_low

            self.basket.add_action(
                "PARTIAL_CLOSE",
                reason    = "TP1 hit",
                price     = price,
                target    = self.state.tp1,
                close_pct = TP1_CLOSE_PCT,
                note      = f"ปิด 70% ที่ {price} — −118% extension  |  trailing เริ่ม @ {self.state.locked_low}",
            )

    # ── Trailing SL update ──
    def _update_trailing_sl(self, price: float):
        """
        Trailing SL เลื่อนตาม Fibo checkpoint:
        ราคาผ่าน 0.618 → SL ขยับมาที่ 0.500
        ราคาผ่าน 1.000 (locked_high) → SL ขยับมาที่ 0.786
        """
        if not self.basket.trailing_active or not self.state.is_ready():
            return

        R  = self.state.locked_high - self.state.locked_low
        p  = price
        lh = self.state.locked_high
        ll = self.state.locked_low

        lvl_618  = lh - R * 0.618
        lvl_500  = lh - R * 0.500
        lvl_786  = lh - R * 0.786
        lvl_100  = lh   # High เดิม

        new_sl = self.basket.trailing_sl

        # ราคาผ่าน locked_high → SL ขึ้นมาที่ 0.786
        if p >= lvl_100:
            candidate = lvl_786
            if new_sl is None or candidate > new_sl:
                new_sl = candidate
                self.basket.add_action(
                    "UPDATE_TRAILING_SL",
                    new_sl = round(new_sl, 5),
                    note   = f"ราคาผ่าน High → SL lock ที่ 0.786 = {round(new_sl,5)}",
                )

        # ราคาผ่าน 0.618 → SL ขึ้นมาที่ 0.500
        elif p >= lvl_618:
            candidate = lvl_500
            if new_sl is None or candidate > new_sl:
                new_sl = candidate
                self.basket.add_action(
                    "UPDATE_TRAILING_SL",
                    new_sl = round(new_sl, 5),
                    note   = f"ราคาผ่าน 0.618 → SL lock ที่ 0.500 = {round(new_sl,5)}",
                )

        self.basket.trailing_sl = new_sl

    # ── Partial De-risk: แก้เกม Martingale ──
    def _check_fibo_partial_close(self, price: float):
        """
        กลยุทธ์ที่ 1 (document):
        ถ้า layers >= 3 และราคาวิ่งกลับมาถึง Fibo checkpoint แรก
        → ปิด 50% ของ basket รวม เพื่อ de-risk
        เงื่อนไข: ยังไม่ถึง TP1 และ stress สูง
        """
        if self.basket.tp1_hit:
            return
        if self.basket.layers_open < 3:
            return
        if self.basket.stress_level not in ("high", "critical"):
            return
        if not self.state.is_ready():
            return

        R   = self.state.locked_high - self.state.locked_low
        # checkpoint แรกคือ 0.382 หรือ 0.500 (แล้วแต่ว่าราคาแตะถึงไหน)
        chk_382 = self.state.locked_high - R * 0.382
        chk_500 = self.state.locked_high - R * 0.500

        for chk, label in [(chk_382, "0.382"), (chk_500, "0.500")]:
            # ราคาวิ่งกลับถึง checkpoint (สำหรับ Buy basket — price ขึ้นมา)
            if price >= chk:
                # ตรวจว่า checkpoint นี้ยัง trigger ไหม (ไม่ queue ซ้ำ)
                action_labels = [a.get("checkpoint") for a in self.basket.action_queue]
                if label not in action_labels:
                    self.basket.add_action(
                        "PARTIAL_CLOSE",
                        reason     = f"Fibo de-risk checkpoint {label}",
                        price      = price,
                        checkpoint = label,
                        close_pct  = 0.50,
                        note       = f"Martingale {self.basket.layers_open} layers — ปิด 50% ที่ {price}",
                    )
                break

    # ── Fibo Trailing Step ──
    def _check_fibo_trailing_step(self, price: float):
        """
        กลยุทธ์ที่ 2 (document):
        Mark checkpoint ที่ราคาผ่านเพื่อ set trailing SL
        (ส่วน SL move จริงอยู่ใน _update_trailing_sl แล้ว)
        นี่แค่ queue notification ให้ bot รับรู้
        """
        if not self.state.is_ready() or self.basket.tp1_hit:
            return

        R = self.state.locked_high - self.state.locked_low
        for zone in self.state.zones:
            if not zone.triggered and price >= zone.price:
                zone.triggered = True
                self.basket.add_action(
                    "FIBO_CHECKPOINT_PASSED",
                    zone       = zone.index,
                    fibo_ratio = zone.fibo_ratio,
                    price      = price,
                    note       = f"ผ่าน {zone.fibo_ratio} @ {zone.price}",
                )


# ════════════════════════════════════════════════════════
# QUICK TEST
# ════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    np.random.seed(42)
    n = 80

    # simulate: up → swing high → pullback → swing low → recovery → TP zone
    trend = np.concatenate([
        np.linspace(1.0850, 1.1060, 15),   # up to swing high
        np.linspace(1.1060, 1.0770, 25),   # down to swing low
        np.linspace(1.0770, 1.1100, 40),   # recovery past high → TP territory
    ])

    vol_base = 1000 + np.random.randint(0, 500, n)
    vol_base[34] = 4500   # VSA spike at swing low

    df = pd.DataFrame({
        'time':   pd.date_range("2026-05-17", periods=n, freq="2min"),
        'open':   trend - 0.0003,
        'high':   trend + 0.0010,
        'low':    trend - 0.0010,
        'close':  trend,
        'volume': vol_base,
    })
    df.loc[14, 'high'] = 1.1065
    df.loc[34, 'low']  = 1.0765

    engine = PivotEngine(left=5, right=5)

    # simulate basket มีอยู่ (3 layers เปิดอยู่)
    engine.basket.basket_open  = True
    engine.basket.layers_open  = 3
    engine.basket.total_lot    = 7.0   # 1+2+4

    print("=" * 56)
    print("Feeding bars...")
    for i in range(11, len(df)):
        state, basket = engine.update(df.iloc[:i+1])

    print(engine.summary())
    print()

    print("── State dict (Supabase payload) ──")
    for k, v in state.to_dict().items():
        if k != "zones":
            print(f"  {k:16}: {v}")

    print(f"\n── Zones ({len(state.zones)}) ──")
    for z in state.zones:
        print(f"  {z.label}  @ {z.price}  triggered={z.triggered}")

    print(f"\n── Action Queue ({len(basket.action_queue)}) ──")
    for a in basket.action_queue:
        print(f"  [{a['type']}]  {a.get('note','')}")

    print("\n── Zone lookup ──")
    for test_p in [1.1040, 1.0998, 1.0949, 1.0918, 1.0880]:
        z = engine.get_zone_for_price(test_p)
        print(f"  {test_p} → {z.label if z else 'between zones'}")

    print("\nAll tests done.")
