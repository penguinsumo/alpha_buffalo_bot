#!/usr/bin/env python3
"""
Shared indicator calculation — Alpha Buffalo v12-core / Pine v2.3 aligned.

Root rule:
- Location first: PRZ/BB/tunnel/micro lot0.
- HA/Pinbar + VSA two-side inside the marked zone = setup.
- BOS/CHoCH promotes setup; it is not the setup itself.
"""
from __future__ import annotations

import os
import pandas as pd
import numpy as np


def _bool_series(index, value: bool = False) -> pd.Series:
    return pd.Series(value, index=index, dtype=bool)


def _asia_session_mask(index: pd.DatetimeIndex) -> pd.Series:
    """Return the production ASIA session mask using Bangkok time."""
    if index.tz is None:
        localized = index.tz_localize("UTC")
    else:
        localized = index.tz_convert("UTC")
    bkk_hour = localized.tz_convert("Asia/Bangkok").hour
    return pd.Series((bkk_hour >= 5) & (bkk_hour < 14), index=index, dtype=bool)


def _apply_deep_sweep_reclaim_state(df: pd.DataFrame) -> pd.DataFrame:
    """Track a 1.00 sweep wall until price reclaims the 0.886-0.720 window.

    The wall candle creates a setup, never an entry.  A later closed candle must
    break the wall candle in the reversal direction while its close is inside
    the reclaim window.  State expires quickly and is invalidated beyond the
    wall, keeping the calculation causal and safe to replay.
    """
    max_age = max(1, int(os.getenv("ALPHA_DEEP_RECLAIM_MAX_BARS", "12")))
    n = len(df)
    buy_active = np.zeros(n, dtype=bool)
    sell_active = np.zeros(n, dtype=bool)
    buy_trigger = np.zeros(n, dtype=bool)
    sell_trigger = np.zeros(n, dtype=bool)
    buy_wall_low = np.full(n, np.nan)
    buy_wall_high = np.full(n, np.nan)
    sell_wall_low = np.full(n, np.nan)
    sell_wall_high = np.full(n, np.nan)
    buy_wall_age = np.full(n, -1, dtype=int)
    sell_wall_age = np.full(n, -1, dtype=int)

    buy_state = None
    sell_state = None

    for pos in range(n):
        row = df.iloc[pos]
        atr = float(row.get("ATR14", 0.0) or 0.0)
        invalidation_buffer = max(atr * 0.05, 0.0)

        if bool(row.get("Deep_Buy_Wall_Candidate", False)):
            buy_state = {
                "low": float(row["low"]),
                "high": float(row["high"]),
                "age": 0,
            }
        elif buy_state is not None:
            buy_state["age"] += 1

        if bool(row.get("Deep_Sell_Wall_Candidate", False)):
            sell_state = {
                "low": float(row["low"]),
                "high": float(row["high"]),
                "age": 0,
            }
        elif sell_state is not None:
            sell_state["age"] += 1

        if buy_state is not None:
            expired = buy_state["age"] > max_age
            invalidated = float(row["close"]) < buy_state["low"] - invalidation_buffer
            if expired or invalidated:
                buy_state = None
            else:
                buy_active[pos] = True
                buy_wall_low[pos] = buy_state["low"]
                buy_wall_high[pos] = buy_state["high"]
                buy_wall_age[pos] = buy_state["age"]
                in_window = (
                    float(row["close"]) >= float(row.get("Fib_0886", np.inf))
                    and float(row["close"]) <= float(row.get("Fib_072", -np.inf))
                )
                confirmed = bool(row.get("HA_Bullish", False)) or bool(row.get("VSA_Buy_Wins", False))
                if buy_state["age"] > 0 and in_window and float(row["close"]) > buy_state["high"] and confirmed:
                    buy_trigger[pos] = True
                    buy_state = None

        if sell_state is not None:
            expired = sell_state["age"] > max_age
            invalidated = float(row["close"]) > sell_state["high"] + invalidation_buffer
            if expired or invalidated:
                sell_state = None
            else:
                sell_active[pos] = True
                sell_wall_low[pos] = sell_state["low"]
                sell_wall_high[pos] = sell_state["high"]
                sell_wall_age[pos] = sell_state["age"]
                in_window = (
                    float(row["close"]) <= float(row.get("Fib_R_0886", -np.inf))
                    and float(row["close"]) >= float(row.get("Fib_R_072", np.inf))
                )
                confirmed = bool(row.get("HA_Bearish", False)) or bool(row.get("VSA_Sell_Wins", False))
                if sell_state["age"] > 0 and in_window and float(row["close"]) < sell_state["low"] and confirmed:
                    sell_trigger[pos] = True
                    sell_state = None

    df["Deep_Buy_Reclaim_Active"] = buy_active
    df["Deep_Sell_Reclaim_Active"] = sell_active
    df["Deep_Buy_Reclaim_Trigger"] = buy_trigger
    df["Deep_Sell_Reclaim_Trigger"] = sell_trigger
    df["Deep_Buy_Wall_Low"] = buy_wall_low
    df["Deep_Buy_Wall_High"] = buy_wall_high
    df["Deep_Sell_Wall_Low"] = sell_wall_low
    df["Deep_Sell_Wall_High"] = sell_wall_high
    df["Deep_Buy_Wall_Age"] = buy_wall_age
    df["Deep_Sell_Wall_Age"] = sell_wall_age
    return df


