
import traceback

def debug_stage(name):
    print(f"\n[DEBUG STAGE] {name}")

def debug_log(label, value):
    print(f"[DEBUG] {label}: {value}")

def debug_error(e):
    print("[DEBUG ERROR]")
    print(traceback.format_exc())


def safe_execute(fn, fallback=None):
    try:
        return fn()
    except Exception as e:
        print('[CORE ERROR]', e)
        return fallback


def safe_run(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print('[ENGINE ERROR]', fn.__name__, e)
        return default

"""
signal_composer.py — Alpha Buffalo v11.2 (New V4 Hybrid)
- Structured Decision Output (No‑None)
- Session-Hour Permission Table (Backtest Proven)
- Session Threshold (ASIA=3, LONDON/NY=4)
- Clean architecture: always returns a Decision object
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime
from zoneinfo import ZoneInfo

# Engines (v11.2 original)
from kivanc_vsaob      import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine      import run_micro, MicroSignal

# v5.3 Modules
from session_clock      import H4SessionTracker, get_current_session
from score_manager_v5p3 import ScoreManager, THRESHOLD_V4, THRESHOLD_V5
from ASIA_TUNING_v5p3   import ASITuningManager, ASIAScalpTriggerGate

from scenario_scanner import ScenarioBlueprint

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

# ── Session-Hour Permission Table ──────────────────────
SESSION_HOURS = {
    'ASIA':   {'BUY': [1],           'SELL': [3, 5]},
    'LONDON': {'BUY': [],            'SELL': [8, 9, 12]},
    'NY':     {'BUY': [13, 15, 16, 17], 'SELL': [13, 14, 15, 16, 17, 18]}
}
SESSION_V4_THRESHOLD = {'ASIA': 3, 'LONDON': 4, 'NY': 4}

# ── Safe helper ─────────────────────────────────────
def safe_float(x, default=0.0):
    return default if x is None else float(x)

# ── Decision Object (No‑None) ──────────────────────────
@dataclass
class SignalDecision:
    status: str = "NO_SIGNAL"       # "SIGNAL" | "NO_SIGNAL" | "WEAK_SIGNAL"
    reason: str = "initial"
    score: float = 0.0
    direction: Optional[str] = None
    signal_type: Optional[str] = None
    entry_price: Optional[float] = None
    sl_price: Optional[float] = None
    tp1_price: Optional[float] = None
    tp2_price: Optional[float] = None
    lot_multiplier: Optional[float] = None
    basket_layer: Optional[int] = None
    debug: Dict[str, Any] = field(default_factory=dict)

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

def calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    close = df["close"]
    mid   = close.rolling(period).mean().iloc[-1]
    s     = close.rolling(period).std().iloc[-1]
    return {"upper": mid + std * s, "mid": mid, "lower": mid - std * s}

def calc_exits(direction: str, entry: float, prz_zones: List[PRZZone], bb: dict,
               kivanc_sig: Optional[KivancSignal]):
    if direction == "BUY":
        sl = entry - 2.0
        tp1 = entry + 3.0
        tp2 = bb["upper"]
        if kivanc_sig and kivanc_sig.direction == "BUY":
            sl = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = max(kivanc_sig.tp2_price, bb["upper"])
    else:
        sl = entry + 2.0
        tp1 = entry - 3.0
        tp2 = bb["lower"]
        if kivanc_sig and kivanc_sig.direction == "SELL":
            sl = kivanc_sig.sl_price
            tp1 = kivanc_sig.tp1_price
            tp2 = min(kivanc_sig.tp2_price, bb["lower"])
    return sl, tp1, tp2

class SignalComposer:
    def __init__(self):
        self.buy_basket  = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.score_mgr = ScoreManager()

    def compose(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame,
                blueprint: Optional[ScenarioBlueprint] = None) -> SignalDecision:
        session = get_current_session()
        h4_info = H4SessionTracker.get_h4_boundary()
        current_price = float(df_15m["close"].iloc[-1])

        # 1. Run engines
        kivanc_sig = safe_run(lambda: run_kivanc(df_1h)) or safe_run(lambda: run_kivanc(df_4h))
        prz_zones = safe_run(lambda: run_harmonic(df_4h), []) + safe_run(lambda: run_harmonic(df_1h), [])
        debug_stage('MICRO_ENGINE')
micro_sigs = run_micro(df_15m) or []

        # 2. ScoreManager (using calculate, NOT evaluate)
        kivanc_score = 1 if kivanc_sig else 0
        bos_detected = any(getattr(s, 'bos', False) for s in micro_sigs) if micro_sigs else False
        score_result = safe_run(lambda: self.score_mgr.calculate(
kivanc_score=kivanc_score,
            bos_detected=bos_detected,
            vsa_ok=False
        )
        base_score = float(getattr(score_result, 'total', 0))

        # Direction detection
        if kivanc_sig and kivanc_sig.direction in ("BUY", "SELL"):
            debug_stage('DIRECTION')
best_dir = kivanc_sig.direction
        elif blueprint:
            best_dir = "BUY" if blueprint.trend_h4 == "UP" else "SELL"
        else:
            debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}

        # 3. Session-Hour Permission Gate
        if session == 'CLOSED':
            debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}
        allowed_hours = SESSION_HOURS.get(session, {}).get(best_dir, [])
        utc_hour = pd.Timestamp.now(tz='UTC').hour
        if utc_hour not in allowed_hours:
            debug_stage('DECISION_BUILD')
return SignalDecision(status="NO_SIGNAL", reason="hour_not_permitted",
                                  score=base_score, direction=best_dir,
                                  debug={"session": session, "hour": utc_hour, "allowed": allowed_hours})

        # 4. Session Threshold
        thresh = SESSION_V4_THRESHOLD.get(session, THRESHOLD_V4)
        if base_score < thresh:
            debug_stage('DECISION_BUILD')
return SignalDecision(status="NO_SIGNAL", reason="below_threshold",
                                  score=base_score, direction=best_dir,
                                  debug={"threshold": thresh, "score": base_score})

        # 5. ASIA Tuning Gates
        if session == "ASIA":
            if not ASIAScalpTriggerGate.verify_sweep(micro_sigs):
                debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}
            if not ASITuningManager.is_within_safe_time(h4_info["current_hour_utc"]):
                debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}

        # 6. Basket Layer
        basket = self.buy_basket if best_dir == "BUY" else self.sell_basket
        if basket.killed:
            debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}
        layer = basket.layer + 1
        lot_multi = COMPOSER_CONFIG[f"lot_layer_{min(layer, 2)}"]
        if layer > 2:
            debug_stage('DECISION_BUILD')
return {'direction': 'HOLD', 'confidence': 0.0, 'source': 'composer'}

        # 7. Exits
        if session == "ASIA":
            sl, tp1, tp2 = ASITuningManager.calculate_dynamic_exits(best_dir, current_price, df_15m)
        else:
            bb = calc_bb(df_15m, COMPOSER_CONFIG["bb_period"], COMPOSER_CONFIG["bb_std"])
            sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig)

        # 8. Build SIGNAL decision
        now = datetime.now(BKK).strftime("%H:%M:%S")
        sig_type = "V5_SNIPER" if base_score >= THRESHOLD_V5 else "V4_SCALP"

        decision = SignalDecision(
            status="SIGNAL",
            reason="approved",
            score=base_score,
            direction=best_dir,
            signal_type=sig_type,
            entry_price=current_price,
            sl_price=safe_float(sl),
            tp1_price=safe_float(tp1),
            tp2_price=safe_float(tp2),
            lot_multiplier=lot_multi,
            basket_layer=layer,
            debug={"session": session, "hour": utc_hour, "threshold": thresh}
        )

        basket.layer = layer
        basket.active = True
        basket.sl, basket.tp1, basket.tp2 = sl, tp1, tp2
        return decision

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

def compose_signal(df_4h, df_1h, df_15m, blueprint=None) -> SignalDecision:

try:
    return composer.compose(df_4h, df_1h, df_15m, blueprint)
except Exception as e:
    print('[FATAL SIGNAL ERROR]', e)
    return {'status':'NO_SIGNAL','score':0,'reason':'fatal_error'}


def kill_basket(direction: str):
    composer.kill_basket(direction)

def reset_basket(direction: str):
    composer.reset_basket(direction)

def get_fill_price(signal_bar, next_bar=None):
    if next_bar is not None:
        return next_bar['open']
    return signal_bar['close']
