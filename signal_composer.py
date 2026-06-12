"""
signal_composer.py — Alpha Buffalo v5.3 (Orchestrator)
"""
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional
from zoneinfo import ZoneInfo
from kivanc_vsaob import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine import run_micro, MicroSignal
from session_clock import get_market_session_info, H4SessionTracker
from score_manager_v5p3 import score_manager, ScoreResult, DXYRegime
from ASIA_TUNING_v5p3 import ASITuningManager, ASIAScalpTriggerGate

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


def detect_trend(df_4h):
    """Detect trend from simple price action"""
    if len(df_4h) < 10:
        return "NEUTRAL"
    
    closes = df_4h['close'].values
    highs = df_4h['high'].values
    lows = df_4h['low'].values
    
    # Check Higher Highs / Higher Lows
    mid = len(closes) // 2
    first_half_high = highs[:mid].max()
    second_half_high = highs[mid:].max()
    first_half_low = lows[:mid].min()
    second_half_low = lows[mid:].min()
    
    if second_half_high > first_half_high and second_half_low > first_half_low:
        return "UP"
    elif second_half_high < first_half_high and second_half_low < first_half_low:
        return "DOWN"
    else:
        # Check last 4 candles vs first 4
        recent_high = highs[-4:].max()
        early_high = highs[:4].max()
        recent_low = lows[-4:].min()
        early_low = lows[:4].min()
        
        if recent_high > early_high and recent_low > early_low:
            return "UP"
        elif recent_high < early_high and recent_low < early_low:
            return "DOWN"
    
    return "NEUTRAL"


class SignalComposer:
    def __init__(self):
        self.buy_basket = BasketState(direction="BUY")
        self.sell_basket = BasketState(direction="SELL")
        self.last_signal = None
        self.asia_manager = ASITuningManager()

    def compose(self, df_4h, df_1h, df_15m):
        session_info = get_market_session_info()
        current_session = session_info["session"]
        current_price = float(df_15m["close"].iloc[-1])

        kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)
        micro_sigs = run_micro(df_15m)

        # ScoreManager inputs (simplified, use defaults for now)
        score_inputs = {
            "cascade_direction": detect_trend(df_4h) if not any(getattr(s, 'bullish', False) or getattr(s, 'bearish', False) for s in micro_sigs) else ("UP" if any(getattr(s, 'bullish', False) for s in micro_sigs) else "DOWN"),
            "cascade_h4_only": True,
            "reversal_stage": 0,
            "harmonic_in_prz": any(
                prz.is_active(current_price) for prz in prz_zones
            ) if prz_zones else False,
            "harmonic_priority": "primary" if any(
                prz.is_active(current_price) and prz.priority == "primary" 
                for prz in prz_zones
            ) else "secondary" if prz_zones else "secondary",
            "kivanc_in_golden": (
                kivanc_sig and 
                getattr(kivanc_sig, 'in_golden_zone', False) and
                getattr(kivanc_sig, 'fib_level', 0) >= 0.76
            ),
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
            "vsa_ok": (
                # Kivanc VSA signal
                (kivanc_sig and getattr(kivanc_sig, 'vsa_wall', False)) or
                # หรือ Volume spike ที่ PRZ
                (prz_zones and any(prz.is_active(current_price) for prz in prz_zones) and 
                 df_15m['volume'].iloc[-1] > df_15m['volume'].rolling(20).mean().iloc[-1] * 1.5)
            ),
            "news_block": False,
            "fg_score": 0,
            "dxy_score": 0,
            "dxy_regime": DXYRegime.NEUTRAL,
            "cot_score": 0,
        }
        score_result: ScoreResult = score_manager.calculate(**score_inputs)

        if not score_result.is_tradable:
            return None

        # best_dir: ใช้ cascade_direction เป็นหลัก แต่ให้โอกาส Buy ถ้ามี Sweep+BOS bullish
        cascade_dir = score_inputs["cascade_direction"]
        has_bullish_reversal = score_inputs["sweep_valid"] and score_inputs["bos_detected"] and any(getattr(s, 'bullish', False) for s in micro_sigs)
        
        if cascade_dir == "UP":
            best_dir = "BUY"
        elif cascade_dir == "DOWN" and has_bullish_reversal:
            best_dir = "BUY"  # Reversal trade
        else:
            best_dir = "SELL"
        
        if kivanc_sig:
            best_dir = kivanc_sig.direction

        # ASIA gate (if applicable)
        if current_session == "ASIA":
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
                current_time=pd.Timestamp.now(tz="UTC"),
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
        now = pd.Timestamp.now(tz=BKK).strftime("%H:%M:%S")
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
