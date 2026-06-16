"""
signal_composer.py — Alpha Buffalo v5.4 (Tunnel & Golden Zone Integrated)
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo

# Engines
from kivanc_vsaob      import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine      import run_micro, MicroSignal

# v5.3 Modules
from session_clock      import H4SessionTracker, get_market_session_info
from score_manager_v5p3 import ScoreManager
from ASIA_TUNING_v5p3   import ASITuningManager, ASIAScalpTriggerGate

# 🆕 Blueprint
from scenario_scanner import ScenarioBlueprint

BKK = ZoneInfo("Asia/Bangkok")

# ── Config ────────────────────────────────────────────────
COMPOSER_CONFIG = {
    "basket_layer_1":   0.618,
    "basket_layer_2":   0.786,
    "basket_kill":      0.886,
    "lot_layer_1":      1.0,
    "lot_layer_2":      1.5,
    "bb_period":        20,
    "bb_std":           2.0,
}

@dataclass
class BasketState:
    direction: str
    layer: int = 0
    entry_1: float = 0.0
    entry_2: float = 0.0
    lot_1: float = 0.0
    lot_2: float = 0.0
    sl: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    active: bool = False
    killed: bool = False

@dataclass
class ComposedSignal:
    direction: str
    signal_type: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    lot_multiplier: float
    basket_layer: int
    confluence_score: int
    sources: list = field(default_factory=list)
    label: str = ""
    timestamp: str = ""

def calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    close = df["close"]
    mid   = close.rolling(period).mean().iloc[-1]
    s     = close.rolling(period).std().iloc[-1]
    return {"upper": mid + std * s, "mid": mid, "lower": mid - std * s}

def calc_exits(direction: str, entry: float, prz_zones: List[PRZZone], bb: dict,
               kivanc_sig: Optional[KivancSignal], blueprint: Optional[ScenarioBlueprint] = None):
    """
    คำนวณ SL/TP โดยรวม Tunnel, Golden Zone จาก Blueprint (ถ้ามี)
    """
    if direction == "BUY":
        sl = entry - 2.0   # fallback
        tp1 = entry + 3.0
        tp2 = bb["upper"]

        # 1. ใช้ Kivanç ถ้ามี
        if kivanc_sig and kivanc_sig.direction == "BUY":
            sl = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = max(kivanc_sig.tp2_price, bb["upper"])

        # 2. ปรับด้วย Tunnel + Golden Zone จาก Blueprint
        if blueprint and blueprint.tunnel_valid:
            # SL: แนวรับที่แข็งที่สุด (สูงสุดของ lower bounds)
            supports = [sl]
            if blueprint.tunnel_lower > 0: supports.append(blueprint.tunnel_lower)
            if blueprint.golden_zone_low > 0: supports.append(blueprint.golden_zone_low)
            sl = max(supports) * 0.999  # buffer เล็กน้อย
            # TP1: แนวต้านที่ใกล้ที่สุด (ต่ำสุดของ upper bounds)
            resistances = [tp1]
            if blueprint.tunnel_upper > entry: resistances.append(blueprint.tunnel_upper)
            if blueprint.golden_zone_high > entry: resistances.append(blueprint.golden_zone_high)
            tp1 = min(resistances)
            # TP2: ใช้ PRZ หรือ plan_b_tp2
            if blueprint.plan_b_tp2 > entry:
                tp2 = blueprint.plan_b_tp2

        elif blueprint:  # tunnel not valid แต่มี golden zone
            if blueprint.golden_zone_low > sl:
                sl = blueprint.golden_zone_low * 0.999
            if blueprint.golden_zone_high > entry and blueprint.golden_zone_high < tp1:
                tp1 = blueprint.golden_zone_high

    else:  # SELL
        sl = entry + 2.0
        tp1 = entry - 3.0
        tp2 = bb["lower"]

        if kivanc_sig and kivanc_sig.direction == "SELL":
            sl = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = min(kivanc_sig.tp2_price, bb["lower"])

        if blueprint and blueprint.tunnel_valid:
            # SL: แนวต้านที่ต่ำที่สุด (ต่ำสุดของ upper bounds)
            resistances = [sl]
            if blueprint.tunnel_upper > 0: resistances.append(blueprint.tunnel_upper)
            if blueprint.golden_zone_high > 0: resistances.append(blueprint.golden_zone_high)
            sl = min(resistances) * 1.001
            # TP1: แนวรับที่สูงที่สุด (สูงสุดของ lower bounds)
            supports = [tp1]
            if blueprint.tunnel_lower < entry: supports.append(blueprint.tunnel_lower)
            if blueprint.golden_zone_low < entry: supports.append(blueprint.golden_zone_low)
            tp1 = max(supports)
            if blueprint.plan_b_tp2 < entry:
                tp2 = blueprint.plan_b_tp2

        elif blueprint:
            if blueprint.golden_zone_high < sl:
                sl = blueprint.golden_zone_high * 1.001
            if blueprint.golden_zone_low < entry and blueprint.golden_zone_low > tp1:
                tp1 = blueprint.golden_zone_low

    return sl, tp1, tp2

# ── Main Composer ─────────────────────────────────────────
class SignalComposer:
    def __init__(self):
        self.buy_basket  = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal: Optional[ComposedSignal] = None

    def compose(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame,
                blueprint: Optional[ScenarioBlueprint] = None) -> Optional[ComposedSignal]:
        session_info = get_market_session_info()
        current_session = session_info["session"]
        h4_info = H4SessionTracker.get_h4_boundary()
        current_price = float(df_15m["close"].iloc[-1])

        # 1. รัน Engines ย่อย
        kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)
        micro_sigs = run_micro(df_15m)

        # 2. ScoreManager
        score_data = ScoreManager.evaluate(
            df_15m=df_15m, df_1h=df_1h, df_4h=df_4h, current_session=current_session
        )
        if not score_data or not score_data.get('is_tradable'):
            return None

        best_dir = score_data['direction']
        best_score = score_data['total_score']
        signal_type = score_data.get('signal_type', 'V5_STANDARD')

        # 🆕 ปรับคะแนนจาก Blueprint
        if blueprint and blueprint.tunnel_valid:
            if best_dir == "BUY" and blueprint.tunnel_lower > 0 and current_price <= blueprint.tunnel_lower * 1.02:
                best_score += 2  # อยู่ใกล้แนวรับแข็งแรง
            elif best_dir == "SELL" and blueprint.tunnel_upper > 0 and current_price >= blueprint.tunnel_upper * 0.98:
                best_score += 2
        if blueprint and blueprint.bos_triggered:
            best_score += 3  # BOS ยืนยัน
        # ถ้าคะแนนติดลบหรือต่ำไป ให้ยกเลิก
        if best_score < 3:
            return None

        # 3. ASIA Tuning Gates
        if current_session == "ASIA":
            if not ASIAScalpTriggerGate.verify_sweep(micro_sigs):
                return None
            if not ASITuningManager.is_within_safe_time(h4_info["current_hour_utc"]):
                return None

        # 4. Basket Layer
        basket = self.buy_basket if best_dir == "BUY" else self.sell_basket
        if basket.killed:
            return None
        layer = basket.layer + 1
        lot_multi = COMPOSER_CONFIG[f"lot_layer_{min(layer, 2)}"]
        if layer > 2:
            return None

        # 5. คำนวณจุดออก
        if current_session == "ASIA" and (blueprint is None or not blueprint.tunnel_valid):
            # ใช้ ASITuning เฉพาะเมื่อไม่มี Tunnel ที่เชื่อถือได้
            sl, tp1, tp2 = ASITuningManager.calculate_dynamic_exits(best_dir, current_price, df_15m)
        else:
            bb = calc_bb(df_15m, COMPOSER_CONFIG["bb_period"], COMPOSER_CONFIG["bb_std"])
            sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig, blueprint)

        # 6. สร้างแพ็กเกจสัญญาณ
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
            sources=[f"ScoreManager({best_score})", "Blueprint" if blueprint else "Legacy"],
            timestamp=now,
            label=f"🎯 {signal_type} {best_dir} | Layer:{layer} | Score:{best_score} | Session:{current_session}",
        )

        basket.layer = layer
        basket.active = True
        basket.sl, basket.tp1, basket.tp2 = sl, tp1, tp2
        self.last_signal = sig
        return sig

    def kill_basket(self, direction: str):
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.killed = True
        basket.active = False
        basket.layer  = 0

    def reset_basket(self, direction: str):
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.__init__(direction=direction)

# ── Singleton ─────────────────────────────────────────────
composer = SignalComposer()

def compose_signal(df_4h, df_1h, df_15m, blueprint: Optional[ScenarioBlueprint] = None):
    return composer.compose(df_4h, df_1h, df_15m, blueprint)

def kill_basket(direction: str):
    composer.kill_basket(direction)

def reset_basket(direction: str):
    composer.reset_basket(direction)

# ── P0 FIX: Entry Fill Timing ──
def get_fill_price(signal_bar, next_bar=None):
    if next_bar is not None:
        return next_bar['open']
    return signal_bar['close']

def compose_signal_v10(signal, df, equity, dd_pct):
    try:
        from alpha_buffalo_signal import V10_READY, V10_CONFIG, AdaptiveEngine, PositionSizer
        if V10_READY:
            adaptive = AdaptiveEngine(V10_CONFIG)
            regime = adaptive.update_regime(df)
            signal['regime'] = regime
            signal['threshold'] = adaptive.get_score_threshold()
    except ImportError:
        pass
    return signal
