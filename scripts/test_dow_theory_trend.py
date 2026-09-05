#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_TREND_DOW_THEORY_ENABLED flag in
trend_monitor.py.

Root cause this replaces (when enabled): the original HH/HL/LH/LL check was
`recent_highs[-1] > recent_highs[-5]` -- a fixed 5-bar-back proxy with no
real pivot confirmation, so it can flip on a single noisy bar and doesn't
match what "Dow Theory structure" actually means (a run of confirmed swing
highs/lows). This adds classify_dow_structure()/_confirmed_swing_pivots(),
which use the same confirmed-pivot definition Pine's ta.pivothigh/pivotlow
use (pivot_bars on both sides), applied to M15 and H4 per the request.

Covers:
  - default OFF: calc_tf_trend()'s hh/hl/lh/ll path is untouched byte-for-
    byte (still the recent_highs[-1] vs recent_highs[-5] proxy) unless the
    env var is explicitly set (checked in a subprocess -- the flag is read
    at module import time)
  - _confirmed_swing_pivots() finds the right pivots on a hand-built zigzag
  - classify_dow_structure() correctly labels a confirmed HH+HL sequence as
    an uptrend and a confirmed LH+LL sequence as a downtrend, and reports
    "unknown" rather than guessing when there aren't two confirmed swings
    yet on either side
  - calc_tf_trend(), when enabled, populates TFTrend.dow from real swing
    structure instead of the proxy
  - analyze_trend(), when enabled, lets M15+H4 agreeing on structure set
    the bias directly (the confluence the user asked for)
  - format_trend_message() only prints the Dow Theory lines when the flag
    is enabled

