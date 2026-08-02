#!/usr/bin/env python3
"""
SignalRouter — V4 location-first router.

Core rule:
- PRZ layers >= 2 + evidence >= 3 arms the directional V4 setup.
- Closed M15 HA flip, pinbar break, or closed M5 sniper reclaim triggers it.
- A confirmed H1 green permission dot on closed M15 may trigger BUY directly
  from remembered two-layer demand PRZ location.
- The EA adapter validates levels/RR once; VSA is evidence, not a second gate.
- CHoCH/BOS promotes V4 scalp to V5 journey; it is not required for V4 entry.
"""
from __future__ import annotations

import os
from typing import List

import pandas as pd

from session_clock import SessionClock
from engine_v4.final_gate import FinalGate
from engine_v4.buy_engine import BuySignalEngine
from engine_v4.sell_engine import SellSignalEngine
from signal_schema import SIGNAL, normalize_engine_candidate


class SignalRouter:
    def __init__(
        self,
        clock: SessionClock,
        gate: FinalGate,
        buy_engine: BuySignalEngine,
        sell_engine: SellSignalEngine,
    ):
        self.clock = clock
        self.gate = gate
        self.buy_engine = buy_engine
        self.sell_engine = sell_engine

    def process(
        self,
        df: pd.DataFrame,
        daily_dd_ok: bool = True,
        consec_loss_ok: bool = True,
        harmonic_context=None,
        require_harmonic: bool = False,
    ) -> List[dict]:
        if not isinstance(df.index, pd.DatetimeIndex):
            raise TypeError("DataFrame must have DatetimeIndex")
        if df.empty:
            return []

        # Scan recent closed bars. A setup can form on the BB/PRZ touch candle
        # and still be valid for execution on the next polling cycle.
        lookback_bars = max(1, int(os.getenv("ENGINE_V4_LOOKBACK_BARS", "6")))
        start_idx = max(0, len(df) - lookback_bars)

        signals: List[dict] = []
        latest_idx = len(df) - 1

        for idx in range(start_idx, len(df)):
            row = df.iloc[idx]
            ts = row.name
            if getattr(ts, "tzinfo", None) is None:
                ts = ts.tz_localize("UTC")
            session_state = self.clock.get(ts)
            age_bars = latest_idx - idx

            gate_buy = self.gate.evaluate(
                session_state,
                "BUY",
                df=df,
                idx=idx,
                daily_dd_ok=daily_dd_ok,
                consec_loss_ok=consec_loss_ok,
                harmonic_context=harmonic_context,
                require_harmonic=require_harmonic,
            )
            buy_raw = self.buy_engine.evaluate(df, idx, session_state, gate_buy)
            if buy_raw:
                buy = normalize_engine_candidate(buy_raw)
                self._enrich_signal(
                    buy,
                    idx,
                    age_bars,
                    row,
                    harmonic_context=harmonic_context,
                    require_harmonic=require_harmonic,
                )
                signals.append(buy)

            gate_sell = self.gate.evaluate(
                session_state,
                "SELL",
                df=df,
                idx=idx,
                daily_dd_ok=daily_dd_ok,
                consec_loss_ok=consec_loss_ok,
                harmonic_context=harmonic_context,
                require_harmonic=require_harmonic,
            )
            sell_raw = self.sell_engine.evaluate(df, idx, session_state, gate_sell)
            if sell_raw:
                sell = normalize_engine_candidate(sell_raw)
                self._enrich_signal(
                    sell,
                    idx,
                    age_bars,
                    row,
                    harmonic_context=harmonic_context,
                    require_harmonic=require_harmonic,
                )
                signals.append(sell)

        if not signals:
            return []

        return [max(signals, key=self._rank)]

    def _enrich_signal(
        self,
        sig: dict,
        idx: int,
        age_bars: int,
        row,
        *,
        harmonic_context=None,
        require_harmonic: bool = False,
    ) -> None:
        direction = str(sig.get("direction", "")).upper()
        setup_state = str(sig.get("setup_state") or f"{direction}_SETUP").upper()
        aligned_structure = bool(
            row.get("CHoCH_Bull", False) or row.get("Micro_BOS_Up", False)
        ) if direction == "BUY" else bool(
            row.get("CHoCH_Bear", False) or row.get("Micro_BOS_Down", False)
        )
        # A closed PA/HA/Sniper confirmation opens V4. Only an independently
        # aligned BOS/CHoCH may promote that existing position into V5.
        structure_confirmed = aligned_structure
        v4_state = f"V4_{direction}_PRZ_ENTRY_READY"
        v5_state = (
            f"V5_{direction}_CONTINUATION_CONFIRMED"
            if structure_confirmed
            else f"V5_{direction}_WAIT_BOS_CHOCH"
        )
        self._apply_target_route(
            sig,
            row,
            direction=direction,
            bos_confirmed=structure_confirmed,
            harmonic_context=harmonic_context,
        )

        sig["selected_idx"] = idx
        sig["selected_age_bars"] = age_bars
        sig["scenario_state"] = setup_state
        sig["journey_state"] = (
            f"V5_{direction}_JOURNEY"
            if structure_confirmed
            else "V4_SCALP_RANGE"
        )
        # Both engines are always observable for a selected PRZ signal. V4
        # owns the single entry command; V5 may only promote/manage that same
        # position after aligned BOS/CHoCH and must never create a second one.
        sig["v4_state"] = v4_state
        sig["v5_state"] = v5_state
        sig["engine_stages"] = {
            "v4": {
                "state": v4_state,
                "role": "PRZ_ENTRY",
                "ready": True,
                "command": "OPEN_IF_LEVELS_RR_RISK_PASS",
                "creates_new_order": True,
                "target": "SCALP_TP_BEFORE_BOS",
            },
            "v5": {
                "state": v5_state,
                "role": "CONTINUATION_PROMOTION",
                "ready": structure_confirmed,
                "command": (
                    "PROMOTE_EXISTING"
                    if structure_confirmed
                    else "WAIT_BOS_CHOCH"
                ),
                "creates_new_order": False,
                "target": (
                    sig.get("target_source")
                    if structure_confirmed
                    else "WAIT_NEXT_PRZ_TARGET"
                ),
            },
        }
        sig["order_policy"] = "V4_OPEN_ONCE_V5_MANAGE_EXISTING"
        sig["vsa_gate"] = (
            "BUY_PRESSURE" if direction == "BUY" else "SELL_PRESSURE"
        )
        sig["bos_confirmed"] = bool(structure_confirmed)
        bb_prz_confluence = bool(sig.get("bb_prz_confluence"))
        zone_confluence = bool(sig.get("zone_confluence"))
        sig["bb_prz_confluence"] = bb_prz_confluence
        sig["zone_confluence"] = bool(
            bb_prz_confluence or zone_confluence
        )
        sig["close_at_signal"] = float(row.get("close", 0.0) or 0.0)
        sig["harmonic_role"] = "POST_BOS_TP2_ONLY"

    @staticmethod
    def _price(value) -> float:
        try:
            price = float(value or 0.0)
        except (TypeError, ValueError):
            return 0.0
        return price if price > 0 else 0.0

    @classmethod
    def _apply_target_route(
        cls,
        sig: dict,
        row,
        *,
        direction: str,
        bos_confirmed: bool,
        harmonic_context=None,
    ) -> None:
        """Route V4 scalp TP and post-BOS V5 TP to the next opposite PRZ.

        Harmonic is accepted only as a TP2 price when its D point overlaps that
        next PRZ. It never changes entry permission or creates another order.
        """
        entry = cls._price(sig.get("entry_price") or sig.get("entry"))
        original_tp1 = cls._price(sig.get("tp1_price") or sig.get("tp1"))
        original_tp2 = cls._price(sig.get("tp2_price") or sig.get("tp"))
        if direction == "BUY":
            scalp_tp = (
                original_tp1
                if original_tp1 > entry
                else original_tp2
            )
            next_low = cls._price(row.get("Pine_PRZ_Resistance_Low"))
            next_high = cls._price(row.get("Pine_PRZ_Resistance_High"))
            next_prz_tp = next_low if next_low > scalp_tp else 0.0
        else:
            scalp_tp = (
                original_tp1
                if 0 < original_tp1 < entry
                else original_tp2
            )
            next_low = cls._price(row.get("Pine_PRZ_Support_Low"))
            next_high = cls._price(row.get("Pine_PRZ_Support_High"))
            next_prz_tp = next_high if 0 < next_high < scalp_tp else 0.0

        if scalp_tp <= 0:
            return

        context = harmonic_context if isinstance(harmonic_context, dict) else {}
        harmonic_d = cls._price(context.get("d_point"))
        harmonic_in_next_prz = bool(
            harmonic_d > 0
            and next_low > 0
            and next_high >= next_low
            and next_low <= harmonic_d <= next_high
        )
        harmonic_directional = bool(
            harmonic_d > scalp_tp
            if direction == "BUY"
            else 0 < harmonic_d < scalp_tp
        )
        use_harmonic = bool(
            bos_confirmed and harmonic_in_next_prz and harmonic_directional
        )

        continuation_tp = scalp_tp
        target_source = "V4_SCALP_CHECKPOINT"
        if bos_confirmed:
            target_source = "V5_WAIT_NEXT_PRZ_TARGET"
            if use_harmonic:
                continuation_tp = harmonic_d
                target_source = "HARMONIC_D_AT_NEXT_PRZ"
            elif next_prz_tp > 0:
                continuation_tp = next_prz_tp
                target_source = "NEXT_OPPOSITE_PRZ"
        has_runner_target = bool(
            bos_confirmed
            and continuation_tp > 0
            and abs(continuation_tp - scalp_tp) > 1e-9
        )

        sig["tp1_price"] = scalp_tp
        sig["tp2_price"] = continuation_tp
        sig["tp1"] = scalp_tp
        sig["tp"] = continuation_tp
        sig["v4_tp_price"] = scalp_tp
        sig["v5_tp_price"] = continuation_tp if has_runner_target else None
        sig["next_prz_low"] = next_low or None
        sig["next_prz_high"] = next_high or None
        sig["harmonic_target_price"] = harmonic_d or None
        sig["harmonic_target_eligible"] = use_harmonic
        sig["harmonic_role"] = "POST_BOS_TP2_ONLY"
        sig["target_source"] = target_source
        sig["tp_mode"] = "TP1_TP2" if has_runner_target else "SINGLE_TP"
        sig["target_contract"] = {
            "v4": "SCALP_TP_BEFORE_BOS",
            "promotion": "ALIGNED_BOS_OR_CHOCH",
            "v5": "NEXT_OPPOSITE_PRZ",
            "harmonic": "D_POINT_ONLY_IF_OVERLAPS_NEXT_PRZ",
        }

        sl = cls._price(sig.get("sl_price") or sig.get("sl"))
        risk = entry - sl if direction == "BUY" else sl - entry
        reward = (
            scalp_tp - entry
            if direction == "BUY"
            else entry - scalp_tp
        )
        if risk > 0 and reward > 0:
            sig["entry_rr"] = reward / risk
            sig["rr_ok"] = sig["entry_rr"] >= float(
                os.getenv("TRADE_MIN_RR", "1.5")
            )

    def _rank(self, sig: dict) -> tuple:
        # Location first. Do not add SELL/BUY bias here.
        contract_valid = 1 if sig.get("status") == SIGNAL else 0
        confluence = 1 if sig.get("bb_prz_confluence") or sig.get("zone_confluence") else 0
        pine_valid = 1 if sig.get("pine_valid") else 0
        cf_ready = 1 if str(sig.get("setup_state", "")).upper().endswith("CF_READY") else 0
        quality = int(sig.get("v5_quality_score", 0) or 0)
        rr = float(sig.get("entry_rr", 0.0) or 0.0)
        age_score = -int(sig.get("selected_age_bars", 99) or 99)
        return (contract_valid, confluence, pine_valid, cf_ready, quality, rr, age_score)
