#!/usr/bin/env python3
"""
Regression tests for the Auto Fibo (144, 1.272) style Estimate Entry
feature (auto_fibo_entry.py) and its two opt-in call sites:

  - trend_monitor.py: ALPHA_TREND_AUTO_FIBO_ENABLED (default OFF) --
    display-only lines in the Telegram Trend Update.
  - signal_engine.py: ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED (default OFF) --
    resolve_auto_fibo_filter() as an extra confirmation gate in
    compute_signal(); the Estimate Entry fields on CloudSignal are always
    computed/attached regardless of the filter flag.

This is the same Auto Fibo methodology already delivered in the Pine
multi-asset fork (AlphaBuff_v2.5.4R3_MultiAsset.pine's `f_auto_fibo()`) --
NOT kivanc_vsaob.py's small-pivot (PIVOT_N=3) Golden Zone method, which is
a different, already-live methodology used elsewhere in signal_engine.py.

Run: python3 scripts/test_auto_fibo_entry.py
Exits non-zero on any failure.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

from auto_fibo_entry import compute_auto_fibo, DIRECTION_UP, DIRECTION_DOWN

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def make_df(prices, seg_len=1):
    """Flat OHLC (open=high=low=close) series from a list of price points,
    optionally linearly interpolated seg_len steps per leg so there's a
    distinct value on every bar (avoids tie ambiguity in argmax/argmin)."""
    full = [float(prices[0])]
    for i in range(1, len(prices)):
        start, end = prices[i - 1], prices[i]
        step = (end - start) / seg_len
        for s in range(1, seg_len + 1):
            full.append(start + step * s)
    return pd.DataFrame({
        "open": full, "high": full, "low": full, "close": full,
        "volume": [0] * len(full),
    })


# ── compute_auto_fibo(): not enough history ─────────────────────────────
check("compute_auto_fibo: None df -> None", compute_auto_fibo(None) is None)
check("compute_auto_fibo: too-short df -> None", compute_auto_fibo(make_df([100, 101])) is None)

# ── compute_auto_fibo(): swing HIGH more recent -> UP (BUY zone) ───────
# 100 -> 90 (swing low, further back) -> 120 (swing high, more recent) ->
# 115 (drop off the live/forming candle, dropped by iloc[:-1] anyway).
up_df = make_df([100, 90, 120, 115], seg_len=10)
up_est = compute_auto_fibo(up_df, length=100)
check("compute_auto_fibo: swing high more recent -> DIRECTION_UP",
      up_est is not None and up_est.direction == DIRECTION_UP)
check("compute_auto_fibo: UP -> swing_high/swing_low match the fixture",
      up_est.swing_high == 120.0 and up_est.swing_low == 90.0)
_rng = 120.0 - 90.0
check("compute_auto_fibo: UP -> entry_near = swing_high - range*0.618",
      abs(up_est.entry_near - (120.0 - _rng * 0.618)) < 1e-9)
check("compute_auto_fibo: UP -> entry_deep = swing_high - range*0.786",
      abs(up_est.entry_deep - (120.0 - _rng * 0.786)) < 1e-9)
check("compute_auto_fibo: UP -> ext_target = swing_low + range*1.272",
      abs(up_est.ext_target - (90.0 + _rng * 1.272)) < 1e-9)
check("compute_auto_fibo: UP -> zone_lo/zone_hi correctly ordered",
      up_est.zone_lo == min(up_est.entry_near, up_est.entry_deep) and
      up_est.zone_hi == max(up_est.entry_near, up_est.entry_deep))
check("compute_auto_fibo: in_zone() true at the zone midpoint",
      up_est.in_zone((up_est.zone_lo + up_est.zone_hi) / 2))
check("compute_auto_fibo: in_zone() false far outside the zone",
      not up_est.in_zone(up_est.zone_hi + 1000))
check("compute_auto_fibo: in_zone() tolerance widens the zone",
      up_est.in_zone(up_est.zone_hi + 5, tolerance=10) and
      not up_est.in_zone(up_est.zone_hi + 5, tolerance=0))

# ── compute_auto_fibo(): swing LOW more recent -> DOWN (SELL zone) ─────
# 100 -> 110 (swing high, further back) -> 70 (swing low, more recent).
down_df = make_df([100, 110, 70, 75], seg_len=10)
down_est = compute_auto_fibo(down_df, length=100)
check("compute_auto_fibo: swing low more recent -> DIRECTION_DOWN",
      down_est is not None and down_est.direction == DIRECTION_DOWN)
_rng2 = 110.0 - 70.0
check("compute_auto_fibo: DOWN -> entry_near = swing_low + range*0.618",
      abs(down_est.entry_near - (70.0 + _rng2 * 0.618)) < 1e-9)
check("compute_auto_fibo: DOWN -> ext_target = swing_high - range*1.272",
      abs(down_est.ext_target - (110.0 - _rng2 * 1.272)) < 1e-9)

# ── compute_auto_fibo(): length actually limits the rolling window ─────
# A short length should "forget" an old extreme outside the window.
long_df = make_df([100, 200, 90, 95, 96, 97, 98], seg_len=10)  # old high 200 way back, recent range 90-98
short_window_est = compute_auto_fibo(long_df, length=25)
check("compute_auto_fibo: short length ignores an old out-of-window extreme",
      short_window_est is not None and short_window_est.swing_high < 200.0)

# ── custom golden/ext levels are respected ──────────────────────────────
custom_est = compute_auto_fibo(up_df, length=100, golden_low=0.5, golden_high=0.5, ext_level=2.0)
check("compute_auto_fibo: custom golden_low==golden_high collapses zone to a point",
      abs(custom_est.entry_near - custom_est.entry_deep) < 1e-9)
check("compute_auto_fibo: custom ext_level=2.0 applied",
      abs(custom_est.ext_target - (90.0 + _rng * 2.0)) < 1e-9)


# ═══════════════════════════════════════════════════════════════════════
# trend_monitor.py wiring (display-only, opt-in)
# ═══════════════════════════════════════════════════════════════════════
import trend_monitor as tm

# Reuse a long-enough uptrend fixture so calc_tf_trend/analyze_trend don't
# bail out on the len(df) < 50 guard.
BIG_UP_DF = make_df([100, 90, 120, 115], seg_len=20)   # 61 bars
check("fixture: BIG_UP_DF long enough for analyze_trend (>=50 bars)", len(BIG_UP_DF) >= 50)

tm.AUTO_FIBO_ENABLED = False
tr_off = tm.analyze_trend(BIG_UP_DF, BIG_UP_DF, BIG_UP_DF, symbol="TEST")
check("trend_monitor: flag OFF -> TrendResult.auto_fibo is None",
      tr_off.auto_fibo is None)
msg_off = tm.format_trend_message(tr_off)
check("trend_monitor: flag OFF -> message omits Est. Entry lines",
      "Est. Entry" not in msg_off)

tm.AUTO_FIBO_ENABLED = True
tr_on = tm.analyze_trend(BIG_UP_DF, BIG_UP_DF, BIG_UP_DF, symbol="TEST")
check("trend_monitor: flag ON -> TrendResult.auto_fibo is populated",
      tr_on.auto_fibo is not None)
msg_on = tm.format_trend_message(tr_on)
check("trend_monitor: flag ON -> message includes Est. Entry line",
      "Est. Entry" in msg_on)
check("trend_monitor: flag ON -> UP-swing fixture labeled BUY zone",
      "BUY zone" in msg_on)
check("trend_monitor: flag ON does not change bias/action computation",
      tr_on.bias == tr_off.bias and tr_on.action == tr_off.action)
tm.AUTO_FIBO_ENABLED = False  # restore default for any later import reuse


# ═══════════════════════════════════════════════════════════════════════
# signal_engine.py wiring: resolve_auto_fibo_filter() (opt-in gate)
# ═══════════════════════════════════════════════════════════════════════
from signal_engine import resolve_auto_fibo_filter
from auto_fibo_entry import AutoFiboEstimate

up_estimate = AutoFiboEstimate(
    direction=DIRECTION_UP, swing_high=120.0, swing_low=90.0,
    entry_near=100.0, entry_deep=95.0, ext_target=126.0,
    zone_lo=95.0, zone_hi=100.0,
)
down_estimate = AutoFiboEstimate(
    direction=DIRECTION_DOWN, swing_high=110.0, swing_low=70.0,
    entry_near=100.0, entry_deep=105.0, ext_target=59.0,
    zone_lo=100.0, zone_hi=105.0,
)

# Flag OFF (default): always proceeds, regardless of estimate/price.
check("resolve_auto_fibo_filter: flag OFF -> always True even with None estimate",
      resolve_auto_fibo_filter("BUY", 50.0, None, 1.0, enabled=False, tolerance_atr=0.5) is True)
check("resolve_auto_fibo_filter: flag OFF -> always True even far outside the zone",
      resolve_auto_fibo_filter("BUY", 9999.0, up_estimate, 1.0, enabled=False, tolerance_atr=0.5) is True)

# Flag ON, no estimate available (not enough history yet): fails open.
check("resolve_auto_fibo_filter: flag ON, estimate=None -> True (fails open, no data to gate on)",
      resolve_auto_fibo_filter("BUY", 50.0, None, 1.0, enabled=True, tolerance_atr=0.5) is True)

# Flag ON, direction mismatch: blocked regardless of price.
check("resolve_auto_fibo_filter: flag ON, BUY vs DOWN-swing estimate -> blocked",
      resolve_auto_fibo_filter("BUY", 102.0, down_estimate, 1.0, enabled=True, tolerance_atr=0.5) is False)
check("resolve_auto_fibo_filter: flag ON, SELL vs UP-swing estimate -> blocked",
      resolve_auto_fibo_filter("SELL", 97.0, up_estimate, 1.0, enabled=True, tolerance_atr=0.5) is False)

# Flag ON, direction matches, price inside zone -> proceeds.
check("resolve_auto_fibo_filter: flag ON, BUY + UP-swing + price in zone -> True",
      resolve_auto_fibo_filter("BUY", 97.5, up_estimate, 1.0, enabled=True, tolerance_atr=0.5) is True)
check("resolve_auto_fibo_filter: flag ON, SELL + DOWN-swing + price in zone -> True",
      resolve_auto_fibo_filter("SELL", 102.5, down_estimate, 1.0, enabled=True, tolerance_atr=0.5) is True)

# Flag ON, direction matches, price outside zone and outside tolerance -> blocked.
check("resolve_auto_fibo_filter: flag ON, BUY + UP-swing + price far outside zone -> blocked",
      resolve_auto_fibo_filter("BUY", 50.0, up_estimate, 1.0, enabled=True, tolerance_atr=0.5) is False)

# Flag ON, direction matches, price just outside zone but within ATR tolerance -> proceeds.
# zone_hi=100.0, atr=2.0, tolerance_atr=0.5 -> tolerance=1.0 -> zone effectively [94, 101]
check("resolve_auto_fibo_filter: flag ON, price just outside zone but within ATR tolerance -> True",
      resolve_auto_fibo_filter("BUY", 100.8, up_estimate, 2.0, enabled=True, tolerance_atr=0.5) is True)
check("resolve_auto_fibo_filter: flag ON, price outside zone AND outside tolerance -> blocked",
      resolve_auto_fibo_filter("BUY", 105.0, up_estimate, 2.0, enabled=True, tolerance_atr=0.5) is False)


# ═══════════════════════════════════════════════════════════════════════
# signal_engine.py: CloudSignal always carries the Estimate Entry fields,
# with sane empty defaults when auto_fibo couldn't be computed.
# ═══════════════════════════════════════════════════════════════════════
from signal_engine import CloudSignal, signal_to_dict

blank_sig = CloudSignal(
    action="OPEN", direction="BUY", signal_type="V4_SESSION",
    entry=100.0, sl=98.0, be_price=100.1, trail_from=100.0, tp_final=105.0,
    partial=[], pattern="", score=5, context_adj=0, final_score=5,
    layer=1, session="London", timestamp="", fallback_sl=98.0, fallback_tp=105.0,
)
check("CloudSignal: auto_fibo_* fields default to empty/0.0 when unset",
      blank_sig.auto_fibo_direction == "" and blank_sig.auto_fibo_entry_zone_lo == 0.0)
d = signal_to_dict(blank_sig)
check("signal_to_dict: includes all four auto_fibo_* keys",
      all(k in d for k in (
          "auto_fibo_direction", "auto_fibo_entry_zone_lo",
          "auto_fibo_entry_zone_hi", "auto_fibo_ext_target",
      )))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Auto Fibo Estimate Entry regression checks passed.")
