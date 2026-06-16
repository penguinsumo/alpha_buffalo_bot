"""
scenario_scanner.py - Alpha Buffalo v5.4
Pre-Market Scenario Blueprint Generator
Runs after market close -> Draws big picture -> Sends Blueprint to Trade Manager

Updates:
- Added Tunnel (Parallel Channel) from Swing Points
- Enhanced Swing Detection to Pivot (left=5, right=5)
- Added Golden Auto Fibo Zone from higher timeframe structure
- Adjusted Plan A/B to incorporate Tunnel + BB + Golden Zone
- Clean code, preserve original structure
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
# SCENARIO BLUEPRINT
# ═══════════════════════════════════════════════════════

@dataclass
class ScenarioBlueprint:
    """Pre-computed trading plan"""
    timestamp: str = ""
    symbol: str = "XAUUSD"
    
    # Market Context
    session: str = ""
    trend_h4: str = "NEUTRAL"
    trend_h1: str = "NEUTRAL"
    market_mode: str = "SIDEWAYS"  # TRENDING / SIDEWAYS / PULLBACK
    
    # Price Levels
    current_price: float = 0.0
    bb_upper: float = 0.0
    bb_middle: float = 0.0
    bb_lower: float = 0.0
    
    # Tunnel (Parallel Channel)
    tunnel_upper: float = 0.0
    tunnel_lower: float = 0.0
    tunnel_mid: float = 0.0
    tunnel_slope: float = 0.0
    tunnel_valid: bool = False
    
    # Golden Auto Fibo Zone (from higher timeframe)
    golden_zone_low: float = 0.0
    golden_zone_high: float = 0.0
    
    # Swing Points (L-H-HL Structure)
    swing_L: Optional[float] = None
    swing_H: Optional[float] = None
    swing_HL: Optional[float] = None
    swing_L_idx: int = -1
    swing_H_idx: int = -1
    swing_HL_idx: int = -1
    bos_triggered: bool = False
    
    # Plan A: Sideways Scalp (V4)
    plan_a_entry: float = 0.0
    plan_a_tp: float = 0.0
    plan_a_sl: float = 0.0
    
    # Plan B: BOS Breakout (V5)
    plan_b_entry: float = 0.0
    plan_b_tp1: float = 0.0
    plan_b_tp2: float = 0.0
    plan_b_sl: float = 0.0
    
    # Harmonic PRZ (GPS Guideline)
    harmonic_pattern: str = ""
    prz_current: Optional[float] = None
    prz_next: Optional[float] = None
    
    # Risk Management
    atr_15m: float = 0.0
    atr_1h: float = 0.0
    max_risk_pct: float = 1.0
    win_rate_est: float = 0.0
    risk_reward_ratio: float = 0.0
    expected_value: float = 0.0
    confidence: str = "LOW"
    historical_win_rate: float = 0.61
    
    # Session Timestamps
    asia_open: str = "01:00 UTC"
    london_open: str = "08:00 UTC"
    ny_open: str = "13:00 UTC"
    
    # Validation
    is_valid: bool = False
    validation_errors: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict:
        """Convert to dict for API"""
        return {
            "timestamp": self.timestamp,
            "session": self.session,
            "trend_h4": self.trend_h4,
            "market_mode": self.market_mode,
            "current_price": self.current_price,
            "bb": {"upper": self.bb_upper, "middle": self.bb_middle, "lower": self.bb_lower},
            "tunnel": {
                "upper": self.tunnel_upper,
                "lower": self.tunnel_lower,
                "mid": self.tunnel_mid,
                "slope": self.tunnel_slope,
                "valid": self.tunnel_valid
            },
            "golden_zone": {
                "low": self.golden_zone_low,
                "high": self.golden_zone_high
            },
            "plan_a": {
                "entry": self.plan_a_entry,
                "tp": self.plan_a_tp,
                "sl": self.plan_a_sl,
                "description": "Sideways Scalp (Tunnel + BB)"
            },
            "plan_b": {
                "entry": self.plan_b_entry,
                "tp1": self.plan_b_tp1,
                "tp2": self.plan_b_tp2,
                "sl": self.plan_b_sl,
                "description": "BOS Breakout -> New PRZ"
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
    1. Fetch OHLCV data
    2. Compute Market Context (Trend, BB, Session)
    3. Detect Swing Points (Pivot left=5, right=5)
    4. Build Tunnel (Parallel Channel) from Swings
    5. Calculate Golden Auto Fibo from higher timeframe (4H)
    6. Read Harmonic PRZ
    7. Plan A (V4) based on Tunnel + BB + Golden Zone
    8. Plan B (V5) when BOS/Tunnel Break
    9. Validate -> Send Blueprint
    """
    
    def __init__(self):
        self.blueprint = ScenarioBlueprint()
    
    def scan(self, df_4h, df_1h, df_15m) -> ScenarioBlueprint:
        """Scan market and generate Blueprint"""
        
        bp = ScenarioBlueprint()
        bp.timestamp = datetime.now(timezone.utc).isoformat()
        
        # 1. Market Context
        bp.current_price = float(df_15m['close'].iloc[-1])
        bp.atr_15m = self._calc_atr(df_15m, 14)
        bp.atr_1h = self._calc_atr(df_1h, 14)
        
        # Bollinger Bands
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
        
        bp.session = self._get_session()
        
        # 2. Swing Points (Pivot left=5, right=5)
        L_val, L_idx, H_val, H_idx, HL_val, HL_idx = self._find_swing_points(df_15m)
        bp.swing_L = L_val
        bp.swing_H = H_val
        bp.swing_HL = HL_val
        bp.swing_L_idx = L_idx
        bp.swing_H_idx = H_idx
        bp.swing_HL_idx = HL_idx
        
        # BOS trigger
        if H_val and bp.trend_h4 == "UP" and bp.current_price > H_val:
            bp.bos_triggered = True
        elif L_val and bp.trend_h4 == "DOWN" and bp.current_price < L_val:
            bp.bos_triggered = True
        else:
            bp.bos_triggered = False
        
        # 3. Tunnel (Parallel Channel)
        self._calc_tunnel(bp, df_15m)
        
        # 4. Golden Auto Fibo (from 4H structure)
        self._calc_golden_fibo(bp, df_4h)
        
        # 5. Harmonic PRZ (GPS)
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
        
        # 6. Plan A (V4)
        self._set_plan_a(bp)
        
        # 7. Plan B (V5)
        self._set_plan_b(bp)
        
        # 8. Validate
        bp.is_valid, bp.validation_errors = self._validate(bp)
        
        # 9. Metrics
        self._calc_metrics(bp)
        
        self.blueprint = bp
        return bp
    
    # ─── Swing Points Detection (Pivot left=5, right=5) ───
    def _is_pivot_low(self, df, i, left=5, right=5):
        if i < left or i + right >= len(df):
            return False
        window = df['low'].iloc[i-left:i+right+1]
        return df['low'].iloc[i] == window.min()
    
    def _is_pivot_high(self, df, i, left=5, right=5):
        if i < left or i + right >= len(df):
            return False
        window = df['high'].iloc[i-left:i+right+1]
        return df['high'].iloc[i] == window.max()
    
    def _find_swing_points(self, df_15m) -> Tuple[Optional[float], int, Optional[float], int, Optional[float], int]:
        """Detect Swing Points (L, H, HL) using pivot detection"""
        pivot_lows = []
        pivot_highs = []
        for i in range(len(df_15m)):
            if self._is_pivot_low(df_15m, i):
                pivot_lows.append((i, float(df_15m['low'].iloc[i])))
            if self._is_pivot_high(df_15m, i):
                pivot_highs.append((i, float(df_15m['high'].iloc[i])))
        
        if not pivot_lows or not pivot_highs:
            return None, -1, None, -1, None, -1
        
        # L = lowest pivot low
        L_idx, L_val = min(pivot_lows, key=lambda x: x[1])
        
        # H = highest pivot high after L (and above L)
        H_candidates = [(idx, val) for idx, val in pivot_highs if idx > L_idx and val > L_val]
        if not H_candidates:
            H_candidates = [(idx, val) for idx, val in pivot_highs if idx > L_idx]
        if not H_candidates:
            return L_val, L_idx, None, -1, None, -1
        H_idx, H_val = max(H_candidates, key=lambda x: x[1])
        
        # HL = lowest pivot low after H, above L and below H
        HL_candidates = [(idx, val) for idx, val in pivot_lows if idx > H_idx and val > L_val and val < H_val]
        if not HL_candidates:
            HL_candidates = [(idx, val) for idx, val in pivot_lows if idx > H_idx and val > L_val]
        if not HL_candidates:
            return L_val, L_idx, H_val, H_idx, None, -1
        
        HL_idx, HL_val = min(HL_candidates, key=lambda x: x[1])
        return L_val, L_idx, H_val, H_idx, HL_val, HL_idx
    
    # ─── Tunnel (Parallel Channel) ───
    def _calc_tunnel(self, bp: ScenarioBlueprint, df_15m):
        """Calculate Parallel Channel from Swing Points"""
        bp.tunnel_valid = False
        if bp.trend_h4 == "UP" and bp.swing_L and bp.swing_HL and bp.swing_L_idx != -1 and bp.swing_HL_idx != -1:
            L_price = bp.swing_L
            HL_price = bp.swing_HL
            L_idx = bp.swing_L_idx
            HL_idx = bp.swing_HL_idx
            slope = (HL_price - L_price) / (HL_idx - L_idx) if HL_idx != L_idx else 0.0
            bp.tunnel_slope = slope
            
            H_price = bp.swing_H
            H_idx = bp.swing_H_idx if bp.swing_H_idx != -1 else L_idx
            lower_at_H = L_price + slope * (H_idx - L_idx)
            offset = H_price - lower_at_H if H_price > lower_at_H else 0.0
            
            current_idx = len(df_15m) - 1
            bp.tunnel_lower = L_price + slope * (current_idx - L_idx)
            bp.tunnel_upper = bp.tunnel_lower + offset
            bp.tunnel_mid = (bp.tunnel_lower + bp.tunnel_upper) / 2.0
            bp.tunnel_valid = True
            
        elif bp.trend_h4 == "DOWN" and bp.swing_H and bp.swing_HL:
            # For downtrend, treat HL as LH (Lower High)
            H_price = bp.swing_H
            LH_price = bp.swing_HL
            H_idx = bp.swing_H_idx
            LH_idx = bp.swing_HL_idx
            if H_idx != -1 and LH_idx != -1 and LH_idx > H_idx:
                slope = (LH_price - H_price) / (LH_idx - H_idx) if LH_idx != H_idx else 0.0
                bp.tunnel_slope = slope
                L_price = bp.swing_L
                L_idx = bp.swing_L_idx if bp.swing_L_idx != -1 else H_idx
                upper_at_L = H_price + slope * (L_idx - H_idx)
                offset = L_price - upper_at_L if L_price < upper_at_L else 0.0  # offset negative
                
                current_idx = len(df_15m) - 1
                bp.tunnel_upper = H_price + slope * (current_idx - H_idx)
                bp.tunnel_lower = bp.tunnel_upper + offset
                bp.tunnel_mid = (bp.tunnel_lower + bp.tunnel_upper) / 2.0
                bp.tunnel_valid = True
        # else: cannot form valid tunnel
    
    # ─── Golden Auto Fibo (from 4H) ───
    def _calc_golden_fibo(self, bp: ScenarioBlueprint, df_4h):
        """Calculate Golden Zone (0.618-0.786) from 4H swing"""
        df_4h_subset = df_4h.iloc[-200:] if len(df_4h) > 200 else df_4h
        L4, _, H4, _, _, _ = self._find_swing_points(df_4h_subset)
        if L4 and H4 and H4 > L4:
            diff = H4 - L4
            if bp.trend_h4 == "UP":
                bp.golden_zone_low = H4 - diff * 0.618
                bp.golden_zone_high = H4 - diff * 0.786
            else:
                bp.golden_zone_low = L4 + diff * 0.618
                bp.golden_zone_high = L4 + diff * 0.786
    
    # ─── Plan A (V4) ───
    def _set_plan_a(self, bp: ScenarioBlueprint):
        """Set Plan A (V4) using Tunnel, BB, Golden Zone"""
        if bp.current_price <= 0:
            return
        
        if bp.trend_h4 == "UP":
            supports = [bp.bb_lower]
            if bp.tunnel_valid:
                supports.append(bp.tunnel_lower)
            if bp.golden_zone_low > 0:
                supports.append(bp.golden_zone_low)
            support_zone = max(supports)
            
            if bp.current_price <= support_zone * 1.02:
                bp.plan_a_entry = support_zone
                resistance_zone = bp.tunnel_upper if (bp.tunnel_valid and bp.tunnel_upper > bp.current_price) else bp.bb_upper
                bp.plan_a_tp = resistance_zone
                bp.plan_a_sl = support_zone * 0.998
                
        elif bp.trend_h4 == "DOWN":
            resistances = [bp.bb_upper]
            if bp.tunnel_valid:
                resistances.append(bp.tunnel_upper)
            if bp.golden_zone_high > 0:
                resistances.append(bp.golden_zone_high)
            resistance_zone = min(resistances)
            
            if bp.current_price >= resistance_zone * 0.98:
                bp.plan_a_entry = resistance_zone
                support_zone = bp.tunnel_lower if (bp.tunnel_valid and bp.tunnel_lower < bp.current_price) else bp.bb_lower
                bp.plan_a_tp = support_zone
                bp.plan_a_sl = resistance_zone * 1.002
        else:  # SIDEWAYS
            if bp.current_price <= bp.bb_lower * 1.02:
                bp.plan_a_entry = bp.bb_lower
                bp.plan_a_tp = bp.bb_upper
                bp.plan_a_sl = bp.bb_lower * 0.998
            elif bp.current_price >= bp.bb_upper * 0.98:
                bp.plan_a_entry = bp.bb_upper
                bp.plan_a_tp = bp.bb_lower
                bp.plan_a_sl = bp.bb_upper * 1.002
    
    # ─── Plan B (V5) ───
    def _set_plan_b(self, bp: ScenarioBlueprint):
        """Set Plan B (V5) when BOS / Tunnel Break"""
        bos_confirmed = bp.bos_triggered
        if bp.tunnel_valid:
            if bp.trend_h4 == "UP" and bp.current_price > bp.tunnel_upper:
                bos_confirmed = True
            elif bp.trend_h4 == "DOWN" and bp.current_price < bp.tunnel_lower:
                bos_confirmed = True
        
        if not bos_confirmed:
            return
        
        bp.plan_b_entry = bp.current_price
        bp.plan_b_sl = bp.tunnel_mid if (bp.tunnel_valid and bp.tunnel_mid > 0) else bp.bb_middle
        
        if bp.prz_next:
            range_to_prz = abs(bp.prz_next - bp.current_price)
            if bp.trend_h4 == "UP":
                bp.plan_b_tp1 = bp.current_price + range_to_prz * 0.618
                bp.plan_b_tp2 = bp.prz_next
            else:
                bp.plan_b_tp1 = bp.current_price - range_to_prz * 0.618
                bp.plan_b_tp2 = bp.prz_next
        else:
            mult = 1 if bp.trend_h4 == "UP" else -1
            bp.plan_b_tp1 = bp.current_price + mult * bp.atr_1h * 2
            bp.plan_b_tp2 = bp.current_price + mult * bp.atr_1h * 3
    
    # ─── Metrics ───
    def _calc_metrics(self, bp: ScenarioBlueprint):
        """Calculate Win Rate, RR, Expected Value, Confidence"""
        if bp.plan_a_tp > 0 and bp.plan_a_sl > 0 and bp.plan_a_entry > 0:
            reward_a = abs(bp.plan_a_tp - bp.plan_a_entry)
            risk_a = abs(bp.plan_a_entry - bp.plan_a_sl)
            if risk_a > 0:
                bp.risk_reward_ratio = reward_a / risk_a
        
        if bp.plan_b_tp2 > 0 and bp.plan_b_sl > 0 and bp.plan_b_entry > 0:
            reward_b = abs(bp.plan_b_tp2 - bp.plan_b_entry)
            risk_b = abs(bp.plan_b_entry - bp.plan_b_sl)
            if risk_b > 0:
                bp.risk_reward_ratio = max(bp.risk_reward_ratio, reward_b / risk_b)
        
        win_rate_map = {"TRENDING": 0.65, "PULLBACK": 0.55, "SIDEWAYS": 0.45}
        bp.win_rate_est = win_rate_map.get(bp.market_mode, 0.50)
        
        if bp.risk_reward_ratio > 0:
            bp.expected_value = (bp.win_rate_est * bp.risk_reward_ratio) - ((1 - bp.win_rate_est) * 1)
        
        if bp.risk_reward_ratio >= 2.0 and bp.win_rate_est >= 0.60:
            bp.confidence = "HIGH"
        elif bp.risk_reward_ratio >= 1.5 or bp.win_rate_est >= 0.55:
            bp.confidence = "MEDIUM"
        else:
            bp.confidence = "LOW"
    
    # ─── Utilities ───
    def _calc_atr(self, df, period=14) -> float:
        high, low, close = df['high'], df['low'], df['close'].shift(1)
        tr1 = high - low
        tr2 = (high - close).abs()
        tr3 = (low - close).abs()
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    
    def _get_session(self) -> str:
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
        errors = []
        if not bp.swing_L:
            errors.append("No Swing L found")
        if bp.plan_a_entry > 0 and bp.plan_a_tp <= bp.plan_a_entry:
            errors.append("Plan A: TP <= Entry (BUY) or TP >= Entry (SELL)")
        if bp.atr_15m == 0:
            errors.append("ATR is zero")
        if not bp.tunnel_valid:
            logger.info("Tunnel not valid, using BB only")
        return len(errors) == 0, errors


# ═══════════════════════════════════════════════════════
# SINGLETON
# ═══════════════════════════════════════════════════════
scanner = ScenarioScanner()
