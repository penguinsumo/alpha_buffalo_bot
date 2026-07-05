from __future__ import annotations

from datetime import datetime, timezone
from typing import Tuple

import pandas as pd

from scenario_blueprint import ScenarioBlueprint


class ScenarioScanner:
    def scan(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame, symbol: str = "XAUUSD") -> ScenarioBlueprint:
        self._validate_df(df_4h, "4h")
        self._validate_df(df_1h, "1h")
        self._validate_df(df_15m, "15m")

        current_price = float(df_15m["close"].iloc[-1])

        trend_h4 = self._trend(df_4h)
        trend_h1 = self._trend(df_1h)
        market_mode = "TRENDING" if trend_h4 == trend_h1 else "PULLBACK"

        bb_upper, bb_middle, bb_lower = self._bollinger(df_15m)
        swing_L, swing_H, swing_L_idx, swing_H_idx = self._swings(df_15m)

        bos_triggered = (
            current_price > swing_H if trend_h4 == "UP"
            else current_price < swing_L if trend_h4 == "DOWN"
            else False
        )

        tunnel_upper = float(df_15m["high"].tail(30).max())
        tunnel_lower = float(df_15m["low"].tail(30).min())
        tunnel_mid = float((tunnel_upper + tunnel_lower) / 2)

        gz_low, gz_high = self._golden_zone(df_4h)

        atr_15m = self._atr(df_15m)
        atr_1h = self._atr(df_1h)

        smc_confirmed = self._smc_proxy(df_15m, current_price)
        vsa_confirmed = self._vsa_proxy(df_15m, atr_15m)

        prz_support_bottom, prz_support_top, prz_current, prz_next = self._prz(df_15m, trend_h4)

        base_score = 0
        if bos_triggered:
            base_score += 2
        if smc_confirmed:
            base_score += 2
        if vsa_confirmed:
            base_score += 2
        if trend_h4 == trend_h1 and trend_h4 in ("UP", "DOWN"):
            base_score += 1
        if prz_support_top > 0 and prz_support_bottom > 0:
            base_score += 1

        if base_score >= 6:
            decision_bias = "STRONG"
        elif base_score >= 3:
            decision_bias = "MODERATE"
        else:
            decision_bias = "WEAK"

        plan_a_entry = current_price
        plan_a_sl = current_price - atr_15m if trend_h4 == "UP" else current_price + atr_15m
        plan_a_tp = current_price + (atr_15m * 2) if trend_h4 == "UP" else current_price - (atr_15m * 2)

        plan_b_entry = swing_H if trend_h4 == "UP" else swing_L
        plan_b_sl = swing_L if trend_h4 == "UP" else swing_H
        plan_b_tp1 = plan_b_entry + (atr_15m * 2) if trend_h4 == "UP" else plan_b_entry - (atr_15m * 2)
        plan_b_tp2 = plan_b_entry + (atr_15m * 3) if trend_h4 == "UP" else plan_b_entry - (atr_15m * 3)

        errors = []
        if current_price <= 0:
            errors.append("INVALID_PRICE")
        if len(df_15m) < 50:
            errors.append("INSUFFICIENT_15M_DATA")
        if len(df_1h) < 50:
            errors.append("INSUFFICIENT_1H_DATA")
        if len(df_4h) < 50:
            errors.append("INSUFFICIENT_4H_DATA")

        risk_reward_ratio = 2.0 if atr_15m > 0 else 0.0
        expected_value = round((0.61 * risk_reward_ratio) - (1 - 0.61), 4) if risk_reward_ratio else 0.0
        confidence = "HIGH" if base_score >= 6 else "MEDIUM" if base_score >= 3 else "LOW"

        return ScenarioBlueprint(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            trend_h4=trend_h4,
            trend_h1=trend_h1,
            market_mode=market_mode,
            current_price=current_price,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            tunnel_upper=tunnel_upper,
            tunnel_lower=tunnel_lower,
            tunnel_mid=tunnel_mid,
            tunnel_slope=0.0,
            tunnel_valid=True,
            golden_zone_low=gz_low,
            golden_zone_high=gz_high,
            swing_L=swing_L,
            swing_H=swing_H,
            swing_HL=swing_L if trend_h4 == "UP" else swing_H,
            swing_L_idx=swing_L_idx,
            swing_H_idx=swing_H_idx,
            swing_HL_idx=swing_L_idx if trend_h4 == "UP" else swing_H_idx,
            bos_triggered=bool(bos_triggered),
            plan_a_entry=round(plan_a_entry, 3),
            plan_a_tp=round(plan_a_tp, 3),
            plan_a_sl=round(plan_a_sl, 3),
            plan_b_entry=round(plan_b_entry, 3),
            plan_b_tp1=round(plan_b_tp1, 3),
            plan_b_tp2=round(plan_b_tp2, 3),
            plan_b_sl=round(plan_b_sl, 3),
            harmonic_pattern="AUTO_RANGE_618" if prz_current else "",
            prz_current=round(prz_current, 3) if prz_current else None,
            prz_next=round(prz_next, 3) if prz_next else None,
            atr_15m=round(atr_15m, 3),
            atr_1h=round(atr_1h, 3),
            win_rate_est=0.61,
            risk_reward_ratio=risk_reward_ratio,
            expected_value=expected_value,
            confidence=confidence,
            base_score=base_score,
            decision_bias=decision_bias,
            smc_confirmed=bool(smc_confirmed),
            vsa_confirmed=bool(vsa_confirmed),
            prz_support_top=round(prz_support_top, 3),
            prz_support_bottom=round(prz_support_bottom, 3),
            is_valid=len(errors) == 0,
            validation_errors=errors,
        )

    def _validate_df(self, df: pd.DataFrame, label: str) -> None:
        required = {"open", "high", "low", "close"}
        if df is None or df.empty:
            raise ValueError(f"EMPTY_DF_{label}")
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"MISSING_COLUMNS_{label}:{sorted(missing)}")

    def _trend(self, df: pd.DataFrame) -> str:
        ema20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
        ema50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
        if ema20 > ema50:
            return "UP"
        if ema20 < ema50:
            return "DOWN"
        return "NEUTRAL"

    def _bollinger(self, df: pd.DataFrame) -> Tuple[float, float, float]:
        mid = df["close"].rolling(20).mean().iloc[-1]
        std = df["close"].rolling(20).std().iloc[-1]
        return round(float(mid + (2 * std)), 3), round(float(mid), 3), round(float(mid - (2 * std)), 3)

    def _swings(self, df: pd.DataFrame) -> Tuple[float, float, int, int]:
        lookback = df.tail(30)
        low_idx = int(lookback["low"].idxmin())
        high_idx = int(lookback["high"].idxmax())
        return float(lookback["low"].min()), float(lookback["high"].max()), low_idx, high_idx

    def _golden_zone(self, df: pd.DataFrame) -> Tuple[float, float]:
        high = float(df["high"].tail(50).max())
        low = float(df["low"].tail(50).min())
        diff = high - low
        return round(low + (diff * 0.5), 3), round(low + (diff * 0.618), 3)

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        tr = pd.concat(
            [
                df["high"] - df["low"],
                (df["high"] - df["close"].shift(1)).abs(),
                (df["low"] - df["close"].shift(1)).abs(),
            ],
            axis=1,
        ).max(axis=1)
        value = tr.rolling(period).mean().iloc[-1]
        return float(value) if pd.notna(value) else 0.0

    def _smc_proxy(self, df: pd.DataFrame, current_price: float) -> bool:
        recent_high = float(df["high"].tail(20).max())
        recent_low = float(df["low"].tail(20).min())
        return current_price > (recent_high * 0.98) or current_price < (recent_low * 1.02)

    def _vsa_proxy(self, df: pd.DataFrame, atr: float) -> bool:
        last = df.iloc[-1]
        rng = float(last["high"] - last["low"])
        body = abs(float(last["close"] - last["open"]))
        return atr > 0 and rng > atr * 1.5 and body > rng * 0.6

    def _prz(self, df: pd.DataFrame, trend: str) -> Tuple[float, float, float, float]:
        high = float(df["high"].tail(50).max())
        low = float(df["low"].tail(50).min())
        diff = high - low

        if diff <= 0:
            return 0.0, 0.0, 0.0, 0.0

        prz_current = low + diff * 0.618
        prz_next = low + diff * 0.786

        if trend == "UP":
            support_bottom = low
            support_top = low + diff * 0.618
        else:
            support_bottom = high - diff * 0.618
            support_top = high

        return support_bottom, support_top, prz_current, prz_next


scanner = ScenarioScanner()
