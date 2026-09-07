#!/usr/bin/env python3
"""
Regression tests for sweep_reentry.py -- the opt-in Round-2 re-entry
watcher that fires after a Sweep Wick Entry (see signal_engine.py's
resolve_sweep_wick_entry / ALPHA_SIGNAL_SWEEP_WICK_ENTRY), per owner's
design (2026-09-07):

  1. fib_618_reference() -- 61.8% price of the original swing.
  2. resolve_reentry_sl() -- take whichever of (structure, fib 61.8%) is
     SAFER/further from price ("กันเหนียวทั้งสองทาง").
  3. find_pivot_since() -- confirmed Lower High (SELL) / Higher Low (BUY)
     pivot detection among CLOSED bars only.
  4. SweepReentryWatcher -- cross-poll state: register/check, one-shot
     firing, expiry, and invalidation.

Run: python3 scripts/test_sweep_reentry.py
Exits non-zero on any failure.
"""
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sweep_reentry import (
    fib_618_reference,
    resolve_reentry_sl,
    find_pivot_since,
    SweepReentryWatcher,
    PIVOT_N,
    MAX_WATCH_BARS,
)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def bars(rows):
    """rows: list of (open, high, low, close) tuples -> OHLCV DataFrame."""
    return pd.DataFrame(rows, columns=["open", "high", "low", "close"])


# ═══════════════════════════════════════════════════════════
# fib_618_reference
# ═══════════════════════════════════════════════════════════

SWING_HIGH = 4514.11331
SWING_LOW  = 4368.52912
RNG        = SWING_HIGH - SWING_LOW

ref = fib_618_reference("SELL", SWING_HIGH, SWING_LOW)
check("SELL: 61.8% measured down from swing_high", ref == round(SWING_HIGH - RNG * 0.618, 2))

ref = fib_618_reference("BUY", SWING_HIGH, SWING_LOW)
check("BUY: 61.8% measured up from swing_low", ref == round(SWING_LOW + RNG * 0.618, 2))

check("no swing (0/0) -> None, no crash", fib_618_reference("SELL", 0.0, 0.0) is None)
check("degenerate swing (high<=low) -> None, no crash", fib_618_reference("SELL", 100.0, 100.0) is None)
check("inverted swing -> None, no crash", fib_618_reference("SELL", 90.0, 100.0) is None)


# ═══════════════════════════════════════════════════════════
# resolve_reentry_sl -- "take the safer/wider of structure vs fib 61.8%"
# ═══════════════════════════════════════════════════════════

# SELL: structure_sl = break_level + buffer. Safer (wider) = the HIGHER
# of structure_sl and fib_ref -- further above price protects more.
sl = resolve_reentry_sl("SELL", pivot_break_level=100.0, fib_ref_price=105.0)
check("SELL: fib ref (105) is wider than structure (100.30) -> fib wins",
      sl == 105.0)

sl = resolve_reentry_sl("SELL", pivot_break_level=100.0, fib_ref_price=95.0)
check("SELL: structure (100.30) is wider than fib ref (95) -> structure wins",
      sl == round(100.0 + 0.30, 2))

sl = resolve_reentry_sl("SELL", pivot_break_level=100.0, fib_ref_price=None)
check("SELL: no fib ref -> structure-only fallback", sl == round(100.30, 2))

# BUY: structure_sl = break_level - buffer. Safer (wider) = the LOWER of
# structure_sl and fib_ref -- further below price protects more.
sl = resolve_reentry_sl("BUY", pivot_break_level=100.0, fib_ref_price=94.0)
check("BUY: fib ref (94) is wider than structure (99.70) -> fib wins", sl == 94.0)

sl = resolve_reentry_sl("BUY", pivot_break_level=100.0, fib_ref_price=99.9)
check("BUY: structure (99.70) is wider than fib ref (99.9) -> structure wins",
      sl == round(100.0 - 0.30, 2))

sl = resolve_reentry_sl("BUY", pivot_break_level=100.0, fib_ref_price=None)
check("BUY: no fib ref -> structure-only fallback", sl == round(99.70, 2))


# ═══════════════════════════════════════════════════════════
# find_pivot_since -- Lower High (SELL) / Higher Low (BUY) pivot detection
# ═══════════════════════════════════════════════════════════

