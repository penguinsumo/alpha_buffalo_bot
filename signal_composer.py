"""
signal_composer.py — Alpha Buffalo v5
รวม v4+ Session Logic + v5 Sniper (Kivanc + Harmonic + Micro)

Flow:
  df_4h  → pivot_engine (structure) + harmonic_detector (PRZ)
  df_1h  → pivot_engine (fibo) + kivanc_vsaob (OB zone)
  df_15m → micro_engine (session H/L + sweep)
         → signal_composer (stack confluence → BUY/SELL Engine)
         → equity_guard → send_signal()

Exit:
  TP1 = 0.382 CD
  TP2 = BB Upper/Lower + Kivanc PRZ ใหม่
  Trail SL = BB Middle
  Kill Switch = ทะลุ 88.6% → lot=0

Basket Martingale:
  ชั้น 1: PRZ 61.8%  lot=1.0x
  ชั้น 2: PRZ 78.6%  lot=1.5x
  Kill  : PRZ 88.6%  close_all + lot=0
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

from kivanc_vsaob    import run_kivanc,    KivancSignal
from harmonic_detector import run_harmonic, PRZZone, get_active_prz
from micro_engine    import run_micro,     MicroSignal, get_micro_summary

BKK = timezone(timedelta(hours=7))


# ── Config ────────────────────────────────────────────────
COMPOSER_CONFIG = {
    "v4_min_score":     3,    # V4+ session signal threshold
    "v5_min_score":     6,    # V5 sniper threshold (สูงกว่า)
    "basket_layer_1":   0.618,
    "basket_layer_2":   0.786,
    "basket_kill":      0.886,
    "lot_layer_1":      1.0,
    "lot_layer_2":      1.5,
    "bb_period":        20,
    "bb_std":           2.0,
    "trail_sl_buffer":  0.30,  # USD
}


# ── Data Classes ──────────────────────────────────────────
@dataclass
class BasketState:
    """State ของ Basket Martingale แต่ละฝั่ง"""
    direction: str         # "BUY" or "SELL"
    layer: int = 0         # 0=ไม่มี position, 1=ชั้น1, 2=ชั้น2
    entry_1: float = 0.0
    entry_2: float = 0.0
    lot_1: float = 0.0
    lot_2: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    active: bool = False
    killed: bool = False   # lot=0 หลัง kill switch


@dataclass
class ComposedSignal:
    """Final signal output"""
    direction: str
    signal_type: str       # "V4_SESSION" or "V5_SNIPER"
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    lot_multiplier: float
    basket_layer: int
    confluence_score: int
    sources: list[str] = field(default_factory=list)
    label: str = ""
    timestamp: str = ""


# ── Bollinger Band ────────────────────────────────────────
def calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    close = df["close"]
    mid   = close.rolling(period).mean().iloc[-1]
    s     = close.rolling(period).std().iloc[-1]
    return {
        "upper": mid + std * s,
        "mid":   mid,
        "lower": mid - std * s,
    }


# ── Confluence Scorer ─────────────────────────────────────
def score_confluence(
    direction: str,
    kivanc_sig: Optional[KivancSignal],
    prz_zones:  list[PRZZone],
    micro_sigs: list[MicroSignal],
    current_price: float,
) -> tuple[int, list[str]]:
    """รวม score จากทุก source"""
    score   = 0
    sources = []

    # Kivanc VSA OB
    if kivanc_sig and kivanc_sig.direction == direction:
        score += kivanc_sig.confluence_score
        sources.append(f"Kivanc({kivanc_sig.confluence_score})")

    # Harmonic PRZ
    active_prz = [z for z in prz_zones if z.direction == direction and z.in_prz(current_price)]
    for z in active_prz:
        bonus = 4 - z.priority  # P1=3, P2=2, P3=1
        score += bonus
        sources.append(f"{z.pattern_name}({bonus})")

    # Micro Sweep
    micro_match = [m for m in micro_sigs if m.direction == direction]
    for m in micro_match:
        score += m.confluence_score
        sources.append(f"Sweep:{m.trigger}({m.confluence_score})")

    return score, sources


# ── Exit Price Calculator ─────────────────────────────────
def calc_exits(
    direction: str,
    entry: float,
    prz_zones: list[PRZZone],
    bb: dict,
    kivanc_sig: Optional[KivancSignal],
) -> tuple[float, float, float]:
    """
    คำนวณ SL, TP1, TP2
    SL   = beyond kill zone
    TP1  = 0.382 of nearest PRZ range
    TP2  = BB Upper/Lower หรือ PRZ ใหม่
    """
    if direction == "BUY":
        sl  = entry - 2.0   # default
        tp1 = entry + 3.0
        tp2 = bb["upper"]

        if kivanc_sig and kivanc_sig.direction == "BUY":
            sl  = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = max(kivanc_sig.tp2_price, bb["upper"])

        # ถ้ามี PRZ Bearish ใกล้ๆ ข้างบน = TP2
        bearish_prz = [z for z in prz_zones if z.direction == "SELL" and z.prz_mid > entry]
        if bearish_prz:
            nearest = min(bearish_prz, key=lambda z: z.prz_mid - entry)
            tp2 = min(tp2, nearest.prz_low)

    else:  # SELL
        sl  = entry + 2.0
        tp1 = entry - 3.0
        tp2 = bb["lower"]

        if kivanc_sig and kivanc_sig.direction == "SELL":
            sl  = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = min(kivanc_sig.tp2_price, bb["lower"])

        # ถ้ามี PRZ Bullish ใกล้ๆ ข้างล่าง = TP2
        bullish_prz = [z for z in prz_zones if z.direction == "BUY" and z.prz_mid < entry]
        if bullish_prz:
            nearest = min(bullish_prz, key=lambda z: entry - z.prz_mid)
            tp2 = max(tp2, nearest.prz_high)

    return sl, tp1, tp2


# ── Main Composer ─────────────────────────────────────────
class SignalComposer:
    def __init__(self):
        self.buy_basket  = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal: Optional[ComposedSignal] = None

    def compose(
        self,
        df_4h:  pd.DataFrame,
        df_1h:  pd.DataFrame,
        df_15m: pd.DataFrame,
    ) -> Optional[ComposedSignal]:
        """
        รับ 3 timeframes → คืน ComposedSignal หรือ None
        """
        current_price = float(df_15m["close"].iloc[-1])
        bb = calc_bb(df_15m, COMPOSER_CONFIG["bb_period"], COMPOSER_CONFIG["bb_std"])

        # ── Run all engines ────────────────────────────────
        kivanc_1h  = run_kivanc(df_1h)
        kivanc_4h  = run_kivanc(df_4h)
        kivanc_sig = kivanc_1h or kivanc_4h   # prefer 1H

        prz_4h = run_harmonic(df_4h)
        prz_1h = run_harmonic(df_1h)
        prz_zones = prz_4h + prz_1h           # รวมทั้ง 2 TF

        micro_sigs = run_micro(df_15m)

        # ── Score ทั้ง BUY และ SELL ────────────────────────
        buy_score,  buy_src  = score_confluence("BUY",  kivanc_sig, prz_zones, micro_sigs, current_price)
        sell_score, sell_src = score_confluence("SELL", kivanc_sig, prz_zones, micro_sigs, current_price)

        # ── V5 Sniper Check ────────────────────────────────
        v5_min = COMPOSER_CONFIG["v5_min_score"]
        v4_min = COMPOSER_CONFIG["v4_min_score"]

        best_dir   = None
        best_score = 0
        best_src   = []
        signal_type = "V4_SESSION"

        if buy_score >= v5_min and buy_score > sell_score:
            best_dir   = "BUY"
            best_score = buy_score
            best_src   = buy_src
            signal_type = "V5_SNIPER"
        elif sell_score >= v5_min and sell_score > buy_score:
            best_dir   = "SELL"
            best_score = sell_score
            best_src   = sell_src
            signal_type = "V5_SNIPER"
        elif buy_score >= v4_min and buy_score > sell_score:
            best_dir   = "BUY"
            best_score = buy_score
            best_src   = buy_src
            signal_type = "V4_SESSION"
        elif sell_score >= v4_min and sell_score > buy_score:
            best_dir   = "SELL"
            best_score = sell_score
            best_src   = sell_src
            signal_type = "V4_SESSION"

        if not best_dir:
            return None

        # ── Basket Layer ───────────────────────────────────
        basket = self.buy_basket if best_dir == "BUY" else self.sell_basket

        if basket.killed:
            return None  # lot=0 รอ reset

        layer     = basket.layer + 1
        lot_multi = COMPOSER_CONFIG[f"lot_layer_{min(layer, 2)}"]

        if layer > 2:
            return None  # Max 2 ชั้น

        # ── Exit Levels ────────────────────────────────────
        sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig)

        # ── Build Signal ───────────────────────────────────
        now = datetime.now(BKK).strftime("%H:%M:%S")
        sig = ComposedSignal(
            direction=best_dir,
            signal_type=signal_type,
            entry_price=current_price,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=tp2,
            lot_multiplier=lot_multi,
            basket_layer=layer,
            confluence_score=best_score,
            sources=best_src,
            timestamp=now,
            label=(
                f"{'🎯' if signal_type == 'V5_SNIPER' else '📡'} "
                f"{signal_type} {best_dir} | Layer:{layer} | "
                f"Score:{best_score} | {', '.join(best_src[:3])}"
            ),
        )

        # อัพเดท basket state
        basket.layer  = layer
        basket.active = True
        if layer == 1:
            basket.entry_1    = current_price
            basket.lot_1      = lot_multi
        else:
            basket.entry_2    = current_price
            basket.lot_2      = lot_multi
        basket.sl  = sl
        basket.tp1 = tp1
        basket.tp2 = tp2

        self.last_signal = sig
        return sig

    def kill_basket(self, direction: str):
        """เรียกเมื่อราคาทะลุ 88.6% → lot=0"""
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.killed = True
        basket.active = False
        basket.layer  = 0

    def reset_basket(self, direction: str):
        """Reset หลัง TP3 หรือ manual"""
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.__init__(direction=direction)

    def format_signal(self, sig: ComposedSignal) -> str:
        emoji = "🟢" if sig.direction == "BUY" else "🔴"
        return (
            f"{emoji} {sig.label}\n"
            f"   Entry : {sig.entry_price:.2f}\n"
            f"   SL    : {sig.sl_price:.2f}\n"
            f"   TP1   : {sig.tp1_price:.2f}\n"
            f"   TP2   : {sig.tp2_price:.2f}\n"
            f"   Lot   : x{sig.lot_multiplier} (Layer {sig.basket_layer}/2)\n"
            f"   Time  : {sig.timestamp}\n"
            f"   Score : {sig.confluence_score}"
        )


# ── Singleton ──────────────────────────────────────────────
composer = SignalComposer()


def compose_signal(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
) -> Optional[ComposedSignal]:
    """Entry point หลัก — เรียกจาก alpha_buffalo_signal.py"""
    return composer.compose(df_4h, df_1h, df_15m)


def format_composed(sig: ComposedSignal) -> str:
    return composer.format_signal(sig)


def kill_basket(direction: str):
    composer.kill_basket(direction)


def reset_basket(direction: str):
    composer.reset_basket(direction)
