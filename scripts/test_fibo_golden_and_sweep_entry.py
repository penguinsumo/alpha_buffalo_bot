#!/usr/bin/env python3
"""
Regression tests for two new opt-in entry/SL/TP helpers in signal_engine.py:

1. ALPHA_SIGNAL_FIBO_GOLDEN_MODE -- resolve_fibo_golden_number() +
   resolve_fibo_sl_tp(). Root cause: the existing Kivanc Golden Zone check
   (kivanc_in_golden) accepts price ANYWHERE inside the 61.8%-78.6% band
   (16.8% of the swing wide) as "golden zone" evidence, and SL/TP for that
   evidence is still a flat ATR buffer / BB-PDH targets unrelated to the
   swing. A real Fibonacci "golden number" is one of the two specific
   ratios (61.8% or 78.6%), not an arbitrary band between them, and SL/TP
   for a real golden-ratio entry should come from the SAME swing's other
   named ratios (88.6% SL, 127.2%/161.8% extension TPs), not ATR/BB.

2. ALPHA_SIGNAL_SWEEP_WICK_ENTRY -- resolve_sweep_wick_entry(). Root cause:
   when the qualifying evidence for a signal is a liquidity sweep
   (sweep_valid), the sweep candle's own wick tip is the real point
   liquidity was taken -- a more meaningful entry anchor than wherever
   price has drifted to by the time every later gate clears, mirroring the
   same idea already used for detect_h1_spike_at_kivanc's SL.

Both are pure-function unit tests (same pattern as
test_zone_based_entry_sl.py) -- no synthetic OHLCV fixtures needed.

Run: python3 scripts/test_fibo_golden_and_sweep_entry.py
Exits non-zero on any failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import (
    resolve_fibo_golden_number,
    resolve_fibo_sl_tp,
    resolve_sweep_wick_entry,
    FIBO_GOLDEN_ZONE_LEVELS,
)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ═══════════════════════════════════════════════════════════
# resolve_fibo_golden_number
# ═══════════════════════════════════════════════════════════

SWING_HIGH = 4514.11331
SWING_LOW  = 4368.52912
RNG        = SWING_HIGH - SWING_LOW  # 145.58419

check("FIBO_GOLDEN_ZONE_LEVELS is (0.618, 0.786) -- the project's established golden zone bounds",
      FIBO_GOLDEN_ZONE_LEVELS == (0.618, 0.786))

# SELL: levels measured DOWN from swing_high
level_618_sell = SWING_HIGH - RNG * 0.618   # 4424.14...
level_786_sell = SWING_HIGH - RNG * 0.786   # 4399.68...

matched, lv, lv_price = resolve_fibo_golden_number(level_618_sell, SWING_HIGH, SWING_LOW, "SELL")
check("SELL: price exactly at 61.8% -> matched, level=0.618",
      matched and lv == 0.618 and abs(lv_price - level_618_sell) < 1e-6)

matched, lv, lv_price = resolve_fibo_golden_number(level_786_sell, SWING_HIGH, SWING_LOW, "SELL")
check("SELL: price exactly at 78.6% -> matched, level=0.786",
      matched and lv == 0.786)

# Within default 2% tolerance of 61.8% (2% of range ~= 2.91)
matched, lv, _ = resolve_fibo_golden_number(level_618_sell + 2.0, SWING_HIGH, SWING_LOW, "SELL",
                                             tolerance_pct=0.02)
check("SELL: price 2.0 away from 61.8% (within 2% tolerance ~2.91) -> matched",
      matched and lv == 0.618)

# The reported real-world case: price at 58.44% (short of 61.8% by 3.36pp,
# i.e. ~4.90 price points) is OUTSIDE default 2% tolerance -> must NOT match
price_5844pct = SWING_HIGH - RNG * 0.5844
matched, lv, _ = resolve_fibo_golden_number(price_5844pct, SWING_HIGH, SWING_LOW, "SELL",
                                             tolerance_pct=0.02)
check("SELL: price at 58.44% (short of the zone) does NOT match within 2% tolerance",
      not matched)

# Comfortably inside the band but NOT close to either named level (e.g. 70%,
# roughly the midpoint of 61.8-78.6%) must NOT match -- this is exactly the
# gap the golden-number filter closes vs. the old plain band-membership
# check (70% is ~8.2pp / ~11.9 price points from 61.8%, ~8.6pp/~12.5 from
# 78.6% -- well outside the default 2% tolerance either way).
price_70pct = SWING_HIGH - RNG * 0.70
matched, lv, _ = resolve_fibo_golden_number(price_70pct, SWING_HIGH, SWING_LOW, "SELL",
                                             tolerance_pct=0.02)
check("SELL: price at 70% (inside the OLD 61.8-78.6% band, but not near either "
      "named golden number) does NOT match -- this is the fix vs. band membership",
      not matched)

# A point just 2.2pp outside the default 2% tolerance (~3.2 price points
# from 61.8%) should still NOT match at the default, but DOES match once
# tolerance is widened past that distance (3%).
price_64pct = SWING_HIGH - RNG * 0.64
matched, lv, _ = resolve_fibo_golden_number(price_64pct, SWING_HIGH, SWING_LOW, "SELL",
                                             tolerance_pct=0.02)
check("SELL: price at 64% (2.2pp from 61.8%) does NOT match under default 2% tolerance",
      not matched)
matched, lv, _ = resolve_fibo_golden_number(price_64pct, SWING_HIGH, SWING_LOW, "SELL",
                                             tolerance_pct=0.03)
check("SELL: same 64% point DOES match once tolerance widens to 3%",
      matched and lv == 0.618)

# BUY mirrors: levels measured UP from swing_low
level_618_buy = SWING_LOW + RNG * 0.618
matched, lv, lv_price = resolve_fibo_golden_number(level_618_buy, SWING_HIGH, SWING_LOW, "BUY")
check("BUY: price exactly at 61.8% up from swing_low -> matched, level=0.618",
      matched and lv == 0.618 and abs(lv_price - level_618_buy) < 1e-6)

# Degenerate swing (range <= 0) never matches, never raises
matched, lv, lv_price = resolve_fibo_golden_number(100.0, 100.0, 100.0, "SELL")
check("degenerate swing (high == low) -> no match, no crash",
      matched is False and lv is None and lv_price is None)

matched, lv, lv_price = resolve_fibo_golden_number(100.0, 90.0, 100.0, "SELL")
check("inverted swing (high < low) -> no match, no crash",
      matched is False)


# ═══════════════════════════════════════════════════════════
# resolve_fibo_sl_tp
# ═══════════════════════════════════════════════════════════

sl, tp1, tp2 = resolve_fibo_sl_tp("SELL", SWING_HIGH, SWING_LOW)
expected_sl  = round(SWING_HIGH - RNG * 0.5, 2)
expected_tp1 = round(SWING_HIGH - RNG * 1.272, 2)
expected_tp2 = round(SWING_HIGH - RNG * 1.618, 2)
check("SELL fibo SL/TP: SL at 50% (the standard level just before/shallower "
      "than the zone's near edge, 61.8%)", sl == expected_sl)
check("SELL fibo SL/TP: TP1 at 127.2% extension", tp1 == expected_tp1)
check("SELL fibo SL/TP: TP2 at 161.8% (Golden Ratio) extension", tp2 == expected_tp2)
check("SELL fibo SL/TP: SL sits ABOVE the zone's near edge (61.8%) -- SL must be "
      "above entry for a SELL, invalidated if the 'shallow pullback' isn't shallow",
      sl > (SWING_HIGH - RNG * 0.618))
check("SELL fibo SL/TP: TP1/TP2 sit BELOW swing_low (continuation past the swing)",
      tp1 < SWING_LOW and tp2 < tp1)

sl, tp1, tp2 = resolve_fibo_sl_tp("BUY", SWING_HIGH, SWING_LOW)
expected_sl  = round(SWING_LOW + RNG * 0.5, 2)
expected_tp1 = round(SWING_LOW + RNG * 1.272, 2)
expected_tp2 = round(SWING_LOW + RNG * 1.618, 2)
check("BUY fibo SL/TP: SL at 50% (the standard level just before/shallower "
      "than the zone's near edge, 61.8%) up from swing_low", sl == expected_sl)
check("BUY fibo SL/TP: TP1 at 127.2% extension", tp1 == expected_tp1)
check("BUY fibo SL/TP: TP2 at 161.8% (Golden Ratio) extension", tp2 == expected_tp2)
check("BUY fibo SL/TP: SL sits BELOW the zone's near edge (61.8%) -- SL must be "
      "below entry for a BUY, invalidated if the 'shallow pullback' isn't shallow",
      sl < (SWING_LOW + RNG * 0.618))
check("BUY fibo SL/TP: TP1/TP2 sit ABOVE swing_high (continuation past the swing)",
      tp1 > SWING_HIGH and tp2 > tp1)


# ═══════════════════════════════════════════════════════════
# resolve_sweep_wick_entry
# ═══════════════════════════════════════════════════════════

entry, sl = resolve_sweep_wick_entry("SELL", sweep_valid=False, curr_high=4420.0, curr_low=4410.0)
check("no valid sweep -> (None, None), caller falls back to its own resolution",
      entry is None and sl is None)

entry, sl = resolve_sweep_wick_entry("SELL", sweep_valid=True, curr_high=4420.0, curr_low=4410.0)
check("SELL sweep: entry anchored at the wick tip (curr_high)", entry == 4420.0)
check("SELL sweep: SL sits just beyond the wick (curr_high + buffer)",
      sl == round(4420.0 + 0.30, 2))
check("SELL sweep: SL is above entry (correct side for a SELL stop)", sl > entry)

entry, sl = resolve_sweep_wick_entry("BUY", sweep_valid=True, curr_high=4420.0, curr_low=4410.0)
check("BUY sweep: entry anchored at the wick tip (curr_low)", entry == 4410.0)
check("BUY sweep: SL sits just beyond the wick (curr_low - buffer)",
      sl == round(4410.0 - 0.30, 2))
check("BUY sweep: SL is below entry (correct side for a BUY stop)", sl < entry)

entry, sl = resolve_sweep_wick_entry("SELL", sweep_valid=True, curr_high=4420.0, curr_low=4410.0,
                                      buffer=0.50)
check("custom buffer is respected", sl == round(4420.0 + 0.50, 2))


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All fibo-golden-number / sweep-wick-entry regression checks passed.")
