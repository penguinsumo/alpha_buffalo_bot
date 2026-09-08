#!/usr/bin/env python3
"""
Regression tests for resolve_kivanc_minor_sl_fallback() -- the default-ON
fix for the flat ATR*1.0 SL fallback ignoring nearby M15 structure.

Root cause (reported live 2026-09-08): a V4_SESSION BUY signal (XAUUSD,
entry 4423.68) landed its flat-ATR SL at 4419.6 -- in FRONT of a minor
Kivanc swing low (M15, pivot_n=5) around 4415.7. A routine sweep down to
that minor support would take out the SL before the setup ever got a
chance to reverse. This fix widens the flat-ATR SL just beyond the minor
swing whenever the swing sits further out than the flat SL already does,
capped at max_atr_mult * atr so a stale/far swing can't blow the stop out.

Run: python3 scripts/test_kivanc_minor_sl_fallback.py
Exits non-zero on any failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import resolve_kivanc_minor_sl_fallback

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


ATR = 4.0  # ~ matches the real 2026-09-08 incident (entry-flat_sl == atr*1.0)

# ── The exact reported incident: BUY, minor swing low beyond flat SL ───
# entry=4423.68, flat_sl=entry-atr=4419.68, minor swing low=4415.70
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=4415.70, atr=ATR)
check("BUY: widens past minor swing low when it's beyond the flat SL",
      sl == round(4415.70 - 0.30, 2))

# ── SELL mirror: minor swing high beyond the flat SL ────────────────────
sl = resolve_kivanc_minor_sl_fallback(
    "SELL", 4423.68, 4427.68, swing_high=4431.70, swing_low=None, atr=ATR)
check("SELL: widens past minor swing high when it's beyond the flat SL",
      sl == round(4431.70 + 0.30, 2))

# ── Swing is CLOSER than flat SL (already tighter) -> never tightens ───
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=4421.00, atr=ATR)
check("BUY: swing closer than flat SL -> flat SL unchanged (never tightens)",
      sl == 4419.68)

sl = resolve_kivanc_minor_sl_fallback(
    "SELL", 4423.68, 4427.68, swing_high=4425.00, swing_low=None, atr=ATR)
check("SELL: swing closer than flat SL -> flat SL unchanged (never tightens)",
      sl == 4427.68)

# ── No swing available at all -> flat SL unchanged ──────────────────────
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=None, atr=ATR)
check("BUY: no swing -> flat SL unchanged", sl == 4419.68)

sl = resolve_kivanc_minor_sl_fallback(
    "SELL", 4423.68, 4427.68, swing_high=None, swing_low=None, atr=ATR)
check("SELL: no swing -> flat SL unchanged", sl == 4427.68)

# ── Swing beyond the max_atr_mult cap -> rejected, flat SL unchanged ────
# default max_atr_mult=2.5 -> max distance from entry = 10.0 (atr=4.0)
far_swing_low = 4423.68 - 20.0  # way beyond the cap
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=far_swing_low, atr=ATR)
check("BUY: swing beyond max_atr_mult cap -> rejected, flat SL unchanged",
      sl == 4419.68)

# ── Swing just inside the cap -> accepted ───────────────────────────────
near_cap_swing_low = 4423.68 - 9.5  # within the 10.0 cap
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=near_cap_swing_low, atr=ATR)
check("BUY: swing just inside max_atr_mult cap -> accepted",
      sl == round(near_cap_swing_low - 0.30, 2))

# ── Disabled (kill switch) -> always returns flat SL unchanged ─────────
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=4415.70, atr=ATR,
    enabled=False)
check("BUY: disabled (kill switch) -> flat SL unchanged even with a wide swing",
      sl == 4419.68)

sl = resolve_kivanc_minor_sl_fallback(
    "SELL", 4423.68, 4427.68, swing_high=4431.70, swing_low=None, atr=ATR,
    enabled=False)
check("SELL: disabled (kill switch) -> flat SL unchanged even with a wide swing",
      sl == 4427.68)

# ── atr<=0 (degenerate data) -> never widens, avoids div/logic issues ──
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=4415.70, atr=0.0)
check("BUY: atr<=0 -> flat SL unchanged", sl == 4419.68)

# ── Custom buffer/max_atr_mult are respected ────────────────────────────
sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=4415.70, atr=ATR,
    buffer=1.00)
check("BUY: custom buffer is applied", sl == round(4415.70 - 1.00, 2))

sl = resolve_kivanc_minor_sl_fallback(
    "BUY", 4423.68, 4419.68, swing_high=None, swing_low=far_swing_low, atr=ATR,
    max_atr_mult=10.0)
check("BUY: widening max_atr_mult accepts a swing previously beyond the cap",
      sl == round(far_swing_low - 0.30, 2))

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Kivanc-minor SL fallback regression checks passed.")
