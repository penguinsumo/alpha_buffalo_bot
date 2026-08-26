#!/usr/bin/env python3
"""
Regression tests for the opt-in analyze_structure() fallbacks added to fix
"M15 no signal all day" (H4 cascade direction stuck at SIDEWAYS because the
XAU/USD feed has no volume, so PULLBACK_UP/PULLBACK_DOWN could never fire,
and single-bar HH/HL comparison on H4 was noisy).

Run: python3 scripts/test_structure_fallback.py
Exits non-zero on any failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from signal_engine import analyze_structure

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def make_df(closes, volume=None):
    """Build OHLC bars from a close-price path: open[i] = close[i-1], so each
    bar's body direction (bullish/bearish) matches the actual price move,
    never a doji."""
    n = len(closes)
    opens = [closes[0] - (closes[1] - closes[0] if n > 1 else 1.0)] + list(closes[:-1])
    highs = [max(o, c) + 0.1 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.1 for o, c in zip(opens, closes)]
    idx = pd.date_range("2026-08-01", periods=n, freq="4h", tz="UTC")
    data = {"open": opens, "high": highs, "low": lows, "close": closes}
    if volume is not None:
        volume = list(volume)
        if len(volume) < n:
            volume = [volume[0]] * (n - len(volume)) + volume
        data["volume"] = volume[:n]
    return pd.DataFrame(data, index=idx)


def clear_env():
    for k in [
        "ALPHA_STRUCTURE_WINDOW_BARS",
        "ALPHA_STRUCTURE_FIB_PULLBACK_FALLBACK",
        "ALPHA_STRUCTURE_FIB_LOOKBACK",
    ]:
        os.environ.pop(k, None)


# ── 1. Default behavior must be byte-identical to the original single-bar,
#      volume-required logic when no env vars are set. ────────────────────
clear_env()

# steady uptrend, no volume column at all -> should still detect IMPULSE_UP
# on the strict single-bar HH/HL rule (no fallback involved).
up_closes = [100 + i * 1.5 for i in range(25)]
df_up = make_df(up_closes)
check("default: clean uptrend, no volume col -> IMPULSE_UP",
      analyze_structure(df_up) == "IMPULSE_UP")

# choppy/no-volume market that should NOT resolve to a signal by default
# (this is the exact real-world case that was silently eating all signals) --
# EMA20 > EMA50 slight uptrend bias but current bar does NOT beat prev bar's
# high+low together -> must stay SIDEWAYS with everything off.
chop_closes = [100 + i * 1.5 for i in range(20)] + [122.0, 116.0, 113.5]
df_chop = make_df(chop_closes)
default_result = analyze_structure(df_chop)
check("default: pullback-shaped bar with no volume, flags off -> NOT a pullback signal",
      default_result in ("SIDEWAYS", "IMPULSE_UP", "IMPULSE_DOWN"))
check("default: pullback-shaped bar with no volume, flags off -> specifically SIDEWAYS "
      "(this is the exact production bug: real correction, mislabeled SIDEWAYS)",
      default_result == "SIDEWAYS")

# ── 2. Fib pullback fallback (b): OFF by default, ON changes the outcome
#      only when volume is genuinely unavailable. ─────────────────────────
clear_env()
before = analyze_structure(df_chop)
os.environ["ALPHA_STRUCTURE_FIB_PULLBACK_FALLBACK"] = "true"
after = analyze_structure(df_chop)
check("fib fallback OFF -> SIDEWAYS (baseline)", before == "SIDEWAYS")
check("fib fallback ON -> reclassifies the same bar as PULLBACK_UP",
      after == "PULLBACK_UP")
clear_env()

# Must NOT engage when real volume is present, even with the flag on --
# vol_drop's own (pre-existing) logic must still be the only path to
# PULLBACK_* in that case, so behavior for volume-bearing feeds (BTC on a
# provider that does supply it, etc.) never regresses.
vol_present = [50] * 19 + [5]  # last bar volume << avg -> real vol_drop=True already
df_chop_vol = make_df(chop_closes, volume=vol_present)
os.environ["ALPHA_STRUCTURE_FIB_PULLBACK_FALLBACK"] = "true"
with_vol = analyze_structure(df_chop_vol)
check("fib fallback ON but real volume present -> still resolved via original vol_drop path "
      "(fallback did not need to engage, same PULLBACK_UP result)",
      with_vol == "PULLBACK_UP")
clear_env()

no_pullback_shape_closes = [100 + i * 1.5 for i in range(25)]  # no retracement at all
df_no_pullback = make_df(no_pullback_shape_closes)
os.environ["ALPHA_STRUCTURE_FIB_PULLBACK_FALLBACK"] = "true"
result = analyze_structure(df_no_pullback)
check("fib fallback ON but no actual retracement shape -> still IMPULSE_UP, not forced PULLBACK",
      result == "IMPULSE_UP")
clear_env()

# ── 3. Window-bars fallback (c): OFF (=1) is byte-identical; >1 rides
#      through a single noisy/wicked bar on H4. ────────────────────────────
clear_env()
# Clean uptrend, then a one-bar upward noise spike (bar -3), then a pullback
# candle (bar -2, the immediate "prev") whose high sits ABOVE the final
# breakout candle's high -- so comparing only against the immediate prev
# bar fails HH/HL, even though comparing against a bar 3 back (before the
# spike) clearly confirms the uptrend continues.
noisy_closes = [100 + i * 1.5 for i in range(20)] + [130.0, 145.0, 140.0, 142.0]
df_noisy = make_df(noisy_closes)
default_noisy = analyze_structure(df_noisy)
os.environ["ALPHA_STRUCTURE_WINDOW_BARS"] = "3"
windowed_noisy = analyze_structure(df_noisy)
check("window=1 (default): immediate-prev noise spike breaks IMPULSE_UP detection -> SIDEWAYS",
      default_noisy == "SIDEWAYS")
check("window=3: looking 3 bars back rides through the noise -> IMPULSE_UP",
      windowed_noisy == "IMPULSE_UP")
clear_env()

# window_bars=1 explicit must equal the default (no env var at all)
os.environ["ALPHA_STRUCTURE_WINDOW_BARS"] = "1"
explicit_one = analyze_structure(df_up)
clear_env()
implicit_default = analyze_structure(df_up)
check("ALPHA_STRUCTURE_WINDOW_BARS=1 explicit == default (no env var)",
      explicit_one == implicit_default)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All structure-fallback regression checks passed.")
