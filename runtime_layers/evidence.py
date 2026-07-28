"""Causal PRZ evidence, M5 sniper sweep memory, and HOLD diagnostics."""
from __future__ import annotations

import os
from typing import Dict, Tuple

import pandas as pd

from runtime_layers.common import (
    _blueprint_float,
    _blueprint_zone_overlap,
    _engine_v4_scalar,
    _iso_timestamp,
    _positive_levels,
    _safe_float,
    _timed_ohlc_frame,
    _v4_bool_series,
)
from runtime_layers.harmonic import _harmonic_gate_context

def _m5_sniper_sweep_overlay(
    df_15m: pd.DataFrame,
    df_5m: pd.DataFrame | None,
    df_1h: pd.DataFrame | None,
    blueprint,
) -> pd.DataFrame:
    """
    Add confirmed M5 wick-sweep evidence to the matching M15 setup bar.

    A sniper event is a confirmed entry trigger once the PRZ setup is ARMED:
    - completed M5 range >= configured XAU dollar threshold;
    - prior three-bar extreme is swept and reclaimed;
    - the wick overlaps a confirmed Kivanc level;
    - the same wick touches/reclaims M15 or H1 Bollinger edge;
    - the wick overlaps a confirmed M15/H1/PRZ-A/PRZ-B location.

    The final M5 and H1 provider rows are excluded because they may still be
    forming. M15/H1 reference values are shifted so the sweep cannot read a
    candle that had not closed when it occurred.
    """
    out = df_15m.copy()
    defaults = {
        "V4_Buy_M5_Sniper_Evidence": False,
        "V4_Sell_M5_Sniper_Evidence": False,
        "V4_Buy_M5_Sniper_Move": 0.0,
        "V4_Sell_M5_Sniper_Move": 0.0,
        "V4_Buy_M5_Sniper_Kivanc": 0.0,
        "V4_Sell_M5_Sniper_Kivanc": 0.0,
        "V4_Buy_M5_Sniper_BB": 0.0,
        "V4_Sell_M5_Sniper_BB": 0.0,
        "V4_Buy_M5_Sniper_BB_TF": "NONE",
        "V4_Sell_M5_Sniper_BB_TF": "NONE",
        "V4_Buy_M5_Sniper_Time": "",
        "V4_Sell_M5_Sniper_Time": "",
    }
    for field, value in defaults.items():
        out[field] = value

    m5 = _timed_ohlc_frame(df_5m)
    if len(m5) < 5 or not isinstance(out.index, pd.DatetimeIndex):
        return out
    # TwelveData includes the updating provider candle as its final row.
    m5 = m5.iloc[:-1].copy()
    if len(m5) < 4:
        return out

    minimum_move = max(
        0.1,
        float(os.getenv("ENGINE_V4_SNIPER_M5_MIN_MOVE", "10.0")),
    )
    level_tolerance = max(
        0.0,
        float(os.getenv("ENGINE_V4_SNIPER_LEVEL_TOLERANCE", "1.5")),
    )
    sweep_lookback = max(
        1,
        int(os.getenv("ENGINE_V4_SNIPER_SWEEP_LOOKBACK", "3")),
    )

    m15_index = out.index
    if m15_index.tz is None:
        m15_index = m15_index.tz_localize("UTC")
    else:
        m15_index = m15_index.tz_convert("UTC")

    # An H1 candle stamped 14:00 is only usable after its 15:00 close.
    h1 = _timed_ohlc_frame(df_1h)
    h1_lower = pd.Series(dtype=float)
    h1_upper = pd.Series(dtype=float)
    if len(h1) >= 3:
        h1 = h1.iloc[:-1].copy()
        if "BB_Lower" in h1 and "BB_Upper" in h1:
            raw_h1_lower = pd.to_numeric(h1["BB_Lower"], errors="coerce")
            raw_h1_upper = pd.to_numeric(h1["BB_Upper"], errors="coerce")
        else:
            h1_mid = h1["close"].rolling(20, min_periods=20).mean()
            h1_std = h1["close"].rolling(20, min_periods=20).std()
            raw_h1_lower = h1_mid - (2.0 * h1_std)
            raw_h1_upper = h1_mid + (2.0 * h1_std)
        h1_lower = raw_h1_lower.copy()
        h1_upper = raw_h1_upper.copy()
        h1_lower.index = h1_lower.index + pd.Timedelta(hours=1)
        h1_upper.index = h1_upper.index + pd.Timedelta(hours=1)

    prior_low = m5["low"].rolling(sweep_lookback, min_periods=sweep_lookback).min().shift(1)
    prior_high = m5["high"].rolling(sweep_lookback, min_periods=sweep_lookback).max().shift(1)
    candle_range = m5["high"] - m5["low"]

    def _zone_overlap(low: float, high: float, zone_low: float, zone_high: float) -> bool:
        if zone_low <= 0 or zone_high <= 0:
            return False
        if zone_low > zone_high:
            zone_low, zone_high = zone_high, zone_low
        return low <= zone_high + level_tolerance and high >= zone_low - level_tolerance

    def _asof_value(series: pd.Series, timestamp: pd.Timestamp) -> float:
        if series.empty:
            return 0.0
        eligible = series.loc[series.index <= timestamp].dropna()
        return _safe_float(eligible.iloc[-1]) if not eligible.empty else 0.0

    market_levels = _positive_levels(
        _blueprint_float(blueprint, "kivanc_boundary_low"),
        _blueprint_float(blueprint, "kivanc_boundary_high"),
        _blueprint_float(blueprint, "kivanc_fibo_0618"),
        _blueprint_float(blueprint, "kivanc_fibo_0786"),
        _blueprint_float(blueprint, "kivanc_fibo_0886"),
    )
    blueprint_buy_zones = [
        (
            _blueprint_float(blueprint, low_field),
            _blueprint_float(blueprint, high_field),
        )
        for low_field, high_field in (
            ("htf_prz_support_low", "htf_prz_support_high"),
            ("prz_a_support_low", "prz_a_support_high"),
            ("prz_b_support_low", "prz_b_support_high"),
        )
    ]
    blueprint_sell_zones = [
        (
            _blueprint_float(blueprint, low_field),
            _blueprint_float(blueprint, high_field),
        )
        for low_field, high_field in (
            ("htf_prz_resistance_low", "htf_prz_resistance_high"),
            ("prz_a_resistance_low", "prz_a_resistance_high"),
            ("prz_b_resistance_low", "prz_b_resistance_high"),
        )
    ]

    # Only recent completed M5 bars can affect the live four-M15-bar memory.
    for m5_position in range(max(sweep_lookback, len(m5) - 24), len(m5)):
        timestamp = pd.Timestamp(m5.index[m5_position])
        m15_position = int(m15_index.searchsorted(timestamp, side="right") - 1)
        if m15_position < 0 or m15_position >= len(out):
            continue
        reference_position = max(0, m15_position - 1)
        reference = out.iloc[reference_position]
        bar = m5.iloc[m5_position]
        low = _safe_float(bar["low"])
        high = _safe_float(bar["high"])
        open_price = _safe_float(bar["open"])
        close = _safe_float(bar["close"])
        move = _safe_float(candle_range.iloc[m5_position])
        if move < minimum_move or low <= 0 or high <= 0:
            continue

        m15_buy_zones = blueprint_buy_zones + [
            (
                _safe_float(reference.get("Pine_PRZ_Support_Low")),
                _safe_float(reference.get("Pine_PRZ_Support_High")),
            )
        ]
        m15_sell_zones = blueprint_sell_zones + [
            (
                _safe_float(reference.get("Pine_PRZ_Resistance_Low")),
                _safe_float(reference.get("Pine_PRZ_Resistance_High")),
            )
        ]
        buy_location = any(
            _zone_overlap(low, high, zone_low, zone_high)
            for zone_low, zone_high in m15_buy_zones
        )
        sell_location = any(
            _zone_overlap(low, high, zone_low, zone_high)
            for zone_low, zone_high in m15_sell_zones
        )

        buy_levels = _positive_levels(
            reference.get("Fib_0618"),
            reference.get("Fib_072"),
            reference.get("Fib_0786"),
            reference.get("Fib_0886"),
            reference.get("Fib_100"),
            *market_levels,
        )
        sell_levels = _positive_levels(
            reference.get("Fib_R_0618"),
            reference.get("Fib_R_072"),
            reference.get("Fib_R_0786"),
            reference.get("Fib_R_0886"),
            reference.get("Fib_R_100"),
            *market_levels,
        )
        lower_body = min(open_price, close)
        upper_body = max(open_price, close)
        touched_buy_levels = [
            level
            for level in buy_levels
            if low <= level + level_tolerance
            and lower_body >= level - level_tolerance
        ]
        touched_sell_levels = [
            level
            for level in sell_levels
            if high >= level - level_tolerance
            and upper_body <= level + level_tolerance
        ]

        m15_lower = _safe_float(reference.get("BB_Lower"))
        m15_upper = _safe_float(reference.get("BB_Upper"))
        h1_bb_lower = _asof_value(h1_lower, timestamp)
        h1_bb_upper = _asof_value(h1_upper, timestamp)
        buy_bb_candidates = [
            ("M15", m15_lower),
            ("H1", h1_bb_lower),
        ]
        sell_bb_candidates = [
            ("M15", m15_upper),
            ("H1", h1_bb_upper),
        ]
        touched_buy_bb = [
            (timeframe, level)
            for timeframe, level in buy_bb_candidates
            if level > 0
            and low <= level + level_tolerance
            and close > level
        ]
        touched_sell_bb = [
            (timeframe, level)
            for timeframe, level in sell_bb_candidates
            if level > 0
            and high >= level - level_tolerance
            and close < level
        ]

        prior_low_value = _safe_float(prior_low.iloc[m5_position])
        prior_high_value = _safe_float(prior_high.iloc[m5_position])
        buy_kivanc_level = (
            min(touched_buy_levels, key=lambda level: abs(low - level))
            if touched_buy_levels
            else 0.0
        )
        sell_kivanc_level = (
            min(touched_sell_levels, key=lambda level: abs(high - level))
            if touched_sell_levels
            else 0.0
        )
        buy_sweep = bool(
            buy_location
            and prior_low_value > 0
            and low < prior_low_value
            and close > prior_low_value
            and touched_buy_levels
            and touched_buy_bb
            and close > buy_kivanc_level
        )
        sell_sweep = bool(
            sell_location
            and prior_high_value > 0
            and high > prior_high_value
            and close < prior_high_value
            and touched_sell_levels
            and touched_sell_bb
            and close < sell_kivanc_level
        )

        row_index = out.index[m15_position]
        if buy_sweep:
            bb_tf, bb_level = min(
                touched_buy_bb,
                key=lambda item: abs(low - item[1]),
            )
            out.at[row_index, "V4_Buy_M5_Sniper_Evidence"] = True
            out.at[row_index, "V4_Buy_M5_Sniper_Move"] = max(
                _safe_float(out.at[row_index, "V4_Buy_M5_Sniper_Move"]),
                move,
            )
            out.at[row_index, "V4_Buy_M5_Sniper_Kivanc"] = buy_kivanc_level
            out.at[row_index, "V4_Buy_M5_Sniper_BB"] = bb_level
            out.at[row_index, "V4_Buy_M5_Sniper_BB_TF"] = bb_tf
            out.at[row_index, "V4_Buy_M5_Sniper_Time"] = timestamp.isoformat()
        if sell_sweep:
            bb_tf, bb_level = min(
                touched_sell_bb,
                key=lambda item: abs(high - item[1]),
            )
            out.at[row_index, "V4_Sell_M5_Sniper_Evidence"] = True
            out.at[row_index, "V4_Sell_M5_Sniper_Move"] = max(
                _safe_float(out.at[row_index, "V4_Sell_M5_Sniper_Move"]),
                move,
            )
            out.at[row_index, "V4_Sell_M5_Sniper_Kivanc"] = sell_kivanc_level
            out.at[row_index, "V4_Sell_M5_Sniper_BB"] = bb_level
            out.at[row_index, "V4_Sell_M5_Sniper_BB_TF"] = bb_tf
            out.at[row_index, "V4_Sell_M5_Sniper_Time"] = timestamp.isoformat()

    return out