# idx:    0    1    2    3    4     5    6    7(still-forming, excluded)
# high:  90   92   95  100  105   101   98   97
# low:   88   90   93   98  103    97   95   94
# A confirmed Lower High pivot sits at idx=4 (high=105, PIVOT_N=2 needs
# idx 2,3 before and 5,6 after all with a LOWER high -- 95,100,101,98 all
# < 105). break_level = low of idx4 = 103.
df_lh = bars([
    (89, 90, 88, 89.5),
    (91, 92, 90, 91.5),
    (94, 95, 93, 94.5),
    (99, 100, 98, 99.5),
    (104, 105, 103, 104.5),
    (100, 101, 97, 100.5),
    (97, 98, 95, 97.5),
    (96, 97, 94, 96.5),   # still-forming bar, excluded by df.iloc[:-1]
])
pivot_idx, pivot_price, break_level = find_pivot_since(df_lh, "SELL", since_idx=0, n=PIVOT_N)
check("SELL: Lower High pivot found at idx 4", pivot_idx == 4)
check("SELL: pivot price is the bar's high (105)", pivot_price == 105.0)
check("SELL: break_level is the pivot bar's LOW (103)", break_level == 103.0)

# Mirror for BUY: a Higher Low pivot.
df_hl = bars([
    (91, 92, 90, 91.5),
    (89, 90, 88, 89.5),
    (86, 87, 85, 86.5),
    (81, 82, 80, 81.5),
    (76, 77, 75, 76.5),
    (80, 81, 79, 80.5),
    (83, 84, 82, 83.5),
    (84, 85, 83, 84.5),   # still-forming, excluded
])
pivot_idx, pivot_price, break_level = find_pivot_since(df_hl, "BUY", since_idx=0, n=PIVOT_N)
check("BUY: Higher Low pivot found at idx 4", pivot_idx == 4)
check("BUY: pivot price is the bar's low (75)", pivot_price == 75.0)
check("BUY: break_level is the pivot bar's HIGH (77)", break_level == 77.0)

# Not enough bars after the candidate pivot yet -> no confirmed pivot.
df_too_short = bars([
    (89, 90, 88, 89.5),
    (91, 92, 90, 91.5),
    (94, 95, 93, 94.5),
    (99, 100, 98, 99.5),
    (104, 105, 103, 104.5),   # candidate high, but only 1 bar follows
    (96, 97, 94, 96.5),       # still-forming, excluded
])
pivot_idx, _, _ = find_pivot_since(df_too_short, "SELL", since_idx=0, n=PIVOT_N)
check("SELL: candidate pivot with too few confirming bars after it -> not confirmed",
      pivot_idx is None)

# since_idx restricts the search to bars formed AFTER a watch was armed --
# an earlier pivot before since_idx must not be picked up.
pivot_idx, _, _ = find_pivot_since(df_lh, "SELL", since_idx=5, n=PIVOT_N)
check("SELL: since_idx excludes an earlier pivot (armed after it formed)",
      pivot_idx is None)


# ═══════════════════════════════════════════════════════════
# SweepReentryWatcher -- cross-poll state machine
# ═══════════════════════════════════════════════════════════

# Case A: pivot confirmed AND a later bar closes below break_level (103)
# in the SAME pass -- fires immediately.
w = SweepReentryWatcher()
w.register(direction="SELL", trade1_entry=110.0, trade1_tp=90.0, fib_ref_price=106.0, formed_bar=0)
df_confirm = bars([
    (89, 90, 88, 89.5),
    (91, 92, 90, 91.5),
    (94, 95, 93, 94.5),
    (99, 100, 98, 99.5),
    (104, 105, 103, 104.5),  # Lower High pivot, break_level=103
    (100, 101, 97, 98.0),    # closes at 98 < 103 -> CONFIRMS Round 2
    (97, 98, 95, 96.5),
    (96, 97, 94, 95.5),      # still-forming, excluded
])
fired = w.check(df_confirm)
check("watcher: fires exactly one Round 2 when pivot+confirmation both present",
      len(fired) == 1)
if fired:
    r = fired[0]
    check("watcher: fired entry == break_level (103)", r["entry"] == 103.0)
    check("watcher: fired SL takes the safer(wider) of structure(103.30) vs fib(106) -> 106",
          r["sl"] == 106.0)
    check("watcher: fired TP carries Trade 1's TP forward unchanged", r["tp"] == 90.0)
    check("watcher: fired trade1_entry matches what was registered", r["trade1_entry"] == 110.0)
