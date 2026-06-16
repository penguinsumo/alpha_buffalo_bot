
"""
scenario_scanner.py — Alpha Buffalo v5.4
Pre-Market Scenario Blueprint Generator
รันหลังตลาดปิด → วาดภาพใหญ่ → ส่ง Blueprint ให้ Trade Manager
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# SCENARIO BLUEPRINT
# ═══════════════════════════════════════════════════════

@dataclass
class ScenarioBlueprint:
    """แผนการเทรดที่คำนวณล่วงหน้า"""
    timestamp: str = ""
    symbol: str = "XAUUSD"
    
    # Market Context
    session: str = ""           # Asia / London / NY
    trend_h4: str = "NEUTRAL"   # UP / DOWN / NEUTRAL
    trend_h1: str = "NEUTRAL"
    market_mode: str = "SIDEWAYS"  # TRENDING / SIDEWAYS / PULLBACK
    
    # Price Levels
    current_price: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    
    # Swing Points (L-H-HL Structure)
    swing_L: Optional[float] = None
    swing_H: Optional[float] = None
    swing_HL: Optional[float] = None
    bos_triggered: bool = False
    
    # Plan A: Sideways Scalp (V4)
    plan_a_entry: float = 0.0
    plan_a_tp: float = 0.0      # BB Opposite
    plan_a_sl: float = 0.0
    
    # Plan B: BOS Breakout (V5)
    plan_b_entry: float = 0.0
    plan_b_tp1: float = 0.0     # PRZ ใหม่ 1.272
    plan_b_tp2: float = 0.0     # PRZ ใหม่ 1.618
    plan_b_sl: float = 0.0
    
    # Harmonic PRZ (GPS Guideline)
    harmonic_pattern: str = ""
    prz_current: Optional[float] = None
    prz_next: Optional[float] = None
    
    # Risk Management
    atr_15m: float = 0.0
    atr_1h: float = 0.0
    max_risk_pct: float = 1.0
    # Risk & Performance Metrics
    win_rate_est: float = 0.0       # Estimated Win Rate (%)
    risk_reward_ratio: float = 0.0  # RR Ratio (TP:SL)
    expected_value: float = 0.0     # EV = (Win% * TP) - (Loss% * SL)
    confidence: str = "LOW"         # LOW / MEDIUM / HIGH
    historical_win_rate: float = 0.61  # จาก Backtest (61.3%)

    
    # Session Timestamps
    asia_open: str = "01:00 UTC"
    london_open: str = "08:00 UTC"
    ny_open: str = "13:00 UTC"
    
    # Validation
    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """แปลงเป็น dict สำหรับ API"""
        return {
            "timestamp": self.timestamp,
            "session": self.session,
            "trend_h4": self.trend_h4,
            "market_mode": self.market_mode,
            "current_price": self.current_price,
            "plan_a": {
                "entry": self.plan_a_entry,
                "tp": self.plan_a_tp,
                "sl": self.plan_a_sl,
                "description": "Sideways Scalp (BB to BB)"
            },
            "plan_b": {
                "entry": self.plan_b_entry,
                "tp1": self.plan_b_tp1,
                "tp2": self.plan_b_tp2,
                "sl": self.plan_b_sl,
                "description": "BOS Breakout → New PRZ"
            },
            "harmonic": {
                "pattern": self.harmonic_pattern,
                "prz_current": self.prz_current,
                "prz_next": self.prz_next
            },
            "swing_points": {
                "L": self.swing_L,
                "H": self.swing_H,
                "HL": self.swing_HL,
                "BOS": self.bos_triggered
            },
            "is_valid": self.is_valid,
            "validation_errors": self.validation_errors
        }


# ═══════════════════════════════════════════════════════
# SCENARIO SCANNER ENGINE
# ═══════════════════════════════════════════════════════

class ScenarioScanner:
    """
    Pre-Market Scenario Generator
    
    Flow:
    1. ดึงข้อมูล OHLCV
    2. คำนวณ Market Context (Trend, BB, Session)
    3. หา Swing Points (L-H-HL)
    4. คำนวณ Plan A (Sideways Scalp)
    5. คำนวณ Plan B (BOS Breakout → PRZ ใหม่)
    6. Validate → ส่ง Blueprint
    """
    
    def __init__(self):
        self.blueprint = ScenarioBlueprint()
    
    def scan(self, df_4h, df_1h, df_15m) -> ScenarioBlueprint:
        """สแกนตลาด — สร้าง Blueprint"""
        
        bp = ScenarioBlueprint()
        bp.timestamp = datetime.now(timezone.utc).isoformat()
        
        # ── 1. Market Context ──
        bp.current_price = float(df_15m['close'].iloc[-1])
        bp.atr_15m = self._calc_atr(df_15m, 14)
        bp.atr_1h = self._calc_atr(df_1h, 14)
        
        # BB
        ma20 = df_15m['close'].rolling(20).mean().iloc[-1]
        std20 = df_15m['close'].rolling(20).std().iloc[-1]
        bp.bb_upper = float(ma20 + std20 * 2)
        bp.bb_middle = float(ma20)
        bp.bb_lower = float(ma20 - std20 * 2)
        
        # Trend
        ema20_h4 = df_4h['close'].ewm(span=20).mean().iloc[-1]
        ema50_h4 = df_4h['close'].ewm(span=50).mean().iloc[-1]
        ema20_h1 = df_1h['close'].ewm(span=20).mean().iloc[-1]
        ema50_h1 = df_1h['close'].ewm(span=50).mean().iloc[-1]
        
        bp.trend_h4 = "UP" if ema20_h4 > ema50_h4 else "DOWN"
        bp.trend_h1 = "UP" if ema20_h1 > ema50_h1 else "DOWN"
        
        if bp.trend_h4 == bp.trend_h1:
            bp.market_mode = "TRENDING"
        elif bp.trend_h4 != bp.trend_h1:
            bp.market_mode = "PULLBACK"
        else:
            bp.market_mode = "SIDEWAYS"
        
        # Session
        bp.session = self._get_session()
        
        # ── 2. Swing Points ──
        bp.swing_L, bp.swing_H, bp.swing_HL = self._find_swing_points(df_15m)
        bp.bos_triggered = bp.current_price > bp.swing_H if bp.swing_H else False
        
        # ── 3. Harmonic PRZ (GPS) ──
        if bp.swing_L and bp.swing_H and bp.swing_HL:
            from harmonic_detector import recalculate_prz_after_bos
            prz_new, pattern = recalculate_prz_after_bos(
                bp.swing_L, bp.swing_H, bp.swing_HL, 
                bp.current_price, 
                "BUY" if bp.trend_h4 == "UP" else "SELL"
            )
            if prz_new:
                bp.prz_next = prz_new.prz_mid
                bp.harmonic_pattern = pattern
        
        # ── 4. Plan A: Sideways Scalp (V4) ──
        # Entry Zone = Support Zone (แนวรับที่แข็งแรง)
        # Support = Golden Kivanc + Harmonic PRZ + BB Lower
        support_zone = bp.bb_lower
        if bp.swing_L and bp.swing_HL:
            support_zone = max(bp.bb_lower, bp.swing_L)  # แนวรับที่สูงกว่า = แข็งแรงกว่า
        
        if bp.current_price <= bp.bb_lower * 1.02:  # ใกล้ Lower BB (2% buffer)
            bp.plan_a_entry = support_zone  # เข้าที่แนวรับ ไม่ใช่ราคาปัจจุบัน
            bp.plan_a_tp = bp.bb_upper
            bp.plan_a_sl = (bp.swing_L or support_zone) * 0.998
        elif bp.current_price >= bp.bb_upper * 0.98:  # ใกล้ Upper BB
            resistance_zone = min(bp.bb_upper, bp.swing_H) if bp.swing_H else bp.bb_upper
            bp.plan_a_entry = resistance_zone  # เข้าที่แนวต้าน
            bp.plan_a_tp = bp.bb_lower
            bp.plan_a_sl = (bp.swing_H or resistance_zone) * 1.002
        
        # ── 5. Plan B: BOS Breakout (V5) ──
        if bp.swing_H and bp.current_price > bp.swing_H:
            # BOS แล้ว → คำนวณ TP ตาม PRZ ใหม่
            bp.plan_b_entry = bp.current_price
            bp.plan_b_sl = bp.bb_middle  # Trailing ตาม Middle BB
            
            if bp.prz_next:
                range_to_prz = abs(bp.prz_next - bp.current_price)
                bp.plan_b_tp1 = bp.current_price + range_to_prz * 0.618
                bp.plan_b_tp2 = bp.prz_next
            else:
                bp.plan_b_tp1 = bp.current_price + bp.atr_1h * 2
                bp.plan_b_tp2 = bp.current_price + bp.atr_1h * 3
        
        # ── 6. Validate ──
        bp.is_valid, bp.validation_errors = self._validate(bp)
        
        self.blueprint = bp
                # ── Calculate Win Rate & RR ──
        # Plan A RR
        if bp.plan_a_tp > 0 and bp.plan_a_sl > 0 and bp.plan_a_entry > 0:
            reward_a = abs(bp.plan_a_tp - bp.plan_a_entry)
            risk_a = abs(bp.plan_a_entry - bp.plan_a_sl)
            if risk_a > 0:
                bp.risk_reward_ratio = reward_a / risk_a
        
        # Plan B RR
        if bp.plan_b_tp2 > 0 and bp.plan_b_sl > 0 and bp.plan_b_entry > 0:
            reward_b = abs(bp.plan_b_tp2 - bp.plan_b_entry)
            risk_b = abs(bp.plan_b_entry - bp.plan_b_sl)
            if risk_b > 0:
                bp.risk_reward_ratio = max(bp.risk_reward_ratio, reward_b / risk_b)
        
        # Estimate Win Rate จาก Market Mode
        win_rate_map = {
            "TRENDING": 0.65,
            "PULLBACK": 0.55,
            "SIDEWAYS": 0.45,
        }
        bp.win_rate_est = win_rate_map.get(bp.market_mode, 0.50)
        
        # Expected Value
        if bp.risk_reward_ratio > 0:
            bp.expected_value = (bp.win_rate_est * bp.risk_reward_ratio) - ((1 - bp.win_rate_est) * 1)
        
        # Confidence
        if bp.risk_reward_ratio >= 2.0 and bp.win_rate_est >= 0.60:
            bp.confidence = "HIGH"
        elif bp.risk_reward_ratio >= 1.5 or bp.win_rate_est >= 0.55:
            bp.confidence = "MEDIUM"
        else:
            bp.confidence = "LOW"
        
        return bp
    
    def _find_swing_points(self, df_15m) -> tuple:
        """หา Swing Points (L, H, HL)"""
        lookback = min(50, len(df_15m) // 2)
        lows = df_15m['low'].iloc[-lookback:]
        highs = df_15m['high'].iloc[-lookback:]
        
        L = float(lows.min())
        L_idx = lows.idxmin()
        
        # H = สูงสุดหลัง L
        highs_after_L = df_15m['high'].loc[L_idx:]
        H = float(highs_after_L.max()) if len(highs_after_L) > 0 else None
        
        # HL = ต่ำสุดหลัง H แต่ > L
        if H:
            H_idx = highs_after_L.idxmax()
            lows_after_H = df_15m['low'].loc[H_idx:]
            HL_candidates = lows_after_H[lows_after_H > L]
            HL = float(HL_candidates.min()) if len(HL_candidates) > 0 else None
        else:
            HL = None
        
        return L, H, HL
    
    def _calc_atr(self, df, period=14) -> float:
        """คำนวณ ATR"""
        high, low, close = df['high'], df['low'], df['close'].shift(1)
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    
    def _get_session(self) -> str:
        """ระบุ Session ปัจจุบัน"""
        hour_utc = datetime.now(timezone.utc).hour
        if 1 <= hour_utc < 8:
            return "ASIA"
        elif 8 <= hour_utc < 13:
            return "LONDON"
        elif 13 <= hour_utc < 19:
            return "NY"
        else:
            return "ASIA_LOW"
    
    def _validate(self, bp: ScenarioBlueprint) -> tuple:
        """ตรวจสอบความถูกต้องของ Blueprint"""
        errors = []
        
        if not bp.swing_L:
            errors.append("No Swing L found")
        if bp.plan_a_tp <= bp.plan_a_entry and bp.plan_a_entry > 0:
            errors.append("Plan A: TP <= Entry")
        if bp.atr_15m == 0:
            errors.append("ATR is zero")
        
        return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════
scanner = ScenarioScanner()
