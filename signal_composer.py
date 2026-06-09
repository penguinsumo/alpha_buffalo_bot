"""
signal_composer.py — Alpha Buffalo v5.3 (Orchestrator)
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo
from kivanc_vsaob import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine import run_micro, MicroSignal
from session_clock import get_market_session_info, H4SessionTracker
from score_manager_v5p3 import score_manager, ScoreResult, DXYRegime
from ASIA_TUNING_v5p3 import ASIATuningManager, ASIAScalpTriggerGate, ASIASessionVSAGate

BKK = ZoneInfo("Asia/Bangkok")

COMPOSER_CONFIG = {
    "lot_layer_1": 1.0,
    "lot_layer_2": 1.5,
    "bb_period": 20,
    "bb_std": 2.0,
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

def calc_bb(df, period=20, std=2.0):
    close = df["close"]
    mid = close.rolling(period).mean().iloc[-1]
    s = close.rolling(period).std().iloc[-1]
    return {"upper": mid + std * s, "mid": mid, "lower": mid - std * s}

def calc_exits(direction, entry, prz_zones, bb, kivanc_sig):
    # fallback exits (simplified)
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
        self.buy_basket = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal = None
        self.asia_manager = ASIATuningManager()

    def compose(self, df_4h, df_1h, df_15m):
        session_info = get_market_session_info()
        current_session = session_info["session"]
        current_price = float(df_15m["close"].iloc[-1])

        kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)
        micro_sigs = run_micro(df_15m)

        # ScoreManager inputs (simplified, use defaults for now)
        score_inputs = {
            "cascade_direction": "NEUTRAL",
            "cascade_h4_only": True,
            "reversal_stage": 0,
            "harmonic_in_prz": False,
            "harmonic_priority": "secondary",
            "kivanc_in_golden": False,
            "kivanc_score": kivanc_sig.confluence_score if kivanc_sig else 0,
            "fvg_verdict": "NONE",
            "bos_detected": any(getattr(s, 'bos', False) for s in micro_sigs),
            "mss_detected": any(getattr(s, 'mss', False) for s in micro_sigs),
            "sweep_valid": any(getattr(s, 'sweep_valid', False) for s in micro_sigs),
            "sweep_is_pdh_pdl": any(getattr(s, 'is_pdh_pdl', False) for s in micro_sigs),
            "h1_spike": False,
            "h1_spike_volume": False,
            "h1_spike_at_h4_boundary": H4SessionTracker.get_h4_boundary()['is_boundary_approaching'],
            "at_bonus": 0,
            "vsa_ok": False,
            "news_block": False,
            "fg_score": 0,
            "dxy_score": 0,
            "dxy_regime": DXYRegime.NEUTRAL,
            "cot_score": 0,
        }
        score_result: ScoreResult = score_manager.calculate(**score_inputs)

        if not score_result.is_tradable:
            return None

        best_dir = "BUY" if score_inputs["cascade_direction"] == "UP" else "SELL"  # simplified
        if kivanc_sig:
            best_dir = kivanc_sig.direction

        # ASIA gate (if applicable)
        if current_session == "ASIA":
            from datetime import datetime, timezone
            atr = (df_15m['high'] - df_15m['low']).rolling(14).mean().iloc[-1]
            if pd.isna(atr): atr = current_price * 0.008
            asia_result = self.asia_manager.evaluate_asia_entry(
                direction=best_dir,
                sweep_valid=score_inputs["sweep_valid"],
                sweep_is_pdh_pdl=score_inputs["sweep_is_pdh_pdl"],
                bos_detected=score_inputs["bos_detected"],
                vsa_ok=score_inputs["vsa_ok"],
                h1_spike=score_inputs["h1_spike"],
                recent_volume=df_15m['volume'].iloc[-1],
                volume_ma=df_15m['volume'].rolling(20).mean().iloc[-1],
                entry_price=current_price,
                atr_value=atr,
                current_time=datetime.now(timezone.utc),
                session="ASIA"
            )
            if not asia_result['entry_valid']:
                return None
            sl, tp1, tp2 = asia_result['sl'], asia_result['tp'], asia_result['tp'] * 1.5
        else:
            bb = calc_bb(df_15m)
            sl, tp1, tp2 = calc_exits(best_dir, current_price, prz_zones, bb, kivanc_sig)

        layer = 1  # simplified
        lot_multi = 1.0
        now = datetime.now(BKK).strftime("%H:%M:%S")
        sig = ComposedSignal(
            direction=best_dir,
            signal_type=score_result.signal_type,
            entry_price=current_price,
            sl_price=sl,
            tp1_price=tp1,
            tp2_price=tp2,
            lot_multiplier=lot_multi,
            basket_layer=layer,
            confluence_score=score_result.total,
            sources=[f"ScoreManager({score_result.total})"],
            timestamp=now,
            label=f"🎯 {score_result.signal_type} {best_dir} | Score:{score_result.total} | Session:{current_session}",
        )
        self.last_signal = sig
        return sig

composer = SignalComposer()

def compose_signal(df_4h, df_1h, df_15m):
    return composer.compose(df_4h, df_1h, df_15m)

def kill_basket(direction):
    pass
def reset_basket(direction):
    pass
