"""
auto_fibo_entry.py — Alpha Buffalo v5 (opt-in)

Auto Fibo (144, 1.272) style Estimate Entry — the same methodology added to
the Pine multi-asset fork (AlphaBuff_v2.5.4R3_MultiAsset.pine's
`f_auto_fibo()`), ported here so the Python side (trend_monitor.py's
Telegram Trend Update and signal_engine.py's live signal engine) can
show/use the same Estimate Entry point, per the request to apply "the
Kivanc-fibo idea from the screenshot" to the Python side too.

[IMPORTANT] This is intentionally NOT kivanc_vsaob.py's method. kivanc_vsaob.py
implements a different, already-live methodology: a small pivot window
(PIVOT_N=3) Golden Zone (0.618-0.786) combined with Order Block detection —
used inside signal_engine.py's own fib_zone/get_kivanc_swing_zone logic.
This module is the separate "Auto Fibo" style the user pointed to in their
screenshot: a large rolling window (default 144 bars) auto-detects whichever
swing extreme (high or low) happened more recently to decide swing
direction, then applies a golden-zone retracement (0.618-0.786 by default)
as the Estimate Entry zone, plus a 1.272 extension as a reference target.

Non-repaint convention: matches kivanc_vsaob.py's existing convention of
using `df.iloc[:-1]` ("confirmed" bars only, dropping the live/forming
candle) rather than Pine's `[1]`-shifted-tuple trick, since this file feeds
a bar-close polling loop rather than a live-updating chart.

Everything here is pure/side-effect-free and computing it changes nothing
by itself — see trend_monitor.py's ALPHA_TREND_AUTO_FIBO_ENABLED and
signal_engine.py's ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED for the two opt-in
call sites (both default OFF).
"""
import os
from dataclasses import dataclass
from typing import Optional

import pandas as pd

AUTO_FIBO_LENGTH      = int(os.getenv("ALPHA_AUTO_FIBO_LENGTH", "144"))
AUTO_FIBO_GOLDEN_LOW  = float(os.getenv("ALPHA_AUTO_FIBO_GOLDEN_LOW", "0.618"))
AUTO_FIBO_GOLDEN_HIGH = float(os.getenv("ALPHA_AUTO_FIBO_GOLDEN_HIGH", "0.786"))
AUTO_FIBO_EXT_LEVEL   = float(os.getenv("ALPHA_AUTO_FIBO_EXT_LEVEL", "1.272"))

DIRECTION_UP   = "UP"     # swing HIGH is more recent -> pullback retracement = BUY zone
DIRECTION_DOWN = "DOWN"   # swing LOW is more recent  -> bounce retracement   = SELL zone


@dataclass
class AutoFiboEstimate:
    direction:  str    # DIRECTION_UP / DIRECTION_DOWN
    swing_high: float
    swing_low:  float
    entry_near: float  # golden_low retracement (closer to the swing extreme)
    entry_deep: float  # golden_high retracement (deeper pullback/bounce)
    ext_target: float  # ext_level extension — reference target only
    zone_lo:    float
    zone_hi:    float

    def in_zone(self, price: float, tolerance: float = 0.0) -> bool:
        return (self.zone_lo - tolerance) <= price <= (self.zone_hi + tolerance)


def compute_auto_fibo(
    df: Optional[pd.DataFrame],
    length: int = AUTO_FIBO_LENGTH,
    golden_low: float = AUTO_FIBO_GOLDEN_LOW,
    golden_high: float = AUTO_FIBO_GOLDEN_HIGH,
    ext_level: float = AUTO_FIBO_EXT_LEVEL,
) -> Optional[AutoFiboEstimate]:
    """Auto Fibo (length, ext_level) swing estimate on CONFIRMED bars only
    (drops the live/forming last candle). Returns None when there isn't
    enough history yet, or the window is degenerate (zero range).
    """
    if df is None or len(df) < 3:
        return None

    safe_df = df.iloc[:-1]                       # drop the live/forming candle
    window = safe_df.tail(max(int(length), 2))
    if len(window) < 2:
        return None

    highs = window["high"].values
    lows  = window["low"].values
    swing_high = float(highs.max())
    swing_low  = float(lows.min())
    span = swing_high - swing_low
    if span <= 0:
        return None

    # Offset from the END of the window (0 = most recent bar). Ties resolve
    # to the MOST RECENT occurrence of the extreme (reverse, then argmax/argmin
    # picks the first hit from the end) -- matching Pine's
    # ta.highestbars/ta.lowestbars tie-breaking.
    high_offset_from_end = highs[::-1].argmax()
    low_offset_from_end  = lows[::-1].argmin()
    high_more_recent = high_offset_from_end < low_offset_from_end

    if high_more_recent:
        direction  = DIRECTION_UP
        entry_near = swing_high - span * golden_low
        entry_deep = swing_high - span * golden_high
        ext_target = swing_low + span * ext_level
    else:
        direction  = DIRECTION_DOWN
        entry_near = swing_low + span * golden_low
        entry_deep = swing_low + span * golden_high
        ext_target = swing_high - span * ext_level

    return AutoFiboEstimate(
        direction=direction,
        swing_high=swing_high,
        swing_low=swing_low,
        entry_near=entry_near,
        entry_deep=entry_deep,
        ext_target=ext_target,
        zone_lo=min(entry_near, entry_deep),
        zone_hi=max(entry_near, entry_deep),
    )
