from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import json
import os

import pandas as pd

from scenario_blueprint import ScenarioBlueprint

try:
    from harmonic_detector import run_harmonic, scan_forming_harmonic
except Exception as exc:
    run_harmonic = None
    scan_forming_harmonic = None
    HARMONIC_IMPORT_ERROR = str(exc)
else:
    HARMONIC_IMPORT_ERROR = ""


INTRADAY_HARMONIC_SCAN_ENABLED = os.getenv(
    "ALPHA_INTRADAY_HARMONIC_SCAN", "false"
).lower() in {"1", "true", "yes", "on"}
MARKET_MAP_DIR = os.getenv("ALPHA_MARKET_MAP_DIR", "data/market_maps")


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

        market_map = self._load_market_close_map(symbol)
        map_lot0 = market_map.get("lot0", {}) if market_map else {}
        map_kivanc = market_map.get("kivanc", {}) if market_map else {}

        htf_support_low, htf_support_high, htf_resistance_low, htf_resistance_high = self._prz_zone(df_1h)
        micro_support_low, micro_support_high, micro_resistance_low, micro_resistance_high = self._prz_zone(df_15m)

        selected_harmonic = self._select_harmonic_prz(
            df_4h=df_4h,
            df_1h=df_1h,
            current_price=current_price,
            symbol=symbol,
            market_map=market_map,
        )
        if selected_harmonic.get("found") and str(
            selected_harmonic.get("state", "NONE")
        ).upper() == "FORMING":
            reversal_direction = str(
                selected_harmonic.get("direction", "NONE") or "NONE"
            ).upper()
            projected_c = float(selected_harmonic.get("c", 0.0) or 0.0)
            projected_d = float(selected_harmonic.get("d_point", 0.0) or 0.0)
            harmonic_break_buffer = max(
                projected_d * 0.0015,
                abs(
                    float(selected_harmonic.get("prz_high", 0.0) or 0.0)
                    - float(selected_harmonic.get("prz_low", 0.0) or 0.0)
                ) * 0.10,
            )
            c_leg_broken = bool(
                projected_c > 0
                and (
                    (reversal_direction == "BUY" and current_price > projected_c + harmonic_break_buffer)
                    or (reversal_direction == "SELL" and current_price < projected_c - harmonic_break_buffer)
                )
            )
            prior_channel_broken = bool(
                (reversal_direction == "BUY" and current_price > prior_tunnel_upper + tunnel_tolerance)
                or (reversal_direction == "SELL" and current_price < prior_tunnel_lower - tunnel_tolerance)
            )
            if c_leg_broken or prior_channel_broken:
                selected_harmonic = {
                    **selected_harmonic,
                    "state": "INVALIDATED",
                    "tunnel_broken": True,
                }
        real_harmonic = bool(selected_harmonic.get("found"))

        harmonic_prz_low = float(selected_harmonic.get("prz_low", 0.0)) if real_harmonic else 0.0
        harmonic_prz_high = float(selected_harmonic.get("prz_high", 0.0)) if real_harmonic else 0.0
        harmonic_d_point = float(selected_harmonic.get("d_point", 0.0)) if real_harmonic else 0.0
        harmonic_x_point = float(selected_harmonic.get("x", 0.0)) if real_harmonic else 0.0
        harmonic_a_point = float(selected_harmonic.get("a", 0.0)) if real_harmonic else 0.0
        harmonic_b_point = float(selected_harmonic.get("b", 0.0)) if real_harmonic else 0.0
        harmonic_c_point = float(selected_harmonic.get("c", 0.0)) if real_harmonic else 0.0

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

        self._log_scan_state(
            symbol=symbol,
            current_price=current_price,
            trend_h4=trend_h4,
            trend_h1=trend_h1,
            market_mode=market_mode,
            m15_phase=m15_phase,
            h1_phase=h1_phase,
            h4_phase=h4_phase,
            watch_bias=watch_bias,
            trade_plan=trade_plan,
            prz_state=prz_state,
            micro_prz_broken=micro_prz_broken,
            micro_prz_reclaimed=micro_prz_reclaimed,
            selected_harmonic=selected_harmonic,
            market_map=market_map,
            map_lot0=map_lot0,
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
            market_map_date=str(market_map.get("map_date", "")) if market_map else "",
            market_map_source=str(market_map.get("source", "NONE")) if market_map else "NONE",
            lot0_price=round(float(map_lot0.get("price", 0.0) or 0.0), 3),
            lot0_side=str(map_lot0.get("side", "NONE")),
            lot0_source=str(map_lot0.get("source", "NONE")),
            lot0_timeframe=str(map_lot0.get("timeframe", "NONE")),
            kivanc_boundary_high=round(float(map_kivanc.get("boundary_high", 0.0) or 0.0), 3),
            kivanc_boundary_low=round(float(map_kivanc.get("boundary_low", 0.0) or 0.0), 3),
            kivanc_fibo_0618=round(float(map_kivanc.get("fibo_0618", 0.0) or 0.0), 3),
            kivanc_fibo_0786=round(float(map_kivanc.get("fibo_0786", 0.0) or 0.0), 3),
            kivanc_fibo_0886=round(float(map_kivanc.get("fibo_0886", 0.0) or 0.0), 3),
            harmonic_pattern=str(selected_harmonic.get("pattern", "")) if real_harmonic else "",
            harmonic_state=str(selected_harmonic.get("state", "NONE")) if real_harmonic else "NONE",
            harmonic_source_tf=str(selected_harmonic.get("source_tf", "NONE")) if real_harmonic else "NONE",
            harmonic_source=str(selected_harmonic.get("source", "NONE")) if real_harmonic else "NONE",
            harmonic_direction=str(selected_harmonic.get("direction", "NONE")) if real_harmonic else "NONE",
            harmonic_approach_direction=str(selected_harmonic.get("approach_direction", "NONE")) if real_harmonic else "NONE",
            harmonic_pattern_state=str(selected_harmonic.get("pattern_state", selected_harmonic.get("state", "NONE"))) if real_harmonic else "NONE",
            harmonic_is_real=bool(real_harmonic),
            harmonic_d_point=round(harmonic_d_point, 3) if real_harmonic else 0.0,
            harmonic_x_price=round(harmonic_x_point, 3) if real_harmonic else 0.0,
            harmonic_a_price=round(harmonic_a_point, 3) if real_harmonic else 0.0,
            harmonic_b_price=round(harmonic_b_point, 3) if real_harmonic else 0.0,
            harmonic_c_price=round(harmonic_c_point, 3) if real_harmonic else 0.0,
            harmonic_ratios={
                str(key): round(float(value), 6)
                for key, value in dict(selected_harmonic.get("ratios") or {}).items()
            } if real_harmonic else {},
            harmonic_tp1=round(float(selected_harmonic.get("tp1", 0.0)), 3) if real_harmonic else 0.0,
            harmonic_tp2=round(float(selected_harmonic.get("tp2", 0.0)), 3) if real_harmonic else 0.0,
            harmonic_tp3=round(float(selected_harmonic.get("tp3", 0.0)), 3) if real_harmonic else 0.0,
            harmonic_invalidation=round(float(selected_harmonic.get("invalidation", 0.0)), 3) if real_harmonic else 0.0,
            harmonic_projection_mode=str(selected_harmonic.get("projection_mode", "COMPLETED_XABCD")) if real_harmonic else "NONE",
            harmonic_execution_authority=bool(selected_harmonic.get("execution_authority", True)) if real_harmonic else False,
            harmonic_tunnel_broken=bool(selected_harmonic.get("tunnel_broken", False)) if real_harmonic else False,
            harmonic_selected_pattern=str(selected_harmonic.get("selected_pattern", selected_harmonic.get("pattern", ""))) if real_harmonic else "",
            harmonic_candidate_patterns=list(selected_harmonic.get("candidate_patterns") or []) if real_harmonic else [],
            harmonic_current_xad=round(float(selected_harmonic.get("current_xad", 0.0)), 6) if real_harmonic else 0.0,
            harmonic_current_bcd=round(float(selected_harmonic.get("current_bcd", 0.0)), 6) if real_harmonic else 0.0,
            harmonic_next_xad=round(float(selected_harmonic.get("next_xad", 0.0)), 6) if real_harmonic else 0.0,
            harmonic_prz_timeframe=str(selected_harmonic.get("source_tf", "NONE")) if real_harmonic else "NONE",
            harmonic_prz_source=str(selected_harmonic.get("source", "NONE")) if real_harmonic else "NONE",
            prz_current=round(harmonic_d_point, 3) if real_harmonic else None,
            prz_next=round(harmonic_prz_high, 3) if real_harmonic else None,
            atr_15m=round(atr_15m, 3),
            atr_1h=round(atr_1h, 3),
            # No reproducible pattern/session/timeframe sample is attached to
            # this scenario yet. Never publish a hard-coded win probability.
            win_rate_est=0.0,
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

    def _load_market_close_map(self, symbol: str) -> Dict[str, Any]:
        """Load latest market-close map; never fail the intraday scanner."""
        clean_symbol = str(symbol or "XAUUSD").replace("/", "")
        map_dir = Path(MARKET_MAP_DIR)
        if not map_dir.is_absolute():
            map_dir = Path.cwd() / map_dir

        try:
            files = sorted(map_dir.glob(f"{clean_symbol}_*.json"))
            if not files:
                return {}
            with files[-1].open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("source", "market_close_map")
            return data
        except Exception as exc:
            print(f"AlphaBuffalo market map load failed | symbol={clean_symbol} error={exc}", flush=True)
            return {}

    def _harmonic_from_market_map(self, market_map: Optional[Dict[str, Any]], current_price: float) -> Dict[str, Any]:
        if not market_map:
            return {"found": False}

        harmonic = market_map.get("harmonic_context") or market_map.get("harmonic") or {}
        if not harmonic or not bool(harmonic.get("found", harmonic.get("completed", False))):
            return {"found": False}

        prz_low = float(harmonic.get("prz_low", 0.0) or 0.0)
        prz_high = float(harmonic.get("prz_high", 0.0) or 0.0)
        d_point = float(harmonic.get("d_point", 0.0) or 0.0)
        if prz_low <= 0 or prz_high <= 0 or d_point <= 0:
            return {"found": False}

        in_prz = prz_low <= current_price <= prz_high
        distance = abs(float(current_price) - d_point)
        distance_pct = distance / d_point if d_point else 999.0
        direction = str(harmonic.get("direction", "NONE") or "NONE").upper()
        approach_direction = str(
            harmonic.get(
                "approach_direction",
                "SELL" if direction == "BUY" else "BUY" if direction == "SELL" else "NONE",
            )
            or "NONE"
        ).upper()
        raw_state = str(harmonic.get("state", "DISCOVERED") or "DISCOVERED").upper()
        projection_mode = str(harmonic.get("projection_mode", "COMPLETED_XABCD") or "COMPLETED_XABCD")
        is_forming_projection = projection_mode == "FORMING_XABC_TO_D" or raw_state == "FORMING"
        c_point = float(harmonic.get("c_point", harmonic.get("c", 0.0)) or 0.0)
        invalidation = float(harmonic.get("invalidation", 0.0) or 0.0)
        # The pattern may wick beyond its narrow detector PRZ before reclaim.
        # Give the D location a small XAU-relative buffer instead of declaring
        # invalidation on the first liquidity sweep through the boundary.
        invalidation_buffer = max(abs(prz_high - prz_low) * 2.0, d_point * 0.0015)
        d_invalidated = bool(
            (direction == "BUY" and invalidation > 0 and current_price < invalidation - invalidation_buffer)
            or (direction == "SELL" and invalidation > 0 and current_price > invalidation + invalidation_buffer)
        )
        c_break_buffer = max(d_point * 0.0015, abs(prz_high - prz_low) * 0.10)
        forming_tunnel_broken = bool(
            is_forming_projection
            and c_point > 0
            and (
                (direction == "BUY" and current_price > c_point + c_break_buffer)
                or (direction == "SELL" and current_price < c_point - c_break_buffer)
            )
        )
        invalidated = d_invalidated or forming_tunnel_broken
        if invalidated:
            state = "INVALIDATED"
        elif in_prz:
            state = "ACTIVE"
        elif distance_pct <= 0.005:
            state = "ARMED"
        elif is_forming_projection:
            state = "FORMING"
        else:
            state = "WAIT_LOCATION"

        return {
            "found": True,
            "pattern": str(harmonic.get("pattern", "")),
            "direction": direction,
            "approach_direction": approach_direction,
            "priority": int(harmonic.get("priority", 5) or 5),
            "reliability": str(harmonic.get("reliability", "MARKET_CLOSE")),
            "prz_low": prz_low,
            "prz_high": prz_high,
            "d_point": d_point,
            "x": float(harmonic.get("x_point", harmonic.get("x", 0.0)) or 0.0),
            "a": float(harmonic.get("a_point", harmonic.get("a", 0.0)) or 0.0),
            "b": float(harmonic.get("b_point", harmonic.get("b", 0.0)) or 0.0),
            "c": c_point,
            "ratios": dict(harmonic.get("ratios") or {}),
            "tp1": float(harmonic.get("tp1", 0.0) or 0.0),
            "tp2": float(harmonic.get("tp2", 0.0) or 0.0),
            "tp3": float(harmonic.get("tp3", 0.0) or 0.0),
            "invalidation": invalidation,
            "score": 0,
            "in_prz": in_prz,
            "distance": distance,
            "distance_pct": distance_pct,
            "source_tf": str(harmonic.get("timeframe", "4H")),
            "source": "market_close_map",
            "state": state,
            "pattern_state": raw_state,
            "projection_mode": projection_mode,
            "execution_authority": bool(harmonic.get("execution_authority", True)),
            "selected_pattern": str(harmonic.get("selected_pattern", harmonic.get("pattern", "")) or ""),
            "candidate_patterns": list(harmonic.get("candidate_patterns") or []),
            "current_xad": float(harmonic.get("current_xad", 0.0) or 0.0),
            "current_bcd": float(harmonic.get("current_bcd", 0.0) or 0.0),
            "next_xad": float(harmonic.get("next_xad", 0.0) or 0.0),
            "tunnel_broken": forming_tunnel_broken,
        }

    def _select_harmonic_prz(
        self,
        df_4h: pd.DataFrame,
        df_1h: pd.DataFrame,
        current_price: float,
        symbol: str = "XAUUSD",
        market_map: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Harmonic authority order:
        1) market-close map (Lot0/Kivanc/Harmonic framework)
        2) optional legacy intraday scan only when ALPHA_INTRADAY_HARMONIC_SCAN=true

        Harmonic remains WHAT/context only. It must not add score or open trades.
        """
        mapped = self._harmonic_from_market_map(market_map, current_price)
        if mapped.get("found"):
            return mapped

        # Predictive fallback: X/A/B/C are already confirmed, while D is
        # projected. This is non-repainting and is intentionally available
        # even when the overnight Newday JSON has not been written yet.
        forming_candidates = []
        for source_tf, source_df in (("1H", df_1h), ("4H", df_4h)):
            projected = self._scan_forming_harmonic_tf(source_df, source_tf)
            if projected.get("found"):
                forming_candidates.append(projected)
        if forming_candidates:
            state_rank = {"ACTIVE": 0, "ARMED": 1, "FORMING": 2, "PASSED": 9}
            tf_rank = {"1H": 0, "4H": 1}
            forming_candidates.sort(
                key=lambda item: (
                    state_rank.get(str(item.get("state", "NONE")).upper(), 5),
                    tf_rank.get(str(item.get("source_tf", "NONE")), 9),
                    int(item.get("priority", 9) or 9),
                )
            )
            return forming_candidates[0]

        if not INTRADAY_HARMONIC_SCAN_ENABLED:
            return {
                "found": False,
                "pattern": "",
                "state": "MARKET_CLOSE_MAP_MISSING",
                "source_tf": "NONE",
                "source": "market_close_map",
                "direction": "NONE",
                "prz_low": 0.0,
                "prz_high": 0.0,
                "d_point": 0.0,
                "score": 0,
            }

        candidates = []
        candidates.extend(self._scan_harmonic_tf(df_4h, current_price, "4H"))
        candidates.extend(self._scan_harmonic_tf(df_1h, current_price, "1H"))

        if not candidates:
            return {
                "found": False,
                "pattern": "",
                "state": "NONE",
                "source_tf": "NONE",
                "source": "NONE",
                "direction": "NONE",
                "prz_low": 0.0,
                "prz_high": 0.0,
                "d_point": 0.0,
                "score": 0,
            }

        tf_rank = {"4H": 0, "1H": 1}

        def sort_key(item: Dict[str, Any]):
            return (
                0 if item.get("in_prz") else 1,
                tf_rank.get(str(item.get("source_tf")), 9),
                int(item.get("priority", 9)),
                float(item.get("distance_pct", 999.0)),
                -int(item.get("score", 0)),
            )

        candidates.sort(key=sort_key)
        selected = candidates[0]

        if selected.get("in_prz"):
            selected["state"] = "ACTIVE"
        elif float(selected.get("distance_pct", 999.0)) <= 0.005:
            selected["state"] = "ARMED"
        else:
            selected["state"] = "WAIT_LOCATION"

        selected["found"] = True
        return selected

    def _scan_forming_harmonic_tf(
        self,
        df: pd.DataFrame,
        source_tf: str,
    ) -> Dict[str, Any]:
        if scan_forming_harmonic is None or df is None or df.empty:
            return {"found": False}
        try:
            projected = dict(scan_forming_harmonic(df) or {})
        except Exception as exc:
            print(
                "AlphaBuffalo forming harmonic scan failed | "
                f"tf={source_tf} error={exc}",
                flush=True,
            )
            return {"found": False}
        if not projected.get("found"):
            return {"found": False}
        projected["source_tf"] = source_tf
        projected.setdefault("source", "harmonic_detector.project_xabc")
        projected.setdefault("projection_mode", "FORMING_XABC_TO_D")
        projected.setdefault("pattern_state", "FORMING_XABC")
        projected.setdefault("candidate_patterns", list(projected.get("candidates") or []))
        projected.setdefault("invalidation", 0.0)
        projected.setdefault("tp1", 0.0)
        projected.setdefault("tp2", 0.0)
        projected.setdefault("tp3", 0.0)
        return projected

    def _scan_harmonic_tf(
        self,
        df: pd.DataFrame,
        current_price: float,
        source_tf: str,
    ) -> list[Dict[str, Any]]:
        if run_harmonic is None:
            return []

        try:
            zones = run_harmonic(df)
        except Exception as exc:
            print(
                "AlphaBuffalo harmonic scan failed | "
                f"tf={source_tf} error={exc}",
                flush=True,
            )
            return []

        out = []
        for z in zones or []:
            prz_low = float(getattr(z, "prz_low", 0.0) or 0.0)
            prz_high = float(getattr(z, "prz_high", 0.0) or 0.0)
            d_point = float(getattr(z, "d_point", 0.0) or 0.0)

            if prz_low <= 0 or prz_high <= 0 or d_point <= 0:
                continue

            in_prz = bool(prz_low <= current_price <= prz_high)
            distance = abs(float(current_price) - d_point)
            distance_pct = distance / d_point if d_point else 999.0

            out.append(
                {
                    "found": True,
                    "pattern": str(getattr(z, "pattern_name", "")),
                    "direction": str(getattr(z, "direction", "NONE")),
                    "approach_direction": "SELL" if str(getattr(z, "direction", "NONE")) == "BUY" else "BUY",
                    "priority": int(getattr(z, "priority", 9) or 9),
                    "reliability": str(getattr(z, "reliability", "UNKNOWN")),
                    "execution_authority": str(
                        getattr(z, "reliability", "") or ""
                    ).lower() != "context",
                    "prz_low": prz_low,
                    "prz_high": prz_high,
                    "d_point": d_point,
                    "x": float(getattr(z, "x_point", 0.0) or 0.0),
                    "a": float(getattr(z, "a_point", 0.0) or 0.0),
                    "b": float(getattr(z, "b_point", 0.0) or 0.0),
                    "c": float(getattr(z, "c_point", 0.0) or 0.0),
                    "ratios": dict(getattr(z, "ratios", {}) or {}),
                    "score": int(getattr(z, "confluence_score", 0) or 0),
                    "in_prz": in_prz,
                    "distance": distance,
                    "distance_pct": distance_pct,
                    "source_tf": source_tf,
                    "source": "harmonic_detector.run_harmonic",
                    "state": "ACTIVE" if in_prz else "DISCOVERED",
                    "pattern_state": "COMPLETED_AT_D",
                }
            )

        return out

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

    def _log_scan_state(
        self,
        *,
        symbol: str,
        current_price: float,
        trend_h4: str,
        trend_h1: str,
        market_mode: str,
        m15_phase: str,
        h1_phase: str,
        h4_phase: str,
        watch_bias: str,
        trade_plan: str,
        prz_state: str,
        micro_prz_broken: bool,
        micro_prz_reclaimed: bool,
        selected_harmonic: Dict[str, Any],
        market_map: Optional[Dict[str, Any]],
        map_lot0: Dict[str, Any],
    ) -> None:
        """Print compact scanner state only when state changes.

        ALPHA_SCANNER_STATE_LOG=compact (default): compact + dedupe
        ALPHA_SCANNER_STATE_LOG=full: print every scan in compact format
        ALPHA_SCANNER_STATE_LOG=off: no scanner-state log
        """
        mode = os.getenv("ALPHA_SCANNER_STATE_LOG", "compact").strip().lower()
        if mode in {"0", "false", "no", "off", "none"}:
            return

        harmonic = selected_harmonic or {}
        lot0_side = str(map_lot0.get("side", "NONE"))
        lot0_price = float(map_lot0.get("price", 0) or 0)
        map_date = str((market_map or {}).get("map_date", "NONE"))
        harmonic_label = str(harmonic.get("pattern", "NONE") or "NONE")
        harmonic_tf = str(harmonic.get("source_tf", harmonic.get("timeframe", "NONE")) or "NONE")
        harmonic_state = str(harmonic.get("state", "NONE") or "NONE")

        state_key = (
            symbol, trend_h4, trend_h1, market_mode,
            m15_phase, h1_phase, h4_phase,
            watch_bias, trade_plan, prz_state,
            bool(micro_prz_broken), bool(micro_prz_reclaimed),
            harmonic_label, harmonic_tf, harmonic_state,
            map_date, lot0_side, round(lot0_price, 3),
        )

        if mode != "full" and getattr(self, "_last_scan_state_key", None) == state_key:
            return
        self._last_scan_state_key = state_key

        print(
            "AlphaBuffalo scanner state | "
            f"symbol={symbol} price={round(current_price, 3)} "
            f"h4={trend_h4} h1={trend_h1} mode={market_mode} "
            f"m15={m15_phase} h1p={h1_phase} h4p={h4_phase} "
            f"watch={watch_bias} plan={trade_plan} prz={prz_state} "
            f"bos={int(bool(micro_prz_broken))} reclaim={int(bool(micro_prz_reclaimed))} "
            f"harmonic={harmonic_label}/{harmonic_tf}/{harmonic_state} "
            f"map={map_date} lot0={lot0_side}@{round(lot0_price, 3)}",
            flush=True,
        )

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
