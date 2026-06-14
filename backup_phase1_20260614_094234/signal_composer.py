"""
signal_composer.py — Alpha Buffalo v5.3 (Orchestrator)
"""
import logging
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
from trade_manager import trade_manager as buy_engine
from trade_manager import trade_manager_sell as sell_engine

logger = logging.getLogger(__name__)

# 3-STEP TRADING WORKFLOW (v5.4)
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



def get_action_emoji(action: str) -> str:
    """คืนค่า Emoji ตาม Action"""
    if action in ['TP', 'TRAILING']:
        return "🟢"
    if action == 'SL':
        return "🔴"
    if action == 'PARTIAL':
        return "🟡"
    if action == 'BREAKEVEN':
        return "🛡️"
    return "📊"

def process_trade_actions(engine, actions):
    """ประมวลผล Actions จาก Engine → ส่ง Telegram"""
    if not actions:
        return
    
    for action in actions:
        emoji = get_action_emoji(action.get('action', ''))
        trade_type = action.get('type', '')
        act = action.get('action', '')
        price = action.get('price', action.get('sl', 0))
        
        msg = f"{emoji} {trade_type} {act} @ {price:.2f}"
        
        if trade_type == 'V4' and act == 'TP':
            msg += f"\n🛡️ V5 Breakeven Triggered!"
        elif trade_type == 'V5' and act == 'TRAILING':
            msg += f"\n📈 SL moved to {action.get('sl', 0):.2f}"
        elif trade_type == 'V5' and act == 'BREAKEVEN':
            msg += f"\n✅ Risk-Free Trade!"
        
        # ส่ง Telegram (ถ้ามี broadcaster)
        try:
            from telegram_broadcaster import broadcast
            broadcast(msg)
        except ImportError:
            pass  # ไม่มี Telegram ก็ไม่เป็นไร
        
        logger.info(f"📱 Telegram: {msg}")

def check_engine_conflict(best_dir):
    """ป้องกัน Buy/Sell Engine ทำงานพร้อมกัน"""
    from trade_manager import trade_manager as buy_engine, trade_manager_sell as sell_engine
    
    if best_dir == "BUY" and sell_engine.state.phase == "PHASE3":
        logger.warning("⚠️ Sell engine active — skipping Buy signal")
        return False
    if best_dir == "SELL" and buy_engine.state.phase == "PHASE3":
        logger.warning("⚠️ Buy engine active — skipping Sell signal")
        return False
    return True



def calc_rsi(close, period=14):
    """คำนวณ RSI"""
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.rolling(period).mean()
    avg_loss = loss.rolling(period).mean()
    rs = avg_gain / avg_loss
    return float((100 - (100 / (1 + rs))).iloc[-1])