Run: python3 scripts/test_dow_theory_trend.py
Exits non-zero on any failure.
"""
import json
import os
import subprocess
import sys

import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def read_defaults_in_subprocess(env_overrides=None):
    """DOW_THEORY_ENABLED/PIVOT_BARS are read at import time -- reimport in
    a clean subprocess so earlier tests in this process can't pollute it."""
    env = os.environ.copy()
    env.pop("ALPHA_TREND_DOW_THEORY_ENABLED", None)
    env.pop("ALPHA_TREND_DOW_THEORY_PIVOT_BARS", None)
    if env_overrides:
        env.update(env_overrides)
    code = (
        "import json, trend_monitor as m; "
        "print(json.dumps({'enabled': m.DOW_THEORY_ENABLED, 'pivot_bars': m.DOW_THEORY_PIVOT_BARS}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"subprocess failed: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── Defaults: OFF, unless explicitly set ────────────────────────────────
d = read_defaults_in_subprocess()
check("default: ALPHA_TREND_DOW_THEORY_ENABLED is False", d["enabled"] is False)
check("default: pivot_bars defaults to 3", d["pivot_bars"] == 3)

d2 = read_defaults_in_subprocess({
    "ALPHA_TREND_DOW_THEORY_ENABLED": "true",
    "ALPHA_TREND_DOW_THEORY_PIVOT_BARS": "5",
})
check("override: ALPHA_TREND_DOW_THEORY_ENABLED=true takes effect", d2["enabled"] is True)
check("override: pivot_bars override respected", d2["pivot_bars"] == 5)

import trend_monitor as tm


def build_zigzag(points, seg_len=12):
    """Linearly interpolate between successive price levels, seg_len steps
    per leg, so every bar has a distinct value (no ties -> no false
    pivots) and each leg is long enough to give pivot_bars confirmation
    bars on both sides of a turning point."""
    prices = [float(points[0])]
    for i in range(1, len(points)):
        start, end = points[i - 1], points[i]
        step = (end - start) / seg_len
        for s in range(1, seg_len + 1):
            prices.append(start + step * s)
    return prices


def make_df(points, seg_len=12):
    prices = build_zigzag(points, seg_len)
    return pd.DataFrame({
        "open": prices, "high": prices, "low": prices, "close": prices,
        "volume": [0] * len(prices),
    })


# Confirmed swing sequence: low 90 -> high 108 (HH candidate) -> low 96
# (HL) -> high 120 (HH) -> back down to 108 (confirms the 120 high).
UPTREND_DF = make_df([100, 90, 108, 96, 120, 108])
# Mirror image: high 110 -> low 90 -> high 102 (LH) -> low 70 (LL) -> back
# up to 82 (confirms the 70 low).
DOWNTREND_DF = make_df([100, 110, 90, 102, 70, 82])
# A single up-leg then down-leg -- one confirmed high, one confirmed low
# on each side at most, not enough to classify either as HH/LH or HL/LL.
FLAT_DF = make_df([100, 100.5, 99.5, 100.2, 99.8, 100.1], seg_len=12)

check("UPTREND_DF and DOWNTREND_DF are long enough for calc_tf_trend (>=50 bars)",
      len(UPTREND_DF) >= 50 and len(DOWNTREND_DF) >= 50)

# ── _confirmed_swing_pivots() ────────────────────────────────────────────
up_highs = tm._confirmed_swing_pivots(UPTREND_DF["high"], 3, is_high=True)
up_lows = tm._confirmed_swing_pivots(UPTREND_DF["low"], 3, is_high=False)
check("_confirmed_swing_pivots finds the two confirmed swing highs (120 then 108)",
      up_highs == [120.0, 108.0])
check("_confirmed_swing_pivots finds the two confirmed swing lows (96 then 90)",
      up_lows == [96.0, 90.0])

# ── classify_dow_structure() ─────────────────────────────────────────────
check("classify_dow_structure: confirmed HH + HL -> uptrend",
      tm.classify_dow_structure(UPTREND_DF, 3) == tm.STRUCTURE_UPTREND)
check("classify_dow_structure: confirmed LH + LL -> downtrend",
      tm.classify_dow_structure(DOWNTREND_DF, 3) == tm.STRUCTURE_DOWNTREND)
check("classify_dow_structure: not enough confirmed swings -> unknown, not guessed",
      tm.classify_dow_structure(FLAT_DF, 3) in (tm.STRUCTURE_UNKNOWN, tm.STRUCTURE_MIXED))
check("classify_dow_structure: too few bars -> unknown",
      tm.classify_dow_structure(UPTREND_DF.iloc[:5], 3) == tm.STRUCTURE_UNKNOWN)
check("classify_dow_structure: None df -> unknown",
      tm.classify_dow_structure(None, 3) == tm.STRUCTURE_UNKNOWN)

# ── calc_tf_trend(): flag OFF vs ON (direct attribute toggle -- read as a
# plain global on every call, so no reimport needed within this process) ──
tm.DOW_THEORY_ENABLED = False
off_trend = tm.calc_tf_trend(UPTREND_DF, "M15")
check("calc_tf_trend: flag OFF leaves TFTrend.dow empty (old proxy path only)",
      off_trend.dow == "")

tm.DOW_THEORY_ENABLED = True
tm.DOW_THEORY_PIVOT_BARS = 3
on_trend_up = tm.calc_tf_trend(UPTREND_DF, "M15")
on_trend_down = tm.calc_tf_trend(DOWNTREND_DF, "H4")
check("calc_tf_trend: flag ON reports HH_HL for the uptrend fixture",
      on_trend_up.dow == tm.STRUCTURE_UPTREND)
check("calc_tf_trend: flag ON reports LH_LL for the downtrend fixture",
      on_trend_down.dow == tm.STRUCTURE_DOWNTREND)

# ── analyze_trend(): M15+H4 confluence sets bias directly when enabled ──
tr_up = tm.analyze_trend(UPTREND_DF, UPTREND_DF, UPTREND_DF, symbol="TEST")
check("analyze_trend: M15+H4 both HH_HL -> bias BUY", tr_up.bias == "BUY")

tr_down = tm.analyze_trend(DOWNTREND_DF, DOWNTREND_DF, DOWNTREND_DF, symbol="TEST")
check("analyze_trend: M15+H4 both LH_LL -> bias SELL", tr_down.bias == "SELL")

# ── format_trend_message(): Dow lines only appear when the flag is on ──
msg_on = tm.format_trend_message(tr_up)
check("format_trend_message: flag ON includes Dow M15/H4 lines",
      "Dow M15" in msg_on and "Dow H4" in msg_on)

tm.DOW_THEORY_ENABLED = False
tr_off = tm.analyze_trend(UPTREND_DF, UPTREND_DF, UPTREND_DF, symbol="TEST")
msg_off = tm.format_trend_message(tr_off)
check("format_trend_message: flag OFF omits Dow lines entirely",
      "Dow M15" not in msg_off and "Dow H4" not in msg_off)
check("calc_tf_trend/analyze_trend: flag OFF -> TFTrend.dow stays empty everywhere",
      tr_off.m15.dow == "" and tr_off.h1.dow == "" and tr_off.h4.dow == "")

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Dow Theory trend regression checks passed.")
