from __future__ import annotations

import pandas as pd
import numpy as np
from datetime import datetime, timezone

from scenario_blueprint import ScenarioBlueprint


# =========================================================
# SCENARIO SCANNER v5.4 (CONTEXT BUILDER + INTELLIGENCE LAYER)
# =========================================================
# RULE:
# - Scanner owns ALL market intelligence logic
# - Composer = gate only
# - DecisionEngine = decision only
# =========================================================


class ScenarioScanner:

    def __init__(self):
        pass

    # -----------------------------------------------------
    # MAIN ENTRY
    # -----------------------------------------------------
    def scan(self, df_4h, df_1h, df_15m) -> ScenarioBlueprint:

        bp = ScenarioBlueprint(timestamp=datetime.now(timezone.utc).isoformat())

        # =====================================================
        # 1. PRICE CORE
        # =====================================================
        bp.current_price = float(df_15m['close'].iloc[-1])

        # =====================================================
        # 2. TREND (H4 / H1)
        # =====================================================
        ema20_h4 = df_4h['close'].ewm(span=20).mean().iloc[-1]
        ema50_h4 = df_4h['close'].ewm(span=50).mean().iloc[-1]

        ema20_h1 = df_1h['close'].ewm(span=20).mean().iloc[-1]
        ema50_h1 = df_1h['close'].ewm(span=50).mean().iloc[-1]

        bp.trend_h4 = "UP" if ema20_h4 > ema50_h4 else "DOWN"
        bp.trend_h1 = "UP" if ema20_h1 > ema50_h1 else "DOWN"

        bp.market_mode = (
            "TRENDING" if bp.trend_h4 == bp.trend_h1 else "PULLBACK"
        )

        # =====================================================
        # 3. BOS (STRUCTURE BREAK)
        # =====================================================
        swing_high = df_15m['high'].rolling(10).max().iloc[-2]
        swing_low = df_15m['low'].rolling(10).min().iloc[-2]

        bp.bos_triggered = (
            bp.current_price > swing_high
            if bp.trend_h4 == "UP"
            else bp.current_price < swing_low
        )

        # =====================================================
        # 4. SMC (SIMPLIFIED STRUCTURE CONTEXT)
        # =====================================================
        # Internal liquidity shift approximation
        recent_high = df_15m['high'].iloc[-20:].max()
        recent_low = df_15m['low'].iloc[-20:].min()

        smc_bull = bp.current_price > (recent_high * 0.98)
        smc_bear = bp.current_price < (recent_low * 1.02)

        bp.smc_confirmed = smc_bull or smc_bear

        # =====================================================
        # 5. VSA (VOLUME SPIKE CONFIRMATION)
        # =====================================================
        tr = pd.concat([
            df_15m['high'] - df_15m['low'],
            (df_15m['high'] - df_15m['close'].shift(1)).abs(),
            (df_15m['low'] - df_15m['close'].shift(1)).abs()
        ], axis=1).max(axis=1)

        atr = tr.rolling(14).mean().iloc[-1]
        last = df_15m.iloc[-1]

        body = abs(last['close'] - last['open'])
        rng = last['high'] - last['low']

        vsa_spike = rng > atr * 1.5 and body > rng * 0.6

        bp.vsa_confirmed = bool(vsa_spike)

        # =====================================================
        # 6. PRZ (HARMONIC / STRUCTURE ZONE)
        # =====================================================
        # simplified PRZ from structure extremes
        hh = df_15m['high'].rolling(30).max().iloc[-1]
        ll = df_15m['low'].rolling(30).min().iloc[-1]

        if bp.trend_h4 == "UP":
            bp.prz_support_bottom = ll
            bp.prz_support_top = ll + (hh - ll) * 0.618
        else:
            bp.prz_support_bottom = hh - (hh - ll) * 0.618
            bp.prz_support_top = hh

        # =====================================================
        # 7. SCORE (BASIC CONFLUENCE)
        # =====================================================
        score = 0

        if bp.bos_triggered:
            score += 2
        if bp.smc_confirmed:
            score += 2
        if bp.vsa_confirmed:
            score += 2

        if bp.trend_h4 == bp.trend_h1:
            score += 1

        bp.base_score = score

        # =====================================================
        # 8. DECISION BIAS (NOT FINAL DECISION)
        # =====================================================
        if score >= 5:
            bp.decision_bias = "STRONG"
        elif score >= 3:
            bp.decision_bias = "MODERATE"
        else:
            bp.decision_bias = "WEAK"

        return bp

scanner = ScenarioScanner()
