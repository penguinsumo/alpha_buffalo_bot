"""
signal_composer.py — Alpha Buffalo v5.4 (Dow Theory Tunnel + Micro BOS + Harmonic V5 BOS Add-on)
- V4 Entry: PRZ/Golden Zone + BOS Breakout Add-on
- V5 Entry: PRZ/Golden Zone only (before BOS confirmation)
- 🆕 V5 BOS Mode: Open new V5 at BOS if Harmonic Projection confirms strong continuation
"""

import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo

# Engines
from kivanc_vsaob      import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone, recalculate_prz_after_bos
from micro_engine      import run_micro, MicroSignal

# v5.3 Modules
from session_clock      import H4SessionTracker, get_market_session_info
from ASIA_TUNING_v5p3   import ASITuningManager, ASIAScalpTriggerGate

# ScoreManager (actual)
from score_manager_v5p3 import ScoreManager, THRESHOLD_V4, THRESHOLD_V5

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
    v5_alive: bool = False
    v4_alive: bool = False
    v4_partial_closed: bool = False
    v5_count: int = 0  # Track number of V5 entries for BOS mode

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
    if direction == "BUY":
        sl = entry - 2.0
        tp1 = entry + 3.0
        tp2 = bb["upper"]

        if kivanc_sig and kivanc_sig.direction == "BUY":
            sl = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = max(kivanc_sig.tp2_price, bb["upper"])

        if blueprint and blueprint.tunnel_status == "CONFIRMED":
            supports = [sl]
            if blueprint.tunnel_lower > 0: supports.append(blueprint.tunnel_lower)
            if blueprint.golden_zone_low > 0: supports.append(blueprint.golden_zone_low)
            sl = max(supports) * 0.999
            resistances = [tp1]
            if blueprint.tunnel_upper > entry: resistances.append(blueprint.tunnel_upper)
            if blueprint.golden_zone_high > entry: resistances.append(blueprint.golden_zone_high)
            tp1 = min(resistances)
            if blueprint.plan_b_tp2 > entry:
                tp2 = blueprint.plan_b_tp2
        elif blueprint:
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

        if blueprint and blueprint.tunnel_status == "CONFIRMED":
            resistances = [sl]
            if blueprint.tunnel_upper > 0: resistances.append(blueprint.tunnel_upper)
            if blueprint.golden_zone_high > 0: resistances.append(blueprint.golden_zone_high)
            sl = min(resistances) * 1.001
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

class SignalComposer:
    def __init__(self):
        self.buy_basket  = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal: Optional[ComposedSignal] = None
        self.score_mgr = ScoreManager()

    def compose(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame,
                blueprint: Optional[ScenarioBlueprint] = None) -> Optional[ComposedSignal]:
        session_info = get_market_session_info()
        current_session = session_info["session"]
        h4_info = H4SessionTracker.get_h4_boundary()
        current_price = float(df_15m["close"].iloc[-1])

        # 1. Run engines
        kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)
        micro_sigs = run_micro(df_15m)

        # 2. Real BOS from Micro Engine
        micro_bos = any(s.bos for s in micro_sigs) if micro_sigs else False

        # 3. ScoreManager (base)
        kivanc_score = 1 if kivanc_sig else 0
        vsa_ok = False
        score_result = self.score_mgr.calculate(
            kivanc_score=kivanc_score,
            bos_detected=micro_bos,
            vsa_ok=vsa_ok
        )
        base_score = score_result.total
        if not score_result.is_tradable and base_score < THRESHOLD_V4:
            return None

        # 4. Direction
        if kivanc_sig and kivanc_sig.direction in ("BUY", "SELL"):
            best_dir = kivanc_sig.direction
        elif blueprint:
            best_dir = "BUY" if blueprint.trend_h4 == "UP" else "SELL"
        else:
            return None

        basket = self.buy_basket if best_dir == "BUY" else self.sell_basket

        # 5. Blueprint Boost (Tunnel CONFIRMED only)
        score_boost = 0
        if blueprint and blueprint.tunnel_status == "CONFIRMED":
            if best_dir == "BUY" and current_price <= blueprint.tunnel_lower * 1.02:
                score_boost += 2
            elif best_dir == "SELL" and current_price >= blueprint.tunnel_upper * 0.98:
                score_boost += 2
        best_score = base_score + score_boost
        if best_score < THRESHOLD_V4:
            return None

        # 6. Signal type assignment
        # 🆕 V5 BOS Mode: Harmonic Projection confirms strong continuation
        v5_bos_mode = False
        if micro_bos:
            # Check Harmonic Projection for V5 at BOS
            if blueprint and blueprint.swing_L and blueprint.swing_H and blueprint.swing_HL:
                prz_new, _ = recalculate_prz_after_bos(
                    blueprint.swing_L, blueprint.swing_H, blueprint.swing_HL,
                    current_price, best_dir
                )
                if prz_new:
                    # V5 BOS Mode: New PRZ confirmed after BOS
                    v5_bos_mode = True
                    signal_type = "V5_SNIPER"
                    # Use new PRZ for TP
                    self._v5_bos_tp = prz_new.prz_mid
                else:
                    signal_type = "V4_SCALP"
            else:
                signal_type = "V4_SCALP"
        else:
            # Pre-BOS (PRZ Zone)
            if best_score >= THRESHOLD_V5:
                signal_type = "V5_SNIPER"
            else:
                signal_type = "V4_SCALP"

        # 7. ASIA Gates
        if current_session == "ASIA":
            if not ASIAScalpTriggerGate.verify_sweep(micro_sigs):
                return None
            if not ASITuningManager.is_within_safe_time(h4_info["current_hour_utc"]):
                return None

        # 8. Basket Layer
        if basket.killed:
            return None
        layer = basket.layer + 1
        lot_multi = COMPOSER_CONFIG[f"lot_layer_{min(layer, 2)}"]
        if layer > 2:
            return None

        # 9. Exits
        if current_session == "ASIA" and (blueprint is None or blueprint.tunnel_status != "CONFIRMED"):
            sl, tp1, tp2 = ASITuningManager.calculate_dynamic_exits(best_dir, current_price, df_15m)
        else:
            bb = calc_bb(df_15m, COMPOSER_CONFIG["bb_period"], COMPOSER_CONFIG["bb_std"])
            sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig, blueprint)
            # Override TP2 with V5 BOS PRZ if available
            if v5_bos_mode and hasattr(self, '_v5_bos_tp'):
                tp2 = self._v5_bos_tp

        # 10. Update basket state
        if signal_type == "V5_SNIPER":
            basket.v5_alive = True
            basket.v5_count += 1
        elif signal_type == "V4_SCALP":
            if not basket.v4_alive:
                basket.v4_alive = True
                basket.v4_partial_closed = False

        basket.layer = layer
        basket.active = True
        basket.sl, basket.tp1, basket.tp2 = sl, tp1, tp2

        # 11. Package
        now = datetime.now(BKK).strftime("%H:%M:%S")
        bos_tag = "[BOS-V5]" if v5_bos_mode else ""
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
            sources=[f"Base({base_score})+Boost({score_boost})", "Kivanc" if kivanc_sig else "Trend", 
                     "MicroBOS" if micro_bos else "NoBOS", "HarmonicV5" if v5_bos_mode else ""],
            timestamp=now,
            label=f"🎯 {signal_type} {best_dir}{bos_tag} | Layer:{layer} | Score:{best_score} | Session:{current_session}",
        )

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

# ── P0 FIX ──
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
