from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime, timezone

from scenario_blueprint import ScenarioBlueprint


# =========================================================
# SCENARIO SCANNER v12 (CONTRACT-LOCK COMPLIANT)
# RULE:
# - ONLY fills ScenarioBlueprint fields
# - NO extra attributes
# - NO decision logic
# - NO scoring logic
# =========================================================


class ScenarioScanner:

    def __init__(self):
        pass

    # =====================================================
    # MAIN ENTRY (STRICT CONTRACT)
    # =====================================================
    def scan(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> ScenarioBlueprint:

        bp = ScenarioBlueprint(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol="XAUUSD"
        )

        # =====================================================
        # 1. PRICE CORE
        # =====================================================
        bp = self._set_price(bp, df_15m)

        # =====================================================
        # 2. TREND
        # =====================================================
        bp = self._set_trend(bp, df_4h, df_1h)

        # =====================================================
        # 3. MARKET MODE
        # =====================================================
        bp = self._set_market_mode(bp)

        # =====================================================
        # 4. STRUCTURE (SWINGS + BOS)
        # =====================================================
        bp = self._set_structure(bp, df_15m)

        # =====================================================
        # 5. BB (BASE INDICATOR ONLY)
        # =====================================================
        bp = self._set_bollinger(bp, df_15m)

        # =====================================================
        # 6. TUNNEL (STRUCTURE CHANNEL)
        # =====================================================
        bp = self._set_tunnel(bp, df_15m)

        # =====================================================
        # 7. GOLDEN ZONE
        # =====================================================
        bp = self._set_golden_zone(bp, df_4h)

        # =====================================================
        # 8. HARMONIC / PRZ
        # =====================================================
        bp = self._set_harmonic(bp, df_15m)

        # =====================================================
        # 9. RISK CORE (NO DECISION)
        # =====================================================
        bp = self._set_risk(bp, df_15m, df_1h)

        # =====================================================
        # 10. VALIDATION
        # =====================================================
        bp = self._validate(bp)

        return bp

    # =========================================================
    # PRICE
    # =========================================================
    def _set_price(self, bp, df_15m):
        bp = bp.__class__(**{**bp.__dict__, "current_price": float(df_15m["close"].iloc[-1])})
        return bp

    # =========================================================
    # TREND
    # =========================================================
    def _set_trend(self, bp, df_4h, df_1h):

        ema20_h4 = df_4h["close"].ewm(span=20).mean().iloc[-1]
        ema50_h4 = df_4h["close"].ewm(span=50).mean().iloc[-1]

        ema20_h1 = df_1h["close"].ewm(span=20).mean().iloc[-1]
        ema50_h1 = df_1h["close"].ewm(span=50).mean().iloc[-1]

        trend_h4 = "UP" if ema20_h4 > ema50_h4 else "DOWN"
        trend_h1 = "UP" if ema20_h1 > ema50_h1 else "DOWN"

        return bp.__class__(**{
            **bp.__dict__,
            "trend_h4": trend_h4,
            "trend_h1": trend_h1
        })

    # =========================================================
    # MARKET MODE
    # =========================================================
    def _set_market_mode(self, bp):

        mode = "TRENDING" if bp.trend_h4 == bp.trend_h1 else "SIDEWAYS"

        return bp.__class__(**{
            **bp.__dict__,
            "market_mode": mode
        })

    # =========================================================
    # STRUCTURE
    # =========================================================
    def _set_structure(self, bp, df):

        swing_L = float(df["low"].rolling(20).min().iloc[-1])
        swing_H = float(df["high"].rolling(20).max().iloc[-1])

        bos = bp.current_price > swing_H if bp.trend_h4 == "UP" else bp.current_price < swing_L

        return bp.__class__(**{
            **bp.__dict__,
            "swing_L": swing_L,
            "swing_H": swing_H,
            "bos_triggered": bool(bos)
        })

    # =========================================================
    # BOLLINGER (BASE ONLY)
    # =========================================================
    def _set_bollinger(self, bp, df):

        mid = df["close"].rolling(20).mean()
        std = df["close"].rolling(20).std()

        upper = mid + (2 * std)
        lower = mid - (2 * std)

        return bp.__class__(**{
            **bp.__dict__,
            "bb_upper": float(upper.iloc[-1]),
            "bb_middle": float(mid.iloc[-1]),
            "bb_lower": float(lower.iloc[-1])
        })

    # =========================================================
    # TUNNEL (STRUCTURE CHANNEL)
    # =========================================================
    def _set_tunnel(self, bp, df):

        high = df["high"].rolling(30).max().iloc[-1]
        low = df["low"].rolling(30).min().iloc[-1]

        mid = (high + low) / 2

        return bp.__class__(**{
            **bp.__dict__,
            "tunnel_upper": float(high),
            "tunnel_lower": float(low),
            "tunnel_mid": float(mid),
            "tunnel_valid": True
        })

    # =========================================================
    # GOLDEN ZONE
    # =========================================================
    def _set_golden_zone(self, bp, df4h):

        high = df4h["high"].rolling(50).max().iloc[-1]
        low = df4h["low"].rolling(50).min().iloc[-1]

        return bp.__class__(**{
            **bp.__dict__,
            "golden_zone_low": float(low),
            "golden_zone_high": float(high)
        })

    # =========================================================
    # HARMONIC / PRZ
    # =========================================================
    def _set_harmonic(self, bp, df):

        high = df["high"].rolling(50).max().iloc[-1]
        low = df["low"].rolling(50).min().iloc[-1]

        range_ = high - low

        prz_current = low + (range_ * 0.618)
        prz_next = low + (range_ * 0.786)

        return bp.__class__(**{
            **bp.__dict__,
            "prz_current": float(prz_current),
            "prz_next": float(prz_next),
            "harmonic_pattern": "AUTO_RANGE_618"
        })

    # =========================================================
    # RISK CORE
    # =========================================================
    def _set_risk(self, bp, df15, df1h):

        tr = pd.concat([
            df15["high"] - df15["low"],
            (df15["high"] - df15["close"].shift(1)).abs(),
            (df15["low"] - df15["close"].shift(1)).abs()
        ], axis=1).max(axis=1)

        atr_15m = float(tr.rolling(14).mean().iloc[-1])

        atr_1h = float(df1h["high"].rolling(14).mean().iloc[-1] - df1h["low"].rolling(14).mean().iloc[-1])

        rr = 2.0
        ev = rr * 0.5  # placeholder deterministic expectation

        confidence = "HIGH" if atr_15m > 0 else "LOW"

        return bp.__class__(**{
            **bp.__dict__,
            "atr_15m": atr_15m,
            "atr_1h": atr_1h,
            "risk_reward_ratio": rr,
            "expected_value": ev,
            "confidence": confidence
        })

    # =========================================================
    # VALIDATION (STRICT CONTRACT)
    # =========================================================
    def _validate(self, bp):

        errors = []

        if bp.current_price <= 0:
            errors.append("INVALID_PRICE")

        if bp.trend_h4 not in ["UP", "DOWN", "NEUTRAL"]:
            errors.append("INVALID_TREND_H4")

        if bp.trend_h1 not in ["UP", "DOWN", "NEUTRAL"]:
            errors.append("INVALID_TREND_H1")

        is_valid = len(errors) == 0

        return bp.__class__(**{
            **bp.__dict__,
            "is_valid": is_valid,
            "validation_errors": errors
        })


scanner = ScenarioScanner()