def _v4_location_evidence_memory(
    df: pd.DataFrame,
    *,
    touch: pd.Series,
    reset: pd.Series,
    component_weights: Dict[str, int],
    lock_bars: int,
    wall_side: str,
) -> Tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    """
    Latch PRZ location/evidence for closed M15 bars without looking ahead.

    A new touch starts or refreshes the four-bar window. Evidence observed
    after that touch is retained until expiry. An opposite BOS/CHoCH clears
    the state before an entry can be emitted.
    """
    limit = max(1, int(lock_bars))
    active_values = []
    score_values = []
    age_values = []
    wall_values = []
    active = False
    age = -1
    seen = {field: False for field in component_weights}
    wall = 0.0

    for position in range(len(df)):
        if bool(reset.iloc[position]):
            active = False
            age = -1
            wall = 0.0
            seen = {field: False for field in component_weights}
        else:
            touched = bool(touch.iloc[position])
            if touched:
                if not active:
                    seen = {field: False for field in component_weights}
                    wall = 0.0
                active = True
                age = 0
                candle_wall = _safe_float(
                    df["low"].iloc[position]
                    if wall_side == "LOW"
                    else df["high"].iloc[position]
                )
                if candle_wall > 0:
                    if wall <= 0:
                        wall = candle_wall
                    elif wall_side == "LOW":
                        wall = min(wall, candle_wall)
                    else:
                        wall = max(wall, candle_wall)
            elif active:
                age += 1

            if active and age >= limit:
                active = False
                age = -1
                wall = 0.0
                seen = {field: False for field in component_weights}

            if active:
                for field in component_weights:
                    if field in df and bool(
                        df[field].fillna(False).astype(bool).iloc[position]
                    ):
                        seen[field] = True

        if active:
            # Location qualification is a separate prerequisite. The score
            # contains confirmation evidence only, preventing PRZ from being
            # counted once as location and again as evidence.
            evidence_score = sum(
                weight
                for field, weight in component_weights.items()
                if seen.get(field, False)
            )
        else:
            evidence_score = 0

        active_values.append(active)
        score_values.append(evidence_score)
        age_values.append(age)
        wall_values.append(wall)

    return (
        pd.Series(active_values, index=df.index, dtype=bool),
        pd.Series(score_values, index=df.index, dtype=int),
        pd.Series(age_values, index=df.index, dtype=int),
        pd.Series(wall_values, index=df.index, dtype=float),
    )

