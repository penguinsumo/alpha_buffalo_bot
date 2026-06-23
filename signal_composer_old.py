"""
signal_composer.py — Alpha Buffalo New V4 + Session-Hour Gate + Adaptive Hourly Gate + Threshold per Session
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List
from datetime import datetime
from zoneinfo import ZoneInfo

from kivanc_vsaob      import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine      import run_micro, MicroSignal
from session_clock     import H4SessionTracker, get_current_session
from score_manager_v5p3 import ScoreManager, THRESHOLD_V4, THRESHOLD_V5
from scenario_scanner  import ScenarioBlueprint
from trade_manager     import hourly_stats

BKK = ZoneInfo("Asia/Bangkok")

COMPOSER_CONFIG = {
    "basket_layer_1":   0.618,
    "basket_layer_2":   0.786,
    "basket_kill":      0.886,
    "lot_layer_1":      1.0,
    "lot_layer_2":      1.5,
    "bb_period":        20,
    "bb_std":           2.0,
}

# ── Permission Table ──────────────────────────────
SESSION_HOURS = {
    'ASIA':   {'BUY': [1],           'SELL': [3, 5]},
    'LONDON': {'BUY': [],            'SELL': [8, 9, 12]},
    'NY':     {'BUY': [13, 15, 16, 17], 'SELL': [13, 14, 15, 16, 17, 18]}
}

MIN_HOURLY_WR = {'ASIA': 0.30, 'LONDON': 0.35, 'NY': 0.40}
SESSION_V4_THRESHOLD = {'ASIA': 3, 'LONDON': 4, 'NY': 4}

def get_session(hour: int) -> str:
    if 1 <= hour < 8: return 'ASIA'
    elif 8 <= hour < 13: return 'LONDON'
    elif 13 <= hour < 19: return 'NY'
    return 'CLOSED'

@dataclass
class BasketState:
    direction: str; layer: int = 0
    entry_1: float = 0.0; entry_2: float = 0.0
    lot_1: float = 0.0; lot_2: float = 0.0
    sl: float = 0.0; tp1: float = 0.0; tp2: float = 0.0
    active: bool = False; killed: bool = False
    v5_alive: bool = False; v4_alive: bool = False
    v4_partial_closed: bool = False

@dataclass
class ComposedSignal:
    direction: str; signal_type: str
    entry_price: float; sl_price: float
    tp1_price: float; tp2_price: float
    lot_multiplier: float; basket_layer: int
    confluence_score: int
    sources: list = field(default_factory=list)
    label: str = ""; timestamp: str = ""

def calc_bb(df: pd.DataFrame, period=20, std=2.0):
    close = df["close"]
    mid = close.rolling(period).mean().iloc[-1]
    s = close.rolling(period).std().iloc[-1]
    return {"upper": mid + std * s, "mid": mid, "lower": mid - std * s}

def calc_exits(direction, entry, prz_zones, bb, kivanc_sig, blueprint=None):
    if direction == "BUY":
        sl = entry - 2.0; tp1 = entry + 3.0; tp2 = bb["upper"]
        if kivanc_sig and kivanc_sig.direction == "BUY":
            sl = kivanc_sig.sl_price; tp1 = kivanc_sig.tp1_price
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
            if blueprint.plan_b_tp2 > entry: tp2 = blueprint.plan_b_tp2
        elif blueprint:
            if blueprint.golden_zone_low > sl: sl = blueprint.golden_zone_low * 0.999
            if blueprint.golden_zone_high > entry and blueprint.golden_zone_high < tp1:
                tp1 = blueprint.golden_zone_high
    else:
        sl = entry + 2.0; tp1 = entry - 3.0; tp2 = bb["lower"]
        if kivanc_sig and kivanc_sig.direction == "SELL":
            sl = kivanc_sig.sl_price; tp1 = kivanc_sig.tp1_price
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
            if blueprint.plan_b_tp2 < entry: tp2 = blueprint.plan_b_tp2
        elif blueprint:
            if blueprint.golden_zone_high < sl: sl = blueprint.golden_zone_high * 1.001
            if blueprint.golden_zone_low < entry and blueprint.golden_zone_low > tp1:
                tp1 = blueprint.golden_zone_low
    return sl, tp1, tp2

class SignalComposer:
    def __init__(self):
        self.buy_basket  = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal: Optional[ComposedSignal] = None
        self.score_mgr = ScoreManager()

    def compose(self, df_4h, df_1h, df_15m, blueprint=None):
        current_price = float(df_15m["close"].iloc[-1])

        kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)
        micro_sigs = run_micro(df_15m)
        micro_bos = any(s.bos for s in micro_sigs) if micro_sigs else False

        kivanc_score = 1 if kivanc_sig else 0
        vsa_ok = False
        score_result = self.score_mgr.calculate(
            kivanc_score=kivanc_score, bos_detected=micro_bos, vsa_ok=vsa_ok
        )
        base_score = score_result.total

        if kivanc_sig and kivanc_sig.direction in ("BUY", "SELL"):
            best_dir = kivanc_sig.direction
        elif blueprint:
            best_dir = "BUY" if blueprint.trend_h4 == "UP" else "SELL"
        else:
            return None

        utc_hour = pd.Timestamp.now(tz='UTC').hour
        session = get_session(utc_hour)
        if session == 'CLOSED':
            return None

        # ── Gate 1: Session-Hour Permission Table ──
        allowed_hours = SESSION_HOURS.get(session, {}).get(best_dir, [])
        if utc_hour not in allowed_hours:
            return None

        # ── Gate 2: Adaptive Hourly Gate ──
        min_wr = MIN_HOURLY_WR.get(session, 0.35)
        wr = hourly_stats.wr(utc_hour, min_samples=10)
        if wr < min_wr:
            return None

        # ── Threshold per Session ──
        thresh = SESSION_V4_THRESHOLD.get(session, THRESHOLD_V4)
        if not score_result.is_tradable and base_score < thresh:
            return None

        basket = self.buy_basket if best_dir == "BUY" else self.sell_basket

        score_boost = 0
        if blueprint and blueprint.tunnel_status == "CONFIRMED":
            if best_dir == "BUY" and current_price <= blueprint.tunnel_lower * 1.02:
                score_boost += 2
            elif best_dir == "SELL" and current_price >= blueprint.tunnel_upper * 0.98:
                score_boost += 2
        best_score = base_score + score_boost
        if best_score < thresh:
            return None

        signal_type = "V5_SNIPER" if best_score >= THRESHOLD_V5 else "V4_SCALP"

        if basket.killed:
            return None
        layer = basket.layer + 1
        lot_multi = COMPOSER_CONFIG[f"lot_layer_{min(layer, 2)}"]
        if layer > 2:
            return None

        bb = calc_bb(df_15m, COMPOSER_CONFIG["bb_period"], COMPOSER_CONFIG["bb_std"])
        sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig, blueprint)

        if signal_type == "V5_SNIPER":
            basket.v5_alive = True
        elif signal_type == "V4_SCALP":
            if not basket.v4_alive:
                basket.v4_alive = True
                basket.v4_partial_closed = False
        basket.layer = layer
        basket.active = True
        basket.sl, basket.tp1, basket.tp2 = sl, tp1, tp2

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
            sources=[f"Base({base_score})+Boost({score_boost})", "Kivanc" if kivanc_sig else "Trend"],
            timestamp=now,
            label=f"🎯 {signal_type} {best_dir} | Layer:{layer} | Score:{best_score}",
        )
        self.last_signal = sig
        return sig

    def kill_basket(self, direction: str):
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.killed = True; basket.active = False; basket.layer = 0

    def reset_basket(self, direction: str):
        basket = self.buy_basket if direction == "BUY" else self.sell_basket
        basket.__init__(direction=direction)

composer = SignalComposer()

def compose_signal(df_4h, df_1h, df_15m, blueprint=None):
    return composer.compose(df_4h, df_1h, df_15m, blueprint)

def get_fill_price(signal_bar, next_bar=None):
    if next_bar is not None: return next_bar['open']
    return signal_bar['close']
