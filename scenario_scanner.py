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

        prior_window = df_15m.iloc[-60:-30] if len(df_15m) >= 60 else df_15m.tail(30)
        prior_tunnel_upper = float(prior_window["high"].max())
        prior_tunnel_lower = float(prior_window["low"].min())
        prior_tunnel_mid = float((prior_tunnel_upper + prior_tunnel_lower) / 2)

        gz_low, gz_high = self._golden_zone(df_4h)

        atr_15m = self._atr(df_15m)
        atr_1h = self._atr(df_1h)

        ha_m15_bullish, ha_m15_bearish = self._heikin_ashi_state(df_15m)
        ha_h1_bullish, ha_h1_bearish = self._heikin_ashi_state(df_1h)

        m15_delta = self._delta_state(df_15m)
        h1_delta = self._delta_state(df_1h)
        h4_delta = self._delta_state(df_4h)

        m15_phase = self._phase_state(df_15m)
        h1_phase = self._phase_state(df_1h)
        h4_phase = self._phase_state(df_4h)

        m15_impulse = self._is_impulse(df_15m, m15_delta, atr_15m)
        h1_impulse = self._is_impulse(df_1h, h1_delta, atr_1h)

        previous_close = float(df_15m["close"].iloc[-2]) if len(df_15m) > 1 else current_price
        price_reclaimed_bb_middle = previous_close <= bb_middle <= current_price
        price_reclaimed_tunnel_mid = previous_close <= tunnel_mid <= current_price
        price_above_mid_support = (
            current_price >= bb_middle
            and current_price >= tunnel_mid
            and (previous_close <= bb_middle or previous_close <= tunnel_mid)
        )

        early_buy_reclaim_watch = (
            trend_h4 == "UP"
            and ha_m15_bullish
            and m15_delta in ("NEUTRAL", "UP")
            and h1_delta != "DOWN"
            and not (h1_phase == "IMPULSE_DOWN" and ha_h1_bearish)
            and (
                price_reclaimed_bb_middle
                or price_reclaimed_tunnel_mid
                or (
                    price_above_mid_support
                    and m15_phase in ("PULLBACK_UP", "IMPULSE_UP")
                )
            )
        )

        if m15_delta == "UP" and h1_delta == "UP":
            delta_alignment = "BULLISH"
        elif m15_delta == "DOWN" and h1_delta == "DOWN":
            delta_alignment = "BEARISH"
        else:
            delta_alignment = "MIXED"

        if (
            m15_delta == "UP"
            and ha_m15_bullish
            and current_price >= bb_middle
            and (h1_delta == "UP" or trend_h4 == "UP")
        ):
            watch_bias = "BUY"
            impulse_direction = "BUY" if m15_impulse else "NONE"
        elif early_buy_reclaim_watch:
            watch_bias = "BUY_WATCH"
            impulse_direction = "NONE"
        elif (
            m15_delta == "DOWN"
            and ha_m15_bearish
            and current_price <= bb_middle
            and (h1_delta == "DOWN" or trend_h4 == "DOWN")
        ):
            watch_bias = "SELL"
            impulse_direction = "SELL" if m15_impulse else "NONE"
        else:
            watch_bias = "NONE"
            impulse_direction = "NONE"

        tunnel_slope = tunnel_mid - prior_tunnel_mid
        tunnel_width = max(tunnel_upper - tunnel_lower, 0.0)
        tunnel_tolerance = max(atr_15m * 0.35, tunnel_width * 0.08)
        slope_threshold = max(atr_15m * 0.10, 0.1)

        if tunnel_slope > slope_threshold:
            tunnel_state = "UPTREND"
        elif tunnel_slope < -slope_threshold:
            tunnel_state = "DOWNTREND"
        else:
            tunnel_state = "FLAT"

        inside_tunnel = tunnel_lower <= current_price <= tunnel_upper
        near_tunnel_upper = abs(current_price - tunnel_upper) <= tunnel_tolerance
        near_tunnel_mid = abs(current_price - tunnel_mid) <= tunnel_tolerance
        near_tunnel_lower = abs(current_price - tunnel_lower) <= tunnel_tolerance
        tunnel_retest_valid = (
            (trend_h4 == "UP" and tunnel_state in ("UPTREND", "FLAT") and (near_tunnel_lower or near_tunnel_mid))
            or (trend_h4 == "DOWN" and tunnel_state in ("DOWNTREND", "FLAT") and (near_tunnel_upper or near_tunnel_mid))
        )

        smc_confirmed = self._smc_proxy(df_15m, current_price)
        vsa_confirmed = self._vsa_proxy(df_15m, atr_15m)

        prz_support_bottom, prz_support_top, prz_current, prz_next = self._prz(df_15m, trend_h4)

        htf_support_low, htf_support_high, htf_resistance_low, htf_resistance_high = self._prz_zone(df_1h)
        micro_support_low, micro_support_high, micro_resistance_low, micro_resistance_high = self._prz_zone(df_15m)

        harmonic_prz_low = min(prz_current, prz_next) if prz_current and prz_next else 0.0
        harmonic_prz_high = max(prz_current, prz_next) if prz_current and prz_next else 0.0

        inside_htf_prz = (
            self._inside_zone(current_price, htf_support_low, htf_support_high)
            or self._inside_zone(current_price, htf_resistance_low, htf_resistance_high)
        )
        inside_micro_prz = (
            self._inside_zone(current_price, micro_support_low, micro_support_high)
            or self._inside_zone(current_price, micro_resistance_low, micro_resistance_high)
        )

        if trend_h4 == "UP":
            micro_prz_broken = current_price < micro_support_low
            micro_prz_reclaimed = previous_close < micro_support_low and self._inside_zone(current_price, micro_support_low, micro_support_high)
        elif trend_h4 == "DOWN":
            micro_prz_broken = current_price > micro_resistance_high
            micro_prz_reclaimed = previous_close > micro_resistance_high and self._inside_zone(current_price, micro_resistance_low, micro_resistance_high)
        else:
            micro_prz_broken = False
            micro_prz_reclaimed = False

        if micro_prz_broken:
            prz_state = "BROKEN"
        elif micro_prz_reclaimed:
            prz_state = "RECLAIMED"
        elif inside_micro_prz:
            prz_state = "ACTIVE"
        else:
            prz_state = "OUTSIDE"

        reversal_allowed = prz_state in ("ACTIVE", "RECLAIMED") and not micro_prz_broken

        if micro_prz_broken and tunnel_state in ("UPTREND", "DOWNTREND"):
            trade_plan = "TUNNEL_WATCH"
        elif micro_prz_broken:
            trade_plan = "NO_TRADE"
        elif reversal_allowed:
            trade_plan = "PRZ_REVERSAL_WATCH"
        elif early_buy_reclaim_watch:
            trade_plan = "EARLY_BUY_RECLAIM_WATCH"
        else:
            trade_plan = "NONE"

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
        sl_buffer = max(atr_15m * 0.35, 1.0)

        if trend_h4 == "UP":
            buy_sl_candidates = [
                swing_L,
                bb_lower,
                tunnel_lower,
                micro_support_low,
                htf_support_low,
            ]
            buy_sl_candidates = [x for x in buy_sl_candidates if x and x > 0]
            sl_invalidation = min(buy_sl_candidates) if buy_sl_candidates else current_price - atr_15m

            plan_a_sl = sl_invalidation - sl_buffer
            plan_a_tp = current_price + (atr_15m * 2)
            plan_b_entry = swing_H
            plan_b_sl = plan_a_sl
            plan_b_tp1 = plan_b_entry + (atr_15m * 2)
            plan_b_tp2 = plan_b_entry + (atr_15m * 3)

        elif trend_h4 == "DOWN":
            sell_sl_candidates = [
                swing_H,
                bb_upper,
                tunnel_upper,
                micro_resistance_high,
                htf_resistance_high,
            ]
            sell_sl_candidates = [x for x in sell_sl_candidates if x and x > 0]
            sl_invalidation = max(sell_sl_candidates) if sell_sl_candidates else current_price + atr_15m

            plan_a_sl = sl_invalidation + sl_buffer
            plan_a_tp = current_price - (atr_15m * 2)
            plan_b_entry = swing_L
            plan_b_sl = plan_a_sl
            plan_b_tp1 = plan_b_entry - (atr_15m * 2)
            plan_b_tp2 = plan_b_entry - (atr_15m * 3)

        else:
            plan_a_sl = current_price - atr_15m
            plan_a_tp = current_price + (atr_15m * 2)
            plan_b_entry = swing_H
            plan_b_sl = swing_L
            plan_b_tp1 = plan_b_entry + (atr_15m * 2)
            plan_b_tp2 = plan_b_entry + (atr_15m * 3)

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

        print(
            "AlphaBuffalo scanner state | "
            f"symbol={symbol} price={round(current_price, 3)} "
            f"trend_h4={trend_h4} trend_h1={trend_h1} mode={market_mode} "
            f"m15_phase={m15_phase} h1_phase={h1_phase} h4_phase={h4_phase} "
            f"m15_delta={m15_delta} h1_delta={h1_delta} h4_delta={h4_delta} "
            f"m15_impulse={m15_impulse} h1_impulse={h1_impulse} "
            f"ha_m15_bull={ha_m15_bullish} ha_m15_bear={ha_m15_bearish} "
            f"ha_h1_bull={ha_h1_bullish} ha_h1_bear={ha_h1_bearish} "
            f"watch_bias={watch_bias} delta_alignment={delta_alignment} "
            f"impulse_direction={impulse_direction} "
            f"trade_plan={trade_plan} early_buy_reclaim={early_buy_reclaim_watch} "
            f"price_reclaimed_bb_mid={price_reclaimed_bb_middle} "
            f"price_reclaimed_tunnel_mid={price_reclaimed_tunnel_mid} "
            f"price_above_mid_support={price_above_mid_support} "
            f"prev_close={round(previous_close, 3)} "
            f"bb_middle={round(bb_middle, 3)} "
            f"tunnel_mid={round(tunnel_mid, 3)} "
            f"prz_state={prz_state} "
            f"micro_broken={micro_prz_broken} micro_reclaimed={micro_prz_reclaimed}",
            flush=True,
        )

        return ScenarioBlueprint(
            timestamp=datetime.now(timezone.utc).isoformat(),
            symbol=symbol,
            trend_h4=trend_h4,
            trend_h1=trend_h1,
            market_mode=market_mode,
            m15_phase=m15_phase,
            h1_phase=h1_phase,
            h4_phase=h4_phase,
            m15_delta=m15_delta,
            h1_delta=h1_delta,
            h4_delta=h4_delta,
            m15_impulse=bool(m15_impulse),
            h1_impulse=bool(h1_impulse),
            ha_m15_bullish=bool(ha_m15_bullish),
            ha_m15_bearish=bool(ha_m15_bearish),
            ha_h1_bullish=bool(ha_h1_bullish),
            ha_h1_bearish=bool(ha_h1_bearish),
            watch_bias=watch_bias,
            delta_alignment=delta_alignment,
            impulse_direction=impulse_direction,
            current_price=current_price,
            bb_upper=bb_upper,
            bb_middle=bb_middle,
            bb_lower=bb_lower,
            tunnel_upper=tunnel_upper,
            tunnel_lower=tunnel_lower,
            tunnel_mid=tunnel_mid,
            tunnel_slope=round(tunnel_slope, 3),
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
            htf_prz_support_low=round(htf_support_low, 3),
            htf_prz_support_high=round(htf_support_high, 3),
            htf_prz_resistance_low=round(htf_resistance_low, 3),
            htf_prz_resistance_high=round(htf_resistance_high, 3),
            harmonic_prz_low=round(harmonic_prz_low, 3),
            harmonic_prz_high=round(harmonic_prz_high, 3),
            micro_prz_low=round(micro_support_low, 3),
            micro_prz_high=round(micro_resistance_high, 3),
            inside_htf_prz=bool(inside_htf_prz),
            inside_micro_prz=bool(inside_micro_prz),
            zone_validated=bool(reversal_allowed),
            zone_invalidated=bool(micro_prz_broken),
            prz_state=prz_state,
            micro_prz_broken=bool(micro_prz_broken),
            micro_prz_reclaimed=bool(micro_prz_reclaimed),
            reversal_allowed=bool(reversal_allowed),
            tunnel_state=tunnel_state,
            inside_tunnel=bool(inside_tunnel),
            near_tunnel_upper=bool(near_tunnel_upper),
            near_tunnel_mid=bool(near_tunnel_mid),
            near_tunnel_lower=bool(near_tunnel_lower),
            tunnel_retest_valid=bool(tunnel_retest_valid),
            trade_plan=trade_plan,
            execution_state="WATCH",
            is_valid=len(errors) == 0,
            validation_errors=errors,
        )

    def _heikin_ashi_state(self, df: pd.DataFrame) -> Tuple[bool, bool]:
        if len(df) < 3:
            return False, False

        ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
        ha_open = [float((df["open"].iloc[0] + df["close"].iloc[0]) / 2)]

        for i in range(1, len(df)):
            ha_open.append(float((ha_open[i - 1] + ha_close.iloc[i - 1]) / 2))

        last_ha_open = float(ha_open[-1])
        last_ha_close = float(ha_close.iloc[-1])
        prev_ha_close = float(ha_close.iloc[-2])

        bullish = last_ha_close > last_ha_open and last_ha_close >= prev_ha_close
        bearish = last_ha_close < last_ha_open and last_ha_close <= prev_ha_close

        return bool(bullish), bool(bearish)

    def _is_impulse(self, df: pd.DataFrame, delta_state: str, atr_value: float) -> bool:
        if len(df) < 5:
            return False

        if delta_state not in ("UP", "DOWN", "DELTA_PLUS", "DELTA_MINUS"):
            return False

        body = abs(float(df["close"].iloc[-1]) - float(df["open"].iloc[-1]))
        recent_move = abs(float(df["close"].iloc[-1]) - float(df["close"].iloc[-4]))
        avg_range = float((df["high"] - df["low"]).tail(20).mean())
        threshold = max(float(atr_value) * 0.35, avg_range * 0.55, 0.01)

        return bool(body >= threshold or recent_move >= threshold)


    def _delta_state(self, df: pd.DataFrame, lookback: int = 3) -> str:
        """
        Short-horizon price delta heuristic.
        คืนค่า: UP / DOWN / NEUTRAL
        """
        if len(df) < lookback + 2:
            return "NEUTRAL"

        avg_range = float((df["high"] - df["low"]).tail(20).mean())
        threshold = max(avg_range * 0.12, 0.01)

        close_now = float(df["close"].iloc[-1])
        close_prev = float(df["close"].iloc[-lookback - 1])
        body = float(df["close"].iloc[-1]) - float(df["open"].iloc[-1])
        delta = close_now - close_prev

        if delta > threshold and body >= 0:
            return "UP"
        if delta < -threshold and body <= 0:
            return "DOWN"
        return "NEUTRAL"

    def _phase_state(self, df: pd.DataFrame) -> str:
        """
        EMA regime classifier.
        คืนค่า: IMPULSE_UP / IMPULSE_DOWN / PULLBACK_UP / PULLBACK_DOWN / UNKNOWN
        """
        if len(df) < 50:
            return "UNKNOWN"

        close = df["close"].astype(float)
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()

        last_close = float(close.iloc[-1])
        prev_close = float(close.iloc[-4])
        ema20_last = float(ema20.iloc[-1])
        ema50_last = float(ema50.iloc[-1])

        if last_close > ema20_last > ema50_last and last_close > prev_close:
            return "IMPULSE_UP"
        if last_close < ema20_last < ema50_last and last_close < prev_close:
            return "IMPULSE_DOWN"
        if last_close > ema50_last:
            return "PULLBACK_UP"
        if last_close < ema50_last:
            return "PULLBACK_DOWN"
        return "UNKNOWN"

    def _is_impulse(self, df: pd.DataFrame, delta_state: str, atr_value: float) -> bool:
        """
        ATR/range expansion gate.
        """
        if len(df) < 5 or delta_state not in ("UP", "DOWN", "DELTA_PLUS", "DELTA_MINUS"):
            return False

        body = abs(float(df["close"].iloc[-1]) - float(df["open"].iloc[-1]))
        recent_move = abs(float(df["close"].iloc[-1]) - float(df["close"].iloc[-4]))
        avg_range = float((df["high"] - df["low"]).tail(20).mean())
        threshold = max(float(atr_value) * 0.35, avg_range * 0.55, 0.01)

        return bool(body >= threshold or recent_move >= threshold)


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

    def _inside_zone(self, price: float, low: float, high: float) -> bool:
        return low > 0 and high > 0 and low <= price <= high

    def _prz_zone(self, df: pd.DataFrame) -> Tuple[float, float, float, float]:
        lookback = df.tail(80)
        high = float(lookback["high"].max())
        low = float(lookback["low"].min())
        diff = high - low

        if diff <= 0:
            return 0.0, 0.0, 0.0, 0.0

        support_low = low
        support_high = low + diff * 0.382
        resistance_low = high - diff * 0.382
        resistance_high = high
        return support_low, support_high, resistance_low, resistance_high

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