def _overlay_blueprint_prz_memory(
    df: pd.DataFrame,
    blueprint,
    *,
    lock_bars: int = 4,
    df_5m: pd.DataFrame | None = None,
    df_1h: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Connect scanner H1/forecast PRZ layers to the executable V4 dataframe.

    Harmonic and tunnel remain guidance/owner context. They are deliberately
    not execution prerequisites and cannot create an order on their own.
    """
    out = df.copy()

    out["In_H1_PRZ_Support"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "htf_prz_support_low",
        "htf_prz_support_high",
    )
    out["In_H1_PRZ_Resistance"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "htf_prz_resistance_low",
        "htf_prz_resistance_high",
    )
    out["In_PRZ_A_Support"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "prz_a_support_low",
        "prz_a_support_high",
    )
    out["In_PRZ_A_Resistance"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "prz_a_resistance_low",
        "prz_a_resistance_high",
    )
    out["In_PRZ_B_Support"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "prz_b_support_low",
        "prz_b_support_high",
    )
    out["In_PRZ_B_Resistance"] = _blueprint_zone_overlap(
        out,
        blueprint,
        "prz_b_resistance_low",
        "prz_b_resistance_high",
    )

    buy_layers = (
        _v4_bool_series(out, "Deep_Buy_PRZ_Context").astype(int)
        + _v4_bool_series(out, "In_H1_PRZ_Support").astype(int)
        + _v4_bool_series(out, "In_PRZ_A_Support").astype(int)
        + _v4_bool_series(out, "In_PRZ_B_Support").astype(int)
    )
    sell_layers = (
        _v4_bool_series(out, "Deep_Sell_PRZ_Context").astype(int)
        + _v4_bool_series(out, "In_H1_PRZ_Resistance").astype(int)
        + _v4_bool_series(out, "In_PRZ_A_Resistance").astype(int)
        + _v4_bool_series(out, "In_PRZ_B_Resistance").astype(int)
    )
    out["V4_Demand_PRZ_Layer_Count"] = buy_layers
    out["V4_Supply_PRZ_Layer_Count"] = sell_layers
    out["V4_Demand_PRZ_Touch"] = buy_layers > 0
    out["V4_Supply_PRZ_Touch"] = sell_layers > 0
    out = _m5_sniper_sweep_overlay(out, df_5m, df_1h, blueprint)

    out["V4_Demand_PRZ_Qualified"] = buy_layers >= 2
    out["V4_Supply_PRZ_Qualified"] = sell_layers >= 2

    # Evidence can substitute for other evidence. PRZ qualification is kept
    # separate from evidence so "layers >= 2" and "evidence >= 3" cannot be
    # satisfied by counting the same location twice. M5 sniper is worth three
    # points because it already confirms sweep/reclaim + Kivanc + BB.
    buy_components = {
        "V4_Buy_Sweep_Evidence": 2,
        "V4_Buy_Pinbar_Evidence": 2,
        "Near_BB_Lower": 1,
        "VSA_Buy_Wins": 1,
        "Bull_OB": 1,
        "In_Session_Kivanc_Buy_Zone": 1,
        "V4_Buy_M5_Sniper_Evidence": 3,
    }
    sell_components = {
        "V4_Sell_Sweep_Evidence": 2,
        "V4_Sell_Pinbar_Evidence": 2,
        "Near_BB_Upper": 1,
        "VSA_Sell_Wins": 1,
        "Bear_OB": 1,
        "In_Session_Kivanc_Sell_Zone": 1,
        "V4_Sell_M5_Sniper_Evidence": 3,
    }
    out["V4_Buy_Sweep_Evidence"] = (
        _v4_bool_series(out, "Bull_Sweep")
        | _v4_bool_series(out, "Deep_Buy_Reclaim_Trigger")
    )
    out["V4_Sell_Sweep_Evidence"] = (
        _v4_bool_series(out, "Bear_Sweep")
        | _v4_bool_series(out, "Deep_Sell_Reclaim_Trigger")
    )
    out["V4_Buy_Pinbar_Evidence"] = (
        _v4_bool_series(out, "Bullish_Pinbar")
        | _v4_bool_series(out, "Zone_Buy_Pinbar_Trigger")
    )
    out["V4_Sell_Pinbar_Evidence"] = (
        _v4_bool_series(out, "Bearish_Pinbar")
        | _v4_bool_series(out, "Zone_Sell_Pinbar_Trigger")
    )
    out["V4_Buy_PRZ_Overlap_Evidence"] = buy_layers >= 2
    out["V4_Sell_PRZ_Overlap_Evidence"] = sell_layers >= 2

    (
        out["V4_Buy_Location_Memory"],
        out["V4_Buy_Evidence_Score"],
        out["V4_Buy_Location_Age_Bars"],
        out["V4_Buy_Location_Wall"],
    ) = _v4_location_evidence_memory(
        out,
        touch=out["V4_Demand_PRZ_Qualified"],
        reset=(
            _v4_bool_series(out, "CHoCH_Bear")
            | _v4_bool_series(out, "Micro_BOS_Down")
        ),
        component_weights=buy_components,
        lock_bars=lock_bars,
        wall_side="LOW",
    )
    (
        out["V4_Sell_Location_Memory"],
        out["V4_Sell_Evidence_Score"],
        out["V4_Sell_Location_Age_Bars"],
        out["V4_Sell_Location_Wall"],
    ) = _v4_location_evidence_memory(
        out,
        touch=out["V4_Supply_PRZ_Qualified"],
        reset=(
            _v4_bool_series(out, "CHoCH_Bull")
            | _v4_bool_series(out, "Micro_BOS_Up")
        ),
        component_weights=sell_components,
        lock_bars=lock_bars,
        wall_side="HIGH",
    )

    evidence_min = max(1, int(os.getenv("ENGINE_V4_EVIDENCE_MIN", "3")))
    out["V4_Buy_Armed"] = (
        out["V4_Buy_Location_Memory"]
        & (out["V4_Buy_Evidence_Score"] >= evidence_min)
    )
    out["V4_Sell_Armed"] = (
        out["V4_Sell_Location_Memory"]
        & (out["V4_Sell_Evidence_Score"] >= evidence_min)
    )

    if isinstance(out.index, pd.DatetimeIndex):
        timestamps = out.index
        if timestamps.tz is None:
            timestamps = timestamps.tz_localize("UTC")
        else:
            timestamps = timestamps.tz_convert("UTC")
        out["V4_M15_Bar_Closed"] = (
            timestamps + pd.Timedelta(minutes=15)
            <= pd.Timestamp.now(tz="UTC")
        )
    else:
        out["V4_M15_Bar_Closed"] = False

    out["V4_Buy_HA_Trigger"] = (
        out["V4_Buy_Armed"]
        & out["V4_M15_Bar_Closed"]
        & _v4_bool_series(out, "HA_Bull_Reversal")
    )
    out["V4_Sell_HA_Trigger"] = (
        out["V4_Sell_Armed"]
        & out["V4_M15_Bar_Closed"]
        & _v4_bool_series(out, "HA_Bear_Reversal")
    )
    out["V4_Buy_Pinbar_Trigger"] = (
        out["V4_Buy_Armed"]
        & _v4_bool_series(out, "Zone_Buy_Pinbar_Trigger")
    )
    out["V4_Sell_Pinbar_Trigger"] = (
        out["V4_Sell_Armed"]
        & _v4_bool_series(out, "Zone_Sell_Pinbar_Trigger")
    )
    out["V4_Buy_Sniper_Trigger"] = (
        out["V4_Buy_Armed"]
        & _v4_bool_series(out, "V4_Buy_M5_Sniper_Evidence")
    )
    out["V4_Sell_Sniper_Trigger"] = (
        out["V4_Sell_Armed"]
        & _v4_bool_series(out, "V4_Sell_M5_Sniper_Evidence")
    )

    out["V4_Buy_Memory_Trigger"] = (
        out["V4_Buy_HA_Trigger"]
        | out["V4_Buy_Pinbar_Trigger"]
        | out["V4_Buy_Sniper_Trigger"]
    )
    out["V4_Sell_Memory_Trigger"] = (
        out["V4_Sell_HA_Trigger"]
        | out["V4_Sell_Pinbar_Trigger"]
        | out["V4_Sell_Sniper_Trigger"]
    )

    out["V4_Buy_Trigger_Source"] = "NONE"
    out.loc[out["V4_Buy_HA_Trigger"], "V4_Buy_Trigger_Source"] = "M15_HA_BULL_FLIP"
    out.loc[out["V4_Buy_Pinbar_Trigger"], "V4_Buy_Trigger_Source"] = "BULL_PINBAR_HIGH_BREAK"
    out.loc[out["V4_Buy_Sniper_Trigger"], "V4_Buy_Trigger_Source"] = "M5_SNIPER_RECLAIM"
    out["V4_Sell_Trigger_Source"] = "NONE"
    out.loc[out["V4_Sell_HA_Trigger"], "V4_Sell_Trigger_Source"] = "M15_HA_BEAR_FLIP"
    out.loc[out["V4_Sell_Pinbar_Trigger"], "V4_Sell_Trigger_Source"] = "BEAR_PINBAR_LOW_BREAK"
    out.loc[out["V4_Sell_Sniper_Trigger"], "V4_Sell_Trigger_Source"] = "M5_SNIPER_RECLAIM"

    # Production V4 uses one canonical entry contract. Older setup flags from
    # add_indicators() remain observable but cannot bypass ARMED + OR trigger.
    out["V4_Buy_Entry_Zone"] = out["V4_Buy_Armed"]
    out["V4_Sell_Entry_Zone"] = out["V4_Sell_Armed"]
    out["V4_Buy_Setup"] = out["V4_Buy_Memory_Trigger"]
    out["V4_Sell_Setup"] = out["V4_Sell_Memory_Trigger"]
    out["V4_Block_Sell_At_Lower"] = (
        _v4_bool_series(out, "V4_Block_Sell_At_Lower")
        | out["V4_Buy_Armed"]
    )
    out["V4_Block_Buy_At_Upper"] = (
        _v4_bool_series(out, "V4_Block_Buy_At_Upper")
        | out["V4_Sell_Armed"]
    )
    return out

def _engine_v4_wait_diagnostics(
    df: pd.DataFrame,
    blueprint=None,
    *,
    lookback_bars: int = 6,
) -> Dict:
    """
    Explain a V4 HOLD without relaxing the entry gate.

    A wick/close may leave a PRZ before the next cloud poll.  Retaining six
    closed M15 bars makes that location observable while HA/PA/VSA confirmation
    still remains mandatory for an executable order.
    """
    if df is None or getattr(df, "empty", True):
        return {
            "status": "NO_DATA",
            "v4_selected": False,
            "recent_prz_touch": False,
        }

    tail = df.tail(max(1, int(lookback_bars)))
    last = tail.iloc[-1]

    def _count(field: str) -> int:
        if field not in tail:
            return 0
        try:
            return int(tail[field].fillna(False).astype(bool).sum())
        except Exception:
            return 0

    def _any(*fields: str) -> bool:
        return any(_count(field) > 0 for field in fields)

    def _max_int(field: str) -> int:
        if field not in tail:
            return 0
        try:
            return int(
                pd.to_numeric(tail[field], errors="coerce").fillna(0).max()
            )
        except Exception:
            return 0

    def _latest_time(*fields: str) -> str:
        mask = pd.Series(False, index=tail.index)
        for field in fields:
            if field in tail:
                try:
                    mask = mask | tail[field].fillna(False).astype(bool)
                except Exception:
                    continue
        matches = tail.index[mask]
        return _iso_timestamp(matches[-1]) if len(matches) else ""

    def _latest_event(field: str) -> pd.Series | None:
        if field not in tail:
            return None
        try:
            matches = tail.loc[tail[field].fillna(False).astype(bool)]
        except Exception:
            return None
        return matches.iloc[-1] if not matches.empty else None

    def _latest_text(field: str, default: str = "NONE") -> str:
        if field not in tail:
            return default
        for value in reversed(tail[field].tolist()):
            normalized = str(value or default).upper()
            if normalized not in {"", "NONE", "NAN"}:
                return normalized
        return default

    buy_touch = _any(
        "V4_Demand_PRZ_Touch",
        "V4_Buy_Location_Memory",
        "Deep_Buy_PRZ_Context",
        "In_Pine_PRZ_Support",
        "In_H1_PRZ_Support",
    )
    sell_touch = _any(
        "V4_Supply_PRZ_Touch",
        "V4_Sell_Location_Memory",
        "Deep_Sell_PRZ_Context",
        "In_Pine_PRZ_Resistance",
        "In_H1_PRZ_Resistance",
    )
    buy_touch_time = _latest_time(
        "V4_Demand_PRZ_Touch",
        "Deep_Buy_PRZ_Context",
        "In_Pine_PRZ_Support",
        "In_H1_PRZ_Support",
    )
    sell_touch_time = _latest_time(
        "V4_Supply_PRZ_Touch",
        "Deep_Sell_PRZ_Context",
        "In_Pine_PRZ_Resistance",
        "In_H1_PRZ_Resistance",
    )
    buy_reset_time = _latest_time("CHoCH_Bear", "Micro_BOS_Down")
    sell_reset_time = _latest_time("CHoCH_Bull", "Micro_BOS_Up")

    def _not_before(left: str, right: str) -> bool:
        if not left or not right:
            return False
        try:
            return pd.Timestamp(left) >= pd.Timestamp(right)
        except Exception:
            return False

    buy_structure_reset = bool(
        buy_touch
        and not bool(last.get("V4_Buy_Location_Memory", False))
        and _not_before(buy_reset_time, buy_touch_time)
    )
    sell_structure_reset = bool(
        sell_touch
        and not bool(last.get("V4_Sell_Location_Memory", False))
        and _not_before(sell_reset_time, sell_touch_time)
    )

    context_direction = "NONE"
    if buy_touch and not sell_touch:
        context_direction = "BUY"
    elif sell_touch and not buy_touch:
        context_direction = "SELL"
    elif buy_touch and sell_touch:
        try:
            context_direction = (
                "BUY"
                if pd.Timestamp(buy_touch_time) >= pd.Timestamp(sell_touch_time)
                else "SELL"
            )
        except Exception:
            context_direction = "NONE"

    recent_kivanc_state = "OUTSIDE"
    if "Kivanc_Scenario_State" in tail:
        for value in reversed(tail["Kivanc_Scenario_State"].tolist()):
            normalized = str(value or "OUTSIDE").upper()
            if normalized != "OUTSIDE":
                recent_kivanc_state = normalized
                break

    buy_sniper_row = _latest_event("V4_Buy_M5_Sniper_Evidence")
    sell_sniper_row = _latest_event("V4_Sell_M5_Sniper_Evidence")
    buy_sniper_armed = buy_sniper_row is not None
    sell_sniper_armed = sell_sniper_row is not None

    buy_evidence_score = _max_int("V4_Buy_Evidence_Score")
    sell_evidence_score = _max_int("V4_Sell_Evidence_Score")
    buy_layer_count = _max_int("V4_Demand_PRZ_Layer_Count")
    sell_layer_count = _max_int("V4_Supply_PRZ_Layer_Count")
    evidence_min = max(1, int(os.getenv("ENGINE_V4_EVIDENCE_MIN", "3")))
    buy_armed = _any("V4_Buy_Armed")
    sell_armed = _any("V4_Sell_Armed")
    buy_triggered = _any("V4_Buy_Memory_Trigger")
    sell_triggered = _any("V4_Sell_Memory_Trigger")

    missing_buy = []
    if buy_layer_count < 2:
        missing_buy.append(f"PRZ_LAYERS_{buy_layer_count}_OF_2")
    if buy_structure_reset:
        missing_buy.append("CANCELLED_BY_BEAR_BOS_CHOCH")
    if buy_evidence_score < evidence_min:
        missing_buy.append(
            f"EVIDENCE_{buy_evidence_score}_OF_{evidence_min}"
        )
    if buy_armed and not buy_triggered:
        missing_buy.append("WAIT_HA_OR_PINBAR_OR_M5_SNIPER")

    missing_sell = []
    if sell_layer_count < 2:
        missing_sell.append(f"PRZ_LAYERS_{sell_layer_count}_OF_2")
    if sell_structure_reset:
        missing_sell.append("CANCELLED_BY_BULL_BOS_CHOCH")
    if sell_evidence_score < evidence_min:
        missing_sell.append(
            f"EVIDENCE_{sell_evidence_score}_OF_{evidence_min}"
        )
    if sell_armed and not sell_triggered:
        missing_sell.append("WAIT_HA_OR_PINBAR_OR_M5_SNIPER")

    source_fields = (
        ("M15 DEMAND PRZ", ("Deep_Buy_PRZ_Context", "In_Pine_PRZ_Support")),
        ("M15 SUPPLY PRZ", ("Deep_Sell_PRZ_Context", "In_Pine_PRZ_Resistance")),
        ("H1 DEMAND PRZ", ("In_H1_PRZ_Support",)),
        ("H1 SUPPLY PRZ", ("In_H1_PRZ_Resistance",)),
        ("PRZ-A DEMAND", ("In_PRZ_A_Support",)),
        ("PRZ-A SUPPLY", ("In_PRZ_A_Resistance",)),
        ("PRZ-B DEMAND", ("In_PRZ_B_Support",)),
        ("PRZ-B SUPPLY", ("In_PRZ_B_Resistance",)),
    )
    location_sources = [
        label
        for label, fields in source_fields
        if _any(*fields)
    ]

    buy_ready = _any("V4_Buy_Setup")
    sell_ready = _any("V4_Sell_Setup")
    recent_prz_touch = buy_touch or sell_touch
    status = (
        "V4_TRIGGERED"
        if buy_ready or sell_ready
        else "BUY_ARMED"
        if buy_armed and not sell_armed
        else "SELL_ARMED"
        if sell_armed and not buy_armed
        else "BOTH_ARMED"
        if buy_armed and sell_armed
        else "WAIT_REARM"
        if buy_structure_reset or sell_structure_reset
        else "WAIT_CONFIRM"
        if recent_prz_touch
        else "WAIT_LOCATION"
    )

    trace_fields = (
        "close",
        "Pine_PRZ_Support_Low",
        "Pine_PRZ_Support_High",
        "Pine_PRZ_Resistance_Low",
        "Pine_PRZ_Resistance_High",
        "Fib_0618",
        "Fib_072",
        "Fib_0786",
        "Fib_0886",
        "Fib_100",
        "Fib_R_0618",
        "Fib_R_072",
        "Fib_R_0786",
        "Fib_R_0886",
        "Fib_R_100",
        "Kivanc_Scenario_State",
        "In_H1_PRZ_Support",
        "In_H1_PRZ_Resistance",
        "In_PRZ_A_Support",
        "In_PRZ_A_Resistance",
        "In_PRZ_B_Support",
        "In_PRZ_B_Resistance",
        "V4_Demand_PRZ_Layer_Count",
        "V4_Supply_PRZ_Layer_Count",
        "V4_Demand_PRZ_Qualified",
        "V4_Supply_PRZ_Qualified",
        "V4_Buy_M5_Sniper_Evidence",
        "V4_Sell_M5_Sniper_Evidence",
        "V4_Buy_M5_Sniper_Move",
        "V4_Sell_M5_Sniper_Move",
        "V4_Buy_M5_Sniper_Kivanc",
        "V4_Sell_M5_Sniper_Kivanc",
        "V4_Buy_M5_Sniper_BB",
        "V4_Sell_M5_Sniper_BB",
        "V4_Buy_M5_Sniper_BB_TF",
        "V4_Sell_M5_Sniper_BB_TF",
        "V4_Buy_M5_Sniper_Time",
        "V4_Sell_M5_Sniper_Time",
        "V4_Buy_Location_Memory",
        "V4_Sell_Location_Memory",
        "V4_Buy_Location_Age_Bars",
        "V4_Sell_Location_Age_Bars",
        "V4_Buy_Evidence_Score",
        "V4_Sell_Evidence_Score",
        "V4_Buy_Armed",
        "V4_Sell_Armed",
        "V4_M15_Bar_Closed",
        "V4_Buy_HA_Trigger",
        "V4_Sell_HA_Trigger",
        "V4_Buy_Pinbar_Trigger",
        "V4_Sell_Pinbar_Trigger",
        "V4_Buy_Sniper_Trigger",
        "V4_Sell_Sniper_Trigger",
        "V4_Buy_Trigger_Source",
        "V4_Sell_Trigger_Source",
        "V4_Buy_Memory_Trigger",
        "V4_Sell_Memory_Trigger",
        "HA_Bull_Reversal",
        "HA_Bear_Reversal",
        "Bullish_Pinbar",
        "Bearish_Pinbar",
        "Bull_Sweep",
        "Bear_Sweep",
        "VSA_Buy_Wins",
        "VSA_Sell_Wins",
        "V4_Buy_Setup",
        "V4_Sell_Setup",
    )
    latest = {
        field: _engine_v4_scalar(last.get(field))
        for field in trace_fields
        if field in last
    }

    return {
        "status": status,
        "v4_selected": False,
        "selected_direction": None,
        "lookback_bars": len(tail),
        "latest_bar_time": _iso_timestamp(tail.index[-1]),
        "current_price": _safe_float(last.get("close")),
        "context_direction": context_direction,
        "recent_prz_touch": recent_prz_touch,
        "recent_buy_prz_touch": buy_touch,
        "recent_sell_prz_touch": sell_touch,
        "buy_touch_time": buy_touch_time,
        "sell_touch_time": sell_touch_time,
        "buy_reset_time": buy_reset_time,
        "sell_reset_time": sell_reset_time,
        "buy_structure_reset": buy_structure_reset,
        "sell_structure_reset": sell_structure_reset,
        "recent_kivanc_state": recent_kivanc_state,
        "buy_sniper_armed": buy_sniper_armed,
        "sell_sniper_armed": sell_sniper_armed,
        "buy_sniper_move": _safe_float(
            buy_sniper_row.get("V4_Buy_M5_Sniper_Move")
            if buy_sniper_row is not None
            else 0.0
        ),
        "sell_sniper_move": _safe_float(
            sell_sniper_row.get("V4_Sell_M5_Sniper_Move")
            if sell_sniper_row is not None
            else 0.0
        ),
        "buy_sniper_kivanc": _safe_float(
            buy_sniper_row.get("V4_Buy_M5_Sniper_Kivanc")
            if buy_sniper_row is not None
            else 0.0
        ),
        "sell_sniper_kivanc": _safe_float(
            sell_sniper_row.get("V4_Sell_M5_Sniper_Kivanc")
            if sell_sniper_row is not None
            else 0.0
        ),
        "buy_sniper_bb": _safe_float(
            buy_sniper_row.get("V4_Buy_M5_Sniper_BB")
            if buy_sniper_row is not None
            else 0.0
        ),
        "sell_sniper_bb": _safe_float(
            sell_sniper_row.get("V4_Sell_M5_Sniper_BB")
            if sell_sniper_row is not None
            else 0.0
        ),
        "buy_sniper_bb_tf": str(
            buy_sniper_row.get("V4_Buy_M5_Sniper_BB_TF", "NONE")
            if buy_sniper_row is not None
            else "NONE"
        ),
        "sell_sniper_bb_tf": str(
            sell_sniper_row.get("V4_Sell_M5_Sniper_BB_TF", "NONE")
            if sell_sniper_row is not None
            else "NONE"
        ),
        "location_sources": location_sources,
        "buy_evidence_score": buy_evidence_score,
        "sell_evidence_score": sell_evidence_score,
        "buy_prz_layer_count": buy_layer_count,
        "sell_prz_layer_count": sell_layer_count,
        "evidence_min": evidence_min,
        "buy_armed": buy_armed,
        "sell_armed": sell_armed,
        "buy_triggered": buy_triggered,
        "sell_triggered": sell_triggered,
        "buy_trigger_source": _latest_text("V4_Buy_Trigger_Source"),
        "sell_trigger_source": _latest_text("V4_Sell_Trigger_Source"),
        "buy_setup_count": _count("V4_Buy_Setup"),
        "sell_setup_count": _count("V4_Sell_Setup"),
        "buy_entry_zone_count": _count("V4_Buy_Entry_Zone"),
        "sell_entry_zone_count": _count("V4_Sell_Entry_Zone"),
        "missing_buy": missing_buy,
        "missing_sell": missing_sell,
        "latest": latest,
        "harmonic": _harmonic_gate_context(blueprint),
    }
