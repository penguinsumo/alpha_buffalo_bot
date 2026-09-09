#!/usr/bin/env python3
"""
Regression tests for the H1-vs-H4 harmonic PRZ fix (9 ก.ย. 2026).

Owner's report: "สิ่งที่ระบบต้องหาคือ harmonic ระดับ h1 กับ h4 ที่ต่างกัน"
(what the system needs to find is H1-level vs H4-level harmonic, which are
different from each other).

Root cause: scan_harmonic(df_1h, df_4h) genuinely scanned H1 and H4
independently, but merged the results into ONE undifferentiated list:
  - No record of which timeframe a match came from.
  - A flat $3.00 PRZ buffer (PRZ_BUFFER) around D regardless of timeframe
    (or symbol -- this same function also runs for BTCUSD/US100/JPN225 via
    the extra-symbol scan, where $3.00 is a meaningless buffer at very
    different price scales).
  - Final ordering (which match compute_signal() actually uses -- the
    first hit in this list) was sorted only by pattern-type priority, with
    H1 silently beating H4 at equal priority purely because df_1h came
    first in the old `for df in [df_1h, df_4h]` loop -- never a deliberate
    "H1 outweighs H4" design choice. Elsewhere in this same file
    (kivanc_score_raw) H4 evidence already outweighs H1 (+3 vs +2) --
    scan_harmonic() just never matched that existing convention.

Fix, covered below:
  1. Each result dict now carries tf ("H1"/"H4"). Folded into prz_name at
     the match site in compute_signal() ("{name} ({tf})") so it's visible
     everywhere prz_name already flows: validate_scenario()'s pattern arg,
     the Telegram alert's Pattern line (format_signal_message), CloudSignal
     .pattern, and signal_log's pattern column.
  2. PRZ buffer is now ATR-scaled per timeframe (_tf_atr / _prz_buffer_for)
     instead of the flat PRZ_BUFFER=3.0 -- naturally wider for H4 than H1,
     and naturally correct per-symbol too. Falls back to the flat 3.0 only
     when ATR can't be computed (too little/degenerate data).
  3. Final sort: pattern-type priority still comes first, but within the
     same priority tier H4 now ranks ahead of H1.

Run: python3 scripts/test_harmonic_h1_h4_tf.py
Exits non-zero on any failure.
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

import signal_engine as se
from signal_engine import scan_harmonic, _tf_atr, _prz_buffer_for, PRZ_BUFFER

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ═══════════════════════════════════════════════════════════
# 1. _tf_atr() -- per-timeframe average high-low range
# ═══════════════════════════════════════════════════════════

check("_tf_atr: None df -> 0.0", _tf_atr(None) == 0.0)
check("_tf_atr: too-short df (len<2) -> 0.0",
      _tf_atr(pd.DataFrame({"high": [1.0], "low": [1.0]})) == 0.0)

flat_df = pd.DataFrame({"high": [100.0] * 20, "low": [100.0] * 20})
check("_tf_atr: degenerate flat df (high==low every bar) -> 0.0",
      _tf_atr(flat_df) == 0.0)

wicked_df = pd.DataFrame({
    "high": [100.0 + 2.0] * 20,
    "low":  [100.0 - 2.0] * 20,
})
check("_tf_atr: constant 4.0-wide bars over 20 bars -> 4.0",
      abs(_tf_atr(wicked_df) - 4.0) < 1e-9)


# ═══════════════════════════════════════════════════════════
# 2. _prz_buffer_for() -- ATR-scaled, flat-3.0 fallback
# ═══════════════════════════════════════════════════════════

check("_prz_buffer_for: real ATR present -> atr * PRZ_BUFFER_ATR_MULT "
      "(default mult 0.5) -- 4.0 ATR -> 2.0 buffer",
      abs(_prz_buffer_for(wicked_df) - 4.0 * se.PRZ_BUFFER_ATR_MULT) < 1e-9)
check("_prz_buffer_for: degenerate/no ATR -> falls back to flat PRZ_BUFFER",
      _prz_buffer_for(flat_df) == PRZ_BUFFER)
check("_prz_buffer_for: None df -> falls back to flat PRZ_BUFFER",
      _prz_buffer_for(None) == PRZ_BUFFER)

# Custom multiplier is respected (module-level constant, same pattern as
# other ALPHA_*-driven module constants elsewhere in this test suite).
old_mult = se.PRZ_BUFFER_ATR_MULT
se.PRZ_BUFFER_ATR_MULT = 1.0
check("_prz_buffer_for: custom PRZ_BUFFER_ATR_MULT=1.0 -> buffer == ATR exactly",
      abs(_prz_buffer_for(wicked_df) - 4.0) < 1e-9)
se.PRZ_BUFFER_ATR_MULT = old_mult


# ═══════════════════════════════════════════════════════════
# 3. scan_harmonic(): tf tag, per-timeframe buffer, tf-aware dedup, sort
# ═══════════════════════════════════════════════════════════

def make_harmonic_df(wick, seg_len=5):
    """A Bullish_ABCD (dir=BUY, AB/XA=0.618, CD/XA=1.272, priority=1) fixture,
    designed via the exact X/A/B/C/D SWING VALUES a real 5-point XABCD scan
    would read off "high"/"low" -- not the underlying "close" ramp -- so a
    nonzero `wick` (giving the fixture a controllable, nonzero per-bar
    high-low range for _tf_atr to measure) never perturbs the harmonic
    ratios themselves. X=200(L) A=300(H) B=238.2(L) C=269.1(H) D=141.9(L):
    XA=100, AB=61.8 (r_AB=0.618), CD=127.2 (r_CD=1.272) -- exact match.
    """
    X, A, B, C, D = 200.0, 300.0, 238.2, 269.1, 141.9
    # L points need close+wick == target; H points need close-wick == target
    # (uncritical lead-in/tail padding bracket the shape so X/D each get
    # >=PIVOT_N bars of monotonic run-up on both sides, and are never
    # themselves evaluated as pivot candidates -- outside find_pivots()'s
    # scanned index range).
    close_points = [220.0, X + wick, A - wick, B + wick, C - wick, D + wick, 155.0]
    closes = [close_points[0]]
    for i in range(1, len(close_points)):
        start, end = close_points[i - 1], close_points[i]
        step = (end - start) / seg_len
        for s in range(1, seg_len + 1):
            closes.append(start + step * s)
    highs = [c + wick for c in closes]
    lows = [c - wick for c in closes]
    return pd.DataFrame({
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [0] * len(closes),
    })


df_1h_fixture = make_harmonic_df(wick=0.3)   # ATR = 0.6 -> buffer = 0.3
df_4h_fixture = make_harmonic_df(wick=3.0)   # ATR = 6.0 -> buffer = 3.0

results = scan_harmonic(df_1h_fixture, df_4h_fixture)
# NOTE: Bullish_ABCD and Bearish_ABCD share the exact same AB/CD ratio
# definition in PATTERNS (only "dir" differs) -- an unrelated, pre-existing
# quirk of the ratio-matching itself (out of scope here), so this fixture's
# shape always matches BOTH labels on each timeframe. compute_signal()
# already handles this correctly downstream by filtering prz_list on
# prz["direction"]==signal direction, so only Bullish_ABCD (dir=BUY) is
# this test's actual target.
h1_hits = [r for r in results if r["tf"] == "H1" and r["name"] == "Bullish_ABCD"]
h4_hits = [r for r in results if r["tf"] == "H4" and r["name"] == "Bullish_ABCD"]

check("scan_harmonic: found the Bullish_ABCD match on H1", len(h1_hits) == 1)
check("scan_harmonic: found the Bullish_ABCD match on H4", len(h4_hits) == 1)

if h1_hits and h4_hits:
    h1, h4 = h1_hits[0], h4_hits[0]
    check("H1 match: correct name/direction/priority",
          h1["name"] == "Bullish_ABCD" and h1["direction"] == "BUY" and h1["priority"] == 1)
    check("H4 match: correct name/direction/priority",
          h4["name"] == "Bullish_ABCD" and h4["direction"] == "BUY" and h4["priority"] == 1)

    check("H1/H4 matches land on the same D (prz_mid ~= 141.9) -- genuinely "
          "the same swing shape on both timeframes, only volatility differs",
          abs(h1["prz_mid"] - 141.9) < 1e-6 and abs(h4["prz_mid"] - 141.9) < 1e-6)

    h1_width = h1["prz_high"] - h1["prz_low"]
    h4_width = h4["prz_high"] - h4["prz_low"]
    check("H4's PRZ buffer is wider than H1's (ATR-scaled, not the old flat "
          "$3.00 for both)", h4_width > h1_width)
    check("H4/H1 buffer ratio matches the fixtures' wick ratio (10x) -- "
          "buffer genuinely tracks each timeframe's own volatility",
          abs(h4_width / h1_width - 10.0) < 1e-6)

    check("scan_harmonic: dedup keeps BOTH the H1 and H4 Bullish_ABCD "
          "matches despite sharing the same rounded prz_mid -- different "
          "timeframes are different evidence, not duplicates",
          len(h1_hits) == 1 and len(h4_hits) == 1)

    # Same priority (1) on both -> H4 must sort ahead of H1 (bigger-picture
    # timeframe wins ties, matching kivanc_score_raw's existing H4>H1
    # weighting elsewhere in signal_engine.py).
    idx_h1 = results.index(h1)
    idx_h4 = results.index(h4)
    check("scan_harmonic: at equal pattern priority, H4 sorts BEFORE H1 "
          "(compute_signal() uses the FIRST match in this list)",
          idx_h4 < idx_h1)


# ═══════════════════════════════════════════════════════════
# 4. Source guard: compute_signal()'s match site folds tf into prz_name
# ═══════════════════════════════════════════════════════════

src = inspect.getsource(se.compute_signal)
check("compute_signal(): prz_name now includes the matched pattern's "
      "timeframe -- f'{prz[\"name\"]} ({prz[\"tf\"]})'",
      '''prz_name = f'{prz["name"]} ({prz["tf"]})' ''' .strip() in src)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All H1/H4 harmonic PRZ timeframe regression checks passed.")