def _apply_zone_pinbar_break_state(df: pd.DataFrame) -> pd.DataFrame:
    """Require a later candle to break a Kivanc-zone pinbar before entry."""
    max_age = max(1, int(os.getenv("ALPHA_PINBAR_CONFIRM_MAX_BARS", "3")))
    n = len(df)
    buy_trigger = np.zeros(n, dtype=bool)
    sell_trigger = np.zeros(n, dtype=bool)
    buy_wall_low = np.full(n, np.nan)
    buy_wall_high = np.full(n, np.nan)
    sell_wall_low = np.full(n, np.nan)
    sell_wall_high = np.full(n, np.nan)
    buy_state = None
    sell_state = None

    for pos in range(n):
        row = df.iloc[pos]
        if bool(row.get("Zone_Buy_Pinbar_Candidate", False)):
            buy_state = {"low": float(row["low"]), "high": float(row["high"]), "age": 0}
        elif buy_state is not None:
            buy_state["age"] += 1

        if bool(row.get("Zone_Sell_Pinbar_Candidate", False)):
            sell_state = {"low": float(row["low"]), "high": float(row["high"]), "age": 0}
        elif sell_state is not None:
            sell_state["age"] += 1

        if buy_state is not None:
            invalid = float(row["close"]) < buy_state["low"]
            expired = buy_state["age"] > max_age
            if invalid or expired:
                buy_state = None
            elif (
                buy_state["age"] > 0
                and bool(row.get("In_Session_Kivanc_Buy_Zone", False))
                and float(row["close"]) > buy_state["high"]
                and (bool(row.get("HA_Bullish", False)) or bool(row.get("VSA_Buy_Wins", False)))
            ):
                buy_trigger[pos] = True
                buy_wall_low[pos] = buy_state["low"]
                buy_wall_high[pos] = buy_state["high"]
                buy_state = None

        if sell_state is not None:
            invalid = float(row["close"]) > sell_state["high"]
            expired = sell_state["age"] > max_age
            if invalid or expired:
                sell_state = None
            elif (
                sell_state["age"] > 0
                and bool(row.get("In_Session_Kivanc_Sell_Zone", False))
                and float(row["close"]) < sell_state["low"]
                and (bool(row.get("HA_Bearish", False)) or bool(row.get("VSA_Sell_Wins", False)))
            ):
                sell_trigger[pos] = True
                sell_wall_low[pos] = sell_state["low"]
                sell_wall_high[pos] = sell_state["high"]
                sell_state = None

    df["Zone_Buy_Pinbar_Trigger"] = buy_trigger
    df["Zone_Sell_Pinbar_Trigger"] = sell_trigger
    df["Zone_Buy_Wall_Low"] = buy_wall_low
    df["Zone_Buy_Wall_High"] = buy_wall_high
    df["Zone_Sell_Wall_Low"] = sell_wall_low
    df["Zone_Sell_Wall_High"] = sell_wall_high
    return df


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(df.index, pd.DatetimeIndex):
        raise TypeError("DataFrame must have DatetimeIndex")

    df = df.copy().sort_index()

    # ─────────────────────────────────────────────
    # 1) Core BB / ATR / EMA
    # ─────────────────────────────────────────────
    df["BB_Mid"] = df["close"].rolling(20, min_periods=20).mean()
    df["BB_Std"] = df["close"].rolling(20, min_periods=20).std()
    df["BB_Lower"] = df["BB_Mid"] - 2 * df["BB_Std"]
    df["BB_Upper"] = df["BB_Mid"] + 2 * df["BB_Std"]

    h, l, c_prev = df["high"], df["low"], df["close"].shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    df["ATR14"] = tr.rolling(14, min_periods=14).mean()

    df["EMA20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["EMA50"] = df["close"].ewm(span=50, adjust=False).mean()

    # ─────────────────────────────────────────────
    # 2) Sweeps / candles
    # ─────────────────────────────────────────────
    df["Low_Prev"] = df["low"].shift(1)
    df["High_Prev"] = df["high"].shift(1)
    df["Bull_Sweep"] = (df["low"] < df["Low_Prev"]) & (df["close"] > df["Low_Prev"])
    df["Bear_Sweep"] = (df["high"] > df["High_Prev"]) & (df["close"] < df["High_Prev"])

    body = (df["close"] - df["open"]).abs()
    lower_wick = np.minimum(df["open"], df["close"]) - df["low"]
    upper_wick = df["high"] - np.maximum(df["open"], df["close"])
    candle_range = (df["high"] - df["low"]).replace(0, np.nan)

    # Pine f_is_pinbar(true/false)
    df["Bullish_Pinbar"] = (lower_wick >= body * 2.0) & (upper_wick <= body)
    df["Bearish_Pinbar"] = (upper_wick >= body * 2.0) & (lower_wick <= body)

    # ─────────────────────────────────────────────
    # 3) Heikin Ashi + reversal / CF sequence
    # ─────────────────────────────────────────────
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = pd.Series(index=df.index, dtype="float64")
    if len(df):
        ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2
        for i in range(1, len(df)):
            ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2

    df["HA_Close"] = ha_close
    df["HA_Open"] = ha_open
    df["HA_Bullish"] = df["HA_Close"] > df["HA_Open"]
    df["HA_Bearish"] = df["HA_Close"] < df["HA_Open"]
    df["HA_Bull_Reversal"] = df["HA_Bearish"].shift(1, fill_value=False) & df["HA_Bullish"]
    df["HA_Bear_Reversal"] = df["HA_Bullish"].shift(1, fill_value=False) & df["HA_Bearish"]
    df["HA_Green_2_CF"] = df["HA_Bullish"] & df["HA_Bullish"].shift(1, fill_value=False)
    df["HA_Red_2_CF"] = df["HA_Bearish"] & df["HA_Bearish"].shift(1, fill_value=False)

    ha_body = (df["HA_Close"] - df["HA_Open"]).abs()
    df["HA_Red_Weakening"] = (
        df["HA_Bearish"].shift(1, fill_value=False)
        & df["HA_Bearish"]
        & (ha_body < ha_body.shift(1))
    )
    df["HA_Green_Weakening"] = (
        df["HA_Bullish"].shift(1, fill_value=False)
        & df["HA_Bullish"]
        & (ha_body < ha_body.shift(1))
    )

    # Pine 15m PA Confirm proxy
    df["Pine_PA_Bull_Confirmed"] = df["Bullish_Pinbar"] | df["HA_Bull_Reversal"] | df["HA_Green_2_CF"]
    df["Pine_PA_Bear_Confirmed"] = df["Bearish_Pinbar"] | df["HA_Bear_Reversal"] | df["HA_Red_2_CF"]

    # ─────────────────────────────────────────────
    # 4) Pine Deep Sweep PRZ grid (0.786-0.886) adapted to backend.
    #    Uses rolling daily swings when available, otherwise rolling bars.
    # ─────────────────────────────────────────────
    prz_mode = os.getenv("ALPHA_PINE_PRZ_MODE", "deep").lower()
    if prz_mode.startswith("classic"):
        fib_min, fib_max = 0.618, 0.705
    else:
        fib_min, fib_max = 0.786, 0.886

    daily = df.resample("1D").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    lookback_days = int(os.getenv("ALPHA_PINE_PRZ_LOOKBACK_DAYS", "20"))
    fallback_bars = int(os.getenv("ALPHA_PINE_PRZ_FALLBACK_BARS", "96"))

    if len(daily) >= 3:
        min_periods = min(3, len(daily))
        # Only prior completed days may define today's PRZ.  Without the shift,
        # an intraday backtest sees the final high/low of its own day.
        swing_high_daily = daily["high"].rolling(lookback_days, min_periods=min_periods).max().shift(1)
        swing_low_daily = daily["low"].rolling(lookback_days, min_periods=min_periods).min().shift(1)
        fallback_high = df["high"].rolling(fallback_bars, min_periods=min(20, len(df))).max().shift(1)
        fallback_low = df["low"].rolling(fallback_bars, min_periods=min(20, len(df))).min().shift(1)
        swing_high = swing_high_daily.reindex(df.index, method="ffill").combine_first(fallback_high)
        swing_low = swing_low_daily.reindex(df.index, method="ffill").combine_first(fallback_low)
    else:
        min_periods = min(20, max(1, len(df)))
        swing_high = df["high"].rolling(fallback_bars, min_periods=min_periods).max()
        swing_low = df["low"].rolling(fallback_bars, min_periods=min_periods).min()

    diff = swing_high - swing_low
    df["Pine_Swing_H"] = swing_high
    df["Pine_Swing_L"] = swing_low
    df["Pine_PRZ_Resistance_Low"] = swing_low + diff * fib_min
    df["Pine_PRZ_Resistance_High"] = swing_low + diff * fib_max
    df["Pine_PRZ_Support_Low"] = swing_high - diff * fib_max
    df["Pine_PRZ_Support_High"] = swing_high - diff * fib_min

    df["In_Pine_PRZ_Support"] = (
        (df["close"] >= df["Pine_PRZ_Support_Low"])
        & (df["close"] <= df["Pine_PRZ_Support_High"])
    )
    df["In_Pine_PRZ_Resistance"] = (
        (df["close"] >= df["Pine_PRZ_Resistance_Low"])
        & (df["close"] <= df["Pine_PRZ_Resistance_High"])
    )

    # ─────────────────────────────────────────────
    # 5) 1H swing/trend and legacy fibs
    # ─────────────────────────────────────────────
    df1h = df.resample("1h").agg({"high": "max", "low": "min", "close": "last"}).dropna()
    if len(df1h) >= 5:
        # Shift one H1 bucket so every M15 bar sees only completed H1 data.
        sw_high = df1h["high"].rolling(5, min_periods=2).max().shift(1).reindex(df.index, method="ffill")
        sw_low = df1h["low"].rolling(5, min_periods=2).min().shift(1).reindex(df.index, method="ffill")
    else:
        sw_high = df["high"].rolling(100, min_periods=20).max()
        sw_low = df["low"].rolling(100, min_periods=20).min()

    df["Swing_H"] = sw_high
    df["Swing_L"] = sw_low
    df["Diff"] = df["Swing_H"] - df["Swing_L"]
    df["Fib_0618"] = df["Swing_H"] - df["Diff"] * 0.618
    df["Fib_072"] = df["Swing_H"] - df["Diff"] * 0.72
    df["PRZ_Next"] = df["Swing_L"]
    df["Fib_0786"] = df["Swing_H"] - df["Diff"] * 0.786
    df["Fib_0886"] = df["Swing_H"] - df["Diff"] * 0.886
    df["Fib_100"] = df["Swing_L"]
    df["Fib_R_0618"] = df["Swing_L"] + df["Diff"] * 0.618
    df["Fib_R_072"] = df["Swing_L"] + df["Diff"] * 0.72
    df["Fib_R_0786"] = df["Swing_L"] + df["Diff"] * 0.786
    df["Fib_R_0886"] = df["Swing_L"] + df["Diff"] * 0.886
    df["Fib_R_100"] = df["Swing_H"]
    df["Buy_Killzone_0786_0886"] = (df["close"] >= df["Fib_0886"]) & (df["close"] <= df["Fib_0786"])

    if len(df1h):
        df1h["EMA50_1h"] = df1h["close"].ewm(span=50, adjust=False).mean()
        trend_up = (df1h["close"] > df1h["EMA50_1h"]).astype(int).shift(1).reindex(df.index, method="ffill").fillna(0)
        df["Trend_1H_Up"] = trend_up.astype(bool)
    else:
        df["Trend_1H_Up"] = False

    # ─────────────────────────────────────────────
    # 6) Pine SMC trigger proxy: CHoCH + OB on current 15m feed.
    #    BOS promotion is consumed by higher-level journey logic.
    # ─────────────────────────────────────────────
    lookback_cho = int(os.getenv("ALPHA_PINE_CHOCH_LOOKBACK", "5"))
    ob_atr_mult = float(os.getenv("ALPHA_PINE_OB_ATR_MULT", "0.5"))

    hh_prev = df["high"].rolling(lookback_cho, min_periods=lookback_cho).max().shift(1)
    ll_prev = df["low"].rolling(lookback_cho, min_periods=lookback_cho).min().shift(1)
    df["CHoCH_Bull"] = df["close"] > hh_prev
    df["CHoCH_Bear"] = df["close"] < ll_prev

    upper_wick = df["high"] - np.maximum(df["open"], df["close"])
    lower_wick = np.minimum(df["open"], df["close"]) - df["low"]
    body_gt_wick = body > (upper_wick + lower_wick) * 0.8
    sig_body = body > (df["ATR14"] * ob_atr_mult)
    prev_down = df["close"].shift(1) < df["open"].shift(1)
    curr_up = df["close"] > df["open"]
    df["Bull_OB"] = prev_down & curr_up & (df["close"] >= df["open"].shift(1)) & (df["open"] <= df["close"].shift(1)) & body_gt_wick & sig_body
    df["Bear_OB"] = (~prev_down.fillna(False)) & (~curr_up) & (df["open"] >= df["close"].shift(1)) & (df["close"] <= df["open"].shift(1)) & body_gt_wick & sig_body

    # ─────────────────────────────────────────────
    # 7) VSA two-side pressure proxy in the marked frame.
    #    If TwelveData has no volume, wick/body footprint still provides a usable proxy.
    # ─────────────────────────────────────────────
    if "volume" in df.columns:
        volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0.0)
    else:
        volume = pd.Series(1.0, index=df.index)
    avg_vol20 = volume.rolling(20, min_periods=5).mean().replace(0, np.nan)
    rvol = (volume / avg_vol20).replace([np.inf, -np.inf], np.nan).fillna(1.0)
    df["RVOL"] = rvol

    lower_wick_ratio = (lower_wick / candle_range).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    upper_wick_ratio = (upper_wick / candle_range).replace([np.inf, -np.inf], np.nan).fillna(0.0)
    body_ratio = (body / candle_range).replace([np.inf, -np.inf], np.nan).fillna(0.0)

    df["VSA_Buy_Pressure"] = lower_wick_ratio + (df["close"] > df["open"]).astype(float) * body_ratio * 0.5 + (rvol > 1.2).astype(float) * 0.2
    df["VSA_Sell_Pressure"] = upper_wick_ratio + (df["close"] < df["open"]).astype(float) * body_ratio * 0.5 + (rvol > 1.2).astype(float) * 0.2
    df["VSA_Buy_Wins"] = df["VSA_Buy_Pressure"] > df["VSA_Sell_Pressure"]
    df["VSA_Sell_Wins"] = df["VSA_Sell_Pressure"] > df["VSA_Buy_Pressure"]

    # ─────────────────────────────────────────────
    # 8) V4 location setup / micro lot0.
    # ─────────────────────────────────────────────
    atr_buffer = df["ATR14"].fillna(0.0) * 0.25
    df["Near_BB_Lower"] = df["low"] <= (df["BB_Lower"] + atr_buffer)
    df["Near_BB_Upper"] = df["high"] >= (df["BB_Upper"] - atr_buffer)

    # Session-aware Kivanc map: ASIA may react from the classic 0.618-0.786
    # band; LONDON/NY use the v12 deep 0.720-0.886 band.
    asia = _asia_session_mask(df.index)
    df["Is_Asia_Session"] = asia
    df["Kivanc_Buy_Zone_Low"] = np.where(asia, df["Fib_0786"], df["Fib_0886"])
    df["Kivanc_Buy_Zone_High"] = np.where(asia, df["Fib_0618"], df["Fib_072"])
    df["Kivanc_Sell_Zone_Low"] = np.where(asia, df["Fib_R_0618"], df["Fib_R_072"])
    df["Kivanc_Sell_Zone_High"] = np.where(asia, df["Fib_R_0786"], df["Fib_R_0886"])
    df["In_Session_Kivanc_Buy_Zone"] = (
        (df["close"] >= df["Kivanc_Buy_Zone_Low"])
        & (df["close"] <= df["Kivanc_Buy_Zone_High"])
    )
    df["In_Session_Kivanc_Sell_Zone"] = (
        (df["close"] >= df["Kivanc_Sell_Zone_Low"])
        & (df["close"] <= df["Kivanc_Sell_Zone_High"])
    )

    deep_session = ~asia
    touch_tolerance = df["ATR14"].fillna(0.0) * float(os.getenv("ALPHA_DEEP_100_TOUCH_ATR", "0.12"))
    df["Deep_Buy_Touch_100"] = deep_session & (df["low"] <= df["Fib_100"] + touch_tolerance)
    df["Deep_Sell_Touch_100"] = deep_session & (df["high"] >= df["Fib_R_100"] - touch_tolerance)
    # A deep wick may close beyond the PRZ, so require candle/PRZ overlap rather
    # than requiring the closing price to remain inside the original box.
    df["Deep_Buy_PRZ_Context"] = (
        (df["low"] <= df["Pine_PRZ_Support_High"])
        & (df["high"] >= df["Pine_PRZ_Support_Low"])
    )
    df["Deep_Sell_PRZ_Context"] = (
        (df["low"] <= df["Pine_PRZ_Resistance_High"])
        & (df["high"] >= df["Pine_PRZ_Resistance_Low"])
    )
    df["Deep_Buy_Wall_Candidate"] = (
        df["Deep_Buy_Touch_100"]
        & df["Deep_Buy_PRZ_Context"]
        & df["Near_BB_Lower"]
        & df["Bullish_Pinbar"]
        & df["VSA_Buy_Wins"]
    )
    df["Deep_Sell_Wall_Candidate"] = (
        df["Deep_Sell_Touch_100"]
        & df["Deep_Sell_PRZ_Context"]
        & df["Near_BB_Upper"]
        & df["Bearish_Pinbar"]
        & df["VSA_Sell_Wins"]
    )
    df = _apply_deep_sweep_reclaim_state(df)

    # A pinbar inside the normal session zone is also SETUP-only.  Entry waits
    # for a later closed candle to break the pinbar in the reversal direction.
    df["Zone_Buy_Pinbar_Candidate"] = (
        df["In_Session_Kivanc_Buy_Zone"]
        & df["In_Pine_PRZ_Support"]
        & df["Near_BB_Lower"]
        & df["Bullish_Pinbar"]
        & df["VSA_Buy_Wins"]
    )
    df["Zone_Sell_Pinbar_Candidate"] = (
        df["In_Session_Kivanc_Sell_Zone"]
        & df["In_Pine_PRZ_Resistance"]
        & df["Near_BB_Upper"]
        & df["Bearish_Pinbar"]
        & df["VSA_Sell_Wins"]
    )
    df = _apply_zone_pinbar_break_state(df)
    # The indicator set is intentionally wide; defragment before adding the
    # final compatibility fields used by the engines and runtime diagnostics.
    df = df.copy()

    lower_zone_touch = df["Near_BB_Lower"] | df["In_Pine_PRZ_Support"] | df["Buy_Killzone_0786_0886"] | df["Bull_Sweep"]
    upper_zone_touch = df["Near_BB_Upper"] | df["In_Pine_PRZ_Resistance"] | df["Bear_Sweep"]

    df["Micro_Lot0_Low"] = df["low"].where(lower_zone_touch).rolling(12, min_periods=1).min().ffill()
    df["Micro_Lot0_High"] = df["high"].where(upper_zone_touch).rolling(12, min_periods=1).max().ffill()

    # BB edge + Pine PRZ overlap is the strongest V4 entry location.
    # This is a SETUP zone, not a BOS condition.
    df["BB_PRZ_Support_Confluence"] = df["Near_BB_Lower"] & df["In_Pine_PRZ_Support"]
    df["BB_PRZ_Resistance_Confluence"] = df["Near_BB_Upper"] & df["In_Pine_PRZ_Resistance"]

    buy_location = df["In_Pine_PRZ_Support"] | df["In_Session_Kivanc_Buy_Zone"]
    sell_location = df["In_Pine_PRZ_Resistance"] | df["In_Session_Kivanc_Sell_Zone"]
    buy_reaction = df["Near_BB_Lower"] | df["Bull_Sweep"]
    sell_reaction = df["Near_BB_Upper"] | df["Bear_Sweep"]

    base_buy_entry_zone = buy_location & buy_reaction
    base_sell_entry_zone = sell_location & sell_reaction
    df["V4_Buy_Entry_Zone"] = base_buy_entry_zone | df["Deep_Buy_Reclaim_Trigger"] | df["Zone_Buy_Pinbar_Trigger"]
    df["V4_Sell_Entry_Zone"] = base_sell_entry_zone | df["Deep_Sell_Reclaim_Trigger"] | df["Zone_Sell_Pinbar_Trigger"]

    # Pinbars need a later break; HA reversal may confirm directly on its own
    # closed bar.  HA continuation alone is quality, not a fresh entry trigger.
    standard_buy_setup = base_buy_entry_zone & df["HA_Bull_Reversal"] & df["VSA_Buy_Wins"]
    standard_sell_setup = base_sell_entry_zone & df["HA_Bear_Reversal"] & df["VSA_Sell_Wins"]
    df["V4_Buy_Setup"] = standard_buy_setup | df["Zone_Buy_Pinbar_Trigger"] | df["Deep_Buy_Reclaim_Trigger"]
    df["V4_Sell_Setup"] = standard_sell_setup | df["Zone_Sell_Pinbar_Trigger"] | df["Deep_Sell_Reclaim_Trigger"]

    # Pine valid signal equivalents. These promote quality, not direct HTF trend.
    df["Pine_Valid_Buy"] = df["In_Pine_PRZ_Support"] & df["Pine_PA_Bull_Confirmed"] & df["VSA_Buy_Wins"] & (df["CHoCH_Bull"] | df["Bull_OB"] | df["HA_Green_2_CF"])
    df["Pine_Valid_Sell"] = df["In_Pine_PRZ_Resistance"] & df["Pine_PA_Bear_Confirmed"] & df["VSA_Sell_Wins"] & (df["CHoCH_Bear"] | df["Bear_OB"] | df["HA_Red_2_CF"])

    df["V4_Block_Sell_At_Lower"] = df["V4_Buy_Setup"] | standard_buy_setup
    df["V4_Block_Buy_At_Upper"] = df["V4_Sell_Setup"] | standard_sell_setup

    df["Kivanc_Scenario_State"] = np.select(
        [
            df["Deep_Buy_Reclaim_Trigger"],
            df["Deep_Sell_Reclaim_Trigger"],
            df["Deep_Buy_Reclaim_Active"],
            df["Deep_Sell_Reclaim_Active"],
            df["Zone_Buy_Pinbar_Trigger"],
            df["Zone_Sell_Pinbar_Trigger"],
            df["In_Session_Kivanc_Buy_Zone"],
            df["In_Session_Kivanc_Sell_Zone"],
        ],
        [
            "READY_BUY_RECLAIM",
            "READY_SELL_RECLAIM",
            "BUY_WALL_WAIT_RECLAIM",
            "SELL_WALL_WAIT_RECLAIM",
            "READY_BUY_PINBAR_BREAK",
            "READY_SELL_PINBAR_BREAK",
            "BUY_ZONE_ARMED",
            "SELL_ZONE_ARMED",
        ],
        default="OUTSIDE",
    )

    # Legacy micro structure fields retained for SELL evidence compatibility.
    df["Micro_Swing_H"] = df["high"].rolling(5, min_periods=5).max().shift(1)
    df["Micro_Swing_L"] = df["low"].rolling(5, min_periods=5).min().shift(1)
    df["Swing_H_Prev"] = df["Swing_H"].shift(1)
    df["Swing_L_Prev"] = df["Swing_L"].shift(1)
    df["Sweep_Above_100"] = (df["high"] > df["Swing_H_Prev"]) & (df["close"] < df["Swing_H_Prev"])
    df["Sweep_Below_100"] = (df["low"] < df["Swing_L_Prev"]) & (df["close"] > df["Swing_L_Prev"])
    df["Sell_Reclaim"] = df["Sweep_Above_100"] & df["HA_Bearish"]
    df["Buy_Reclaim"] = df["Sweep_Below_100"] & df["HA_Bullish"]
    df["Micro_BOS_Down"] = df["close"] < df["Micro_Swing_L"]
    df["Micro_BOS_Up"] = df["close"] > df["Micro_Swing_H"]

    return df
