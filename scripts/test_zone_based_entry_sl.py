#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL flag
(resolve_zone_based_entry_sl() + its wiring into compute_signal()'s
Entry/SL construction).

Root cause: Entry was always "live price at the moment the score/gate
cascade cleared" and SL was always "Entry +/- a flat ATR(14,15m) buffer" --
neither ever looked at the actual Fib/PRZ zone (fib_zone) the setup was
scored against, or the structure-based reaction-candle SL already computed
by detect_h1_spike_at_kivanc() (spike["sl"]). In practice, by the time every
gate passed, price had often already run past the zone edge, so the real
support/resistance ended up sitting near the reported SL instead of the
reported Entry.

Run: python3 scripts/test_zone_based_entry_sl.py
Exits non-zero on any failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from signal_engine import resolve_zone_based_entry_sl

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


NO_SPIKE = {"found": False, "sl": 0, "tp1": 0, "volume_confirmed": False}
BUY_ZONE = {"prz_low": 100.0, "prz_high": 102.0}
SELL_ZONE = {"prz_low": 100.0, "prz_high": 102.0}


# ── Flag OFF (default): must be byte-identical to old behavior ─────────
entry, sl = resolve_zone_based_entry_sl("BUY", 105.0, BUY_ZONE, NO_SPIKE, enabled=False)
check("flag OFF: entry_price == price unchanged (BUY, price outside zone)",
      entry == 105.0)
check("flag OFF: zone_sl is None (falls back to ATR)", sl is None)

entry, sl = resolve_zone_based_entry_sl("SELL", 95.0, SELL_ZONE, NO_SPIKE, enabled=False)
check("flag OFF: entry_price == price unchanged (SELL)", entry == 95.0)
check("flag OFF: zone_sl is None even with a zone present", sl is None)

# ── Flag ON, no fib_zone available: falls back cleanly ──────────────────
entry, sl = resolve_zone_based_entry_sl("BUY", 105.0, None, NO_SPIKE, enabled=True)
check("flag ON, fib_zone=None: entry_price == price (nothing to clamp to)",
      entry == 105.0)
check("flag ON, fib_zone=None: zone_sl is None", sl is None)

# ── Flag ON, price already inside the zone: no change to entry ─────────
entry, sl = resolve_zone_based_entry_sl("BUY", 101.0, BUY_ZONE, NO_SPIKE, enabled=True)
check("flag ON: price already inside zone -> entry_price unchanged",
      entry == 101.0)

# ── Flag ON, price ran past the zone: entry clamps to the near edge ────
entry, sl = resolve_zone_based_entry_sl("BUY", 110.0, BUY_ZONE, NO_SPIKE, enabled=True)
check("flag ON: BUY price above zone -> entry clamps down to prz_high",
      entry == 102.0)

entry, sl = resolve_zone_based_entry_sl("SELL", 90.0, SELL_ZONE, NO_SPIKE, enabled=True)
check("flag ON: SELL price below zone -> entry clamps up to prz_low",
      entry == 100.0)

entry, sl = resolve_zone_based_entry_sl("BUY", 50.0, BUY_ZONE, NO_SPIKE, enabled=True)
check("flag ON: BUY price way below zone -> entry clamps up to prz_low",
      entry == 100.0)

# ── Flag ON, spike SL available and on the correct side of entry ───────
spike_buy = {"found": True, "sl": 99.0, "tp1": 110.0, "volume_confirmed": True}
entry, sl = resolve_zone_based_entry_sl("BUY", 110.0, BUY_ZONE, spike_buy, enabled=True)
check("flag ON: BUY entry clamps to zone edge", entry == 102.0)
check("flag ON: BUY uses spike['sl'] (below entry) instead of ATR buffer",
      sl == 99.0)

spike_sell = {"found": True, "sl": 103.0, "tp1": 90.0, "volume_confirmed": True}
entry, sl = resolve_zone_based_entry_sl("SELL", 90.0, SELL_ZONE, spike_sell, enabled=True)
check("flag ON: SELL entry clamps to zone edge", entry == 100.0)
check("flag ON: SELL uses spike['sl'] (above entry) instead of ATR buffer",
      sl == 103.0)

# ── Flag ON, spike SL lands on the wrong side of entry -> ignored ──────
bad_spike_buy = {"found": True, "sl": 103.0, "tp1": 110.0, "volume_confirmed": True}
entry, sl = resolve_zone_based_entry_sl("BUY", 110.0, BUY_ZONE, bad_spike_buy, enabled=True)
check("flag ON: spike['sl'] above BUY entry is rejected -> falls back to None (ATR)",
      sl is None)

bad_spike_sell = {"found": True, "sl": 99.0, "tp1": 90.0, "volume_confirmed": True}
entry, sl = resolve_zone_based_entry_sl("SELL", 90.0, SELL_ZONE, bad_spike_sell, enabled=True)
check("flag ON: spike['sl'] below SELL entry is rejected -> falls back to None (ATR)",
      sl is None)

# ── Flag ON, spike not found -> no zone_sl regardless of zone ──────────
entry, sl = resolve_zone_based_entry_sl("BUY", 101.0, BUY_ZONE, NO_SPIKE, enabled=True)
check("flag ON: spike not found -> zone_sl is None", sl is None)

# ── Degenerate zone (prz_high <= prz_low) is ignored, not applied ──────
degenerate_zone = {"prz_low": 102.0, "prz_high": 100.0}
entry, sl = resolve_zone_based_entry_sl("BUY", 110.0, degenerate_zone, NO_SPIKE, enabled=True)
check("flag ON: degenerate zone (hi <= lo) -> entry falls back to raw price",
      entry == 110.0)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All zone-based-entry-SL regression checks passed.")