check("watcher: fired watch is removed (one-shot, not still pending)", len(w.pending) == 0)

# Case B: pivot confirmed but NO bar has closed below break_level yet --
# stays pending, does not fire.
w2 = SweepReentryWatcher()
w2.register(direction="SELL", trade1_entry=110.0, trade1_tp=90.0, fib_ref_price=106.0, formed_bar=0)
df_pending = bars([
    (89, 90, 88, 89.5),
    (91, 92, 90, 91.5),
    (94, 95, 93, 94.5),
    (99, 100, 98, 99.5),
    (104, 105, 103, 104.5),  # Lower High pivot, break_level=103
    (100, 101, 104, 104.5),  # closes at 104.5 -- still ABOVE break_level
    (97, 98, 103.5, 103.8),  # closes at 103.8 -- still ABOVE break_level
    (96, 97, 94, 96.5),      # still-forming, excluded
])
fired2 = w2.check(df_pending)
check("watcher: no bar has closed below break_level yet -> does not fire", fired2 == [])
check("watcher: watch stays pending (not dropped) while still valid", len(w2.pending) == 1)

# Case C: invalidation -- price closes back past the ORIGINAL sweep entry
# in the wrong direction before Round 2 confirms -> watch is dropped,
# never fires even if a pivot/break pattern exists later.
w3 = SweepReentryWatcher()
w3.register(direction="SELL", trade1_entry=100.0, trade1_tp=90.0, fib_ref_price=None, formed_bar=0)
df_invalid = bars([
    (93, 94, 92, 93.5),
    (94, 95, 93, 94.5),
    (95, 96, 94, 95.5),
    (97, 98, 96, 97.5),
    (99, 100, 98, 99.5),
    (100, 101, 99, 100.5),
    (102, 103, 101, 105.0),  # closes at 105 > trade1_entry(100) -- thesis dead
    (96, 97, 94, 95.5),      # still-forming, excluded
])
fired3 = w3.check(df_invalid)
check("watcher: invalidated (closed back past original entry) -> never fires",
      fired3 == [])
check("watcher: invalidated watch is dropped, not kept pending", len(w3.pending) == 0)

# Case D: expiry -- watch armed too many bars ago with no pivot ever
# forming -> dropped once MAX_WATCH_BARS is exceeded.
w4 = SweepReentryWatcher()
w4.register(direction="SELL", trade1_entry=200.0, trade1_tp=190.0, fib_ref_price=None, formed_bar=0)
long_rows = [(95 + i * 0.01, 96 + i * 0.01, 94 + i * 0.01, 95.5 + i * 0.01)
             for i in range(MAX_WATCH_BARS + 5)]
df_expired = bars(long_rows)
fired4 = w4.check(df_expired)
check("watcher: expired watch (no pivot within MAX_WATCH_BARS) -> never fires, dropped",
      fired4 == [] and len(w4.pending) == 0)

# BUY mirror end-to-end: pivot + confirmation fires with correct SL side.
w5 = SweepReentryWatcher()
w5.register(direction="BUY", trade1_entry=70.0, trade1_tp=95.0, fib_ref_price=74.0, formed_bar=0)
df_buy_confirm = bars([
    (91, 92, 90, 91.5),
    (89, 90, 88, 89.5),
    (86, 87, 85, 86.5),
    (81, 82, 80, 81.5),
    (76, 77, 75, 76.5),   # Higher Low pivot, break_level=77 (bar's high)
    (80, 81, 79, 82.0),   # closes at 82 > 77 -> CONFIRMS Round 2
    (83, 84, 82, 83.5),
    (84, 85, 83, 84.5),   # still-forming, excluded
])
fired5 = w5.check(df_buy_confirm)
check("BUY watcher: fires exactly one Round 2", len(fired5) == 1)
if fired5:
    r = fired5[0]
    check("BUY watcher: entry == break_level (77)", r["entry"] == 77.0)
    check("BUY watcher: SL takes safer(wider) of structure(76.70) vs fib(74) -> 74",
          r["sl"] == 74.0)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All sweep_reentry regression checks passed.")