def is_smart_bb_entry(df_15m, current_price, bb_lower, bb_upper, bb_middle=None):
    """
    Smart BB Entry — Multi-Layer Confirmation
    Returns: (is_valid, direction)
    """
    # Layer 1: Volatility Squeeze Check
    bb_width = (bb_upper - bb_lower) / bb_lower
    is_squeeze = bb_width < 0.015  # BB แคบกว่า 1.5% = ห้ามเข้า
    
    # Layer 2: RSI Check
    rsi = calc_rsi(df_15m['close'], 14)
    is_oversold = rsi < 35
    is_overbought = rsi > 65
    
    # Layer 3: Bullish/Bearish Rejection
    last = df_15m.iloc[-1]
    candle_range = last['high'] - last['low']
    if candle_range > 0:
        lower_wick = (min(last['close'], last['open']) - last['low']) / candle_range
        upper_wick = (last['high'] - max(last['close'], last['open'])) / candle_range
        has_bullish_rej = lower_wick > 0.4 and last['close'] > bb_lower
        has_bearish_rej = upper_wick > 0.4 and last['close'] < bb_upper
    else:
        has_bullish_rej = has_bearish_rej = False
    
    # Decision
    if is_squeeze:
        return False, "SQUEEZE"
    
    near_lower = current_price <= bb_lower * 1.01
    near_upper = current_price >= bb_upper * 0.99
    
    if near_lower and is_oversold and has_bullish_rej:
        return True, "BUY"
    if near_upper and is_overbought and has_bearish_rej:
        return True, "SELL"
    
    return False, "WEAK"


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
        micro_sigs = run_micro(df_15m)
        prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)

        # ScoreManager inputs (simplified, use defaults for now)
        score_inputs = {
            # near_lower_bb removed
            "near_lower_bb": smart_bb_valid if 'smart_bb_valid' in dir() else False,
            "cascade_direction": ('bb_lower' in dir() and current_price <= bb_lower * 1.01 and calc_rsi(df_15m['close'], 14) < 35),
            "near_lower_bb": smart_bb_valid if 'smart_bb_valid' in dir() else False,
            "cascade_direction": "UP" if any(getattr(s, 'bullish', False) for s in micro_sigs) else ("DOWN" if any(getattr(s, 'bearish', False) for s in micro_sigs) else "SIDEWAYS"),
            "cascade_h4_only": True,
            "reversal_stage": 0,
            "harmonic_in_prz": any(prz.is_active(current_price) for prz in prz_zones) if prz_zones else False,
            "harmonic_priority": "primary" if any(getattr(p, 'priority', 'secondary') == 'primary' for p in prz_zones) else "secondary",
            "kivanc_in_golden": kivanc_sig and getattr(kivanc_sig, 'in_golden_zone', False),
            "kivanc_score": kivanc_sig.confluence_score if kivanc_sig else 0,
            "fvg_verdict": "NONE",
            "bos_detected": any(getattr(s, 'bos', False) for s in micro_sigs),
            "mss_detected": any(getattr(s, 'mss', False) for s in micro_sigs),
            "sweep_valid": any(getattr(s, 'sweep_valid', False) for s in micro_sigs),
            "sweep_is_pdh_pdl": any(getattr(s, 'is_pdh_pdl', False) for s in micro_sigs),
            "h1_spike": False,
            "h1_spike_volume": False,
            "h1_spike_at_h4_boundary": False,
            "at_bonus": 0,
            "vsa_ok": kivanc_sig and getattr(kivanc_sig, 'vsa_wall', False),
            "news_block": False,
            "fg_score": 0,
            "dxy_score": 0,
            "dxy_regime": 0,
            "cot_score": 0,
        }
        score_result: ScoreResult = score_manager.calculate(**score_inputs)

        # PRZ ไม่ตัดคะแนน แต่ใช้เป็น Bonus Confidence
        in_prz = False  # PRZ = TP guide, not entry condition
        
        if in_prz:
            confidence = "⭐⭐⭐ HIGH (ใน PRZ)"
            # เพิ่ม lot size ได้ถ้าต้องการ
        else:
            confidence = "⭐ NORMAL (ไม่มี PRZ — เทรดตาม Structure)"
        
        logger.info(f"🎯 Confidence: {confidence}")
        
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


        # PRZ บอกว่า "อยู่ไหน → จะไปไหน"
        # ไม่ใช่ตัวตัดสินเข้าเทรด แต่เป็น Bonus Confidence
        in_prz_zone = any(prz.is_active(current_price) for prz in prz_zones) if prz_zones else False
        
        # ถ้า BOS แล้ว → PRZ ใหม่ = TP1/TP2
        new_prz_after_bos = None
        if score_inputs["bos_detected"]:
            if best_dir == "BUY" and buy_engine.state.price_BOS:
                new_prz_after_bos, _ = buy_engine.recalculate_harmonic_prz(current_price)
            elif best_dir == "SELL" and sell_engine.state.price_BOS:
                new_prz_after_bos, _ = sell_engine.recalculate_harmonic_prz(current_price)
        
        # Confidence Level
        if in_prz_zone and score_inputs["bos_detected"]:
            confidence = "⭐⭐⭐ HIGH (PRZ + BOS)"
        elif score_inputs["bos_detected"]:
            confidence = "⭐⭐ MEDIUM (BOS, No PRZ)"
        elif in_prz_zone:
            confidence = "⭐ LOW (PRZ, No BOS)"
        else:
            confidence = "⚡ SCALP ONLY"
        
        logger.info(f"📍 GPS: PRZ={'Yes' if in_prz_zone else 'No'}, BOS={score_inputs['bos_detected']}, Confidence={confidence}")
        # sw_mode = True → Sideways/Pullback → V4 Only (Sniper)
        # sw_mode = False → Trending → V4 + V5 (Sniper + Runner)
        
        market_mode = "SIDEWAYS" if sw_mode else "TRENDING"
        
        if market_mode == "TRENDING":
            # Trending: ใช้ทั้ง V4 + V5
            use_v4 = True
            use_v5 = True
            logger.info(f"📈 {market_mode}: V4 (Scalp) + V5 (Runner)")
        else:
            # Sideways: ใช้ V4 เท่านั้น (กัน Whipsaw กิน V5)
            use_v4 = True
            use_v5 = False
            logger.info(f"📊 {market_mode}: V4 Only (Sniper Mode)")
        
        if not check_engine_conflict(best_dir):
            return None
        
        kivanc_ok = (score_inputs["kivanc_in_golden"] and score_inputs["kivanc_score"] >= 3)
        
        if best_dir == "BUY":
            trade_actions = buy_engine.update(
                df_15m,
                vsa_ok=score_inputs["vsa_ok"],
                kivanc_ok=kivanc_ok
            )
            engine = buy_engine
        else:
            trade_actions = sell_engine.update(
                df_15m,
                vsa_ok=score_inputs["vsa_ok"],
                kivanc_ok=kivanc_ok
            )
            engine = sell_engine
        
        # ถ้า Sideways → ไม่เปิด V5
        if not use_v5 and engine.state.active_orders:
            for o in engine.state.active_orders:
                if o["type"] == "V5_RUNNER":
                    o["status"] = "SKIPPED"
                    logger.info("⏭️ V5 Skipped (Sideways Mode)")
        
        # ประมวลผล Actions → Telegram
        if trade_actions:
            process_trade_actions(engine, trade_actions)
        
        if engine.state.phase == "IDLE" and not trade_actions:
            return None
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
