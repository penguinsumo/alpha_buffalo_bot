#!/usr/bin/env python3
"""
Regression test for a confirmed bug (reported live 9 ก.ย. 2026, not opt-in):
compute_signal()'s Stage-3 "SESSION SIGNAL FIRING" alert (early_warning.
alert_signal_ready()) and signal_log persistence (signal_log.log_signal())
were both called with the raw `price` (the M15 close captured at the very
top of compute_signal(), before any entry adjustment) as "Entry" -- instead
of `entry_price`, the possibly zone/sweep-clamped entry that `sl` is
actually computed relative to, and that the real CloudSignal (sent as the
second "ALPHA BUFFALO V5" message and used for execution) correctly uses.

`price` and `entry_price` only diverge once an entry-adjustment feature is
enabled -- ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL (live in production since
8 ก.ย. 2026) or ALPHA_SIGNAL_SWEEP_WICK_ENTRY -- but once they do, the early
alert showed an internally-inconsistent Entry/SL pair even though the real
signal used for execution was fine throughout. Live incident (9 ก.ย. 2026,
XAUUSD BUY): raw price=4404.01, sl=4413.57 (correctly computed relative to
the real entry_price=~4420.02) -- sl appeared to sit ABOVE the alert's
displayed "Entry" (4404.01), which looks backwards for a BUY, even though
the real entry/SL pair used for the trade was entirely correct.

Fix: both call sites now pass entry_price instead of price.

Covers:
  1. The exact numbers from the reported incident, to pin the invariant
     that motivated the fix (sl sits on the correct side of entry_price,
     but on the WRONG side of the raw price it used to be paired with).
  2. Source guards on compute_signal(): the alert_signal_ready() call site
     passes entry_price (not price) as its Entry argument, and the
     log_signal() call site passes entry=entry_price (not entry=price).
     Full gate-cascade end-to-end testing of compute_signal() with
     synthetic OHLCV is deliberately avoided here, same as every other
     test file in this project (see test_ea_executes_honesty.py) -- these
     are the load-bearing regression guard against the fix being silently
     reverted or bypassed.

Run: python3 scripts/test_alert_entry_consistency.py
Exits non-zero on any failure.
"""
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ═══════════════════════════════════════════════════════════
# 1. The reported incident's exact numbers -- pins the invariant
# ═══════════════════════════════════════════════════════════

PRICE       = 4404.01   # raw M15 close at signal time -- the OLD buggy "Entry"
ENTRY_PRICE = 4420.02   # zone-clamped real entry -- what sl is actually computed against
SL          = 4413.57   # sl, correctly computed relative to entry_price

check("reported incident: sl sits on the CORRECT side of entry_price "
      "(below, as expected for a BUY) -- the real signal was fine",
      SL < ENTRY_PRICE)
check("reported incident: sl sits on the WRONG side of the raw price "
      "(above it) -- exactly the 'SL looks backwards' symptom reported",
      SL > PRICE)


# ═══════════════════════════════════════════════════════════
# 2. Source guards: compute_signal()'s call sites use entry_price
# ═══════════════════════════════════════════════════════════

import signal_engine

src = inspect.getsource(signal_engine.compute_signal)

check("alert_signal_ready() call site passes entry_price (not the raw "
      "price) as its Entry argument",
      bool(re.search(
          r"alert_signal_ready\(\s*active_symbol,\s*direction,\s*sig_type,\s*final_score,\s*\n\s*entry_price,\s*sl,\s*tp_final,",
          src)))
check("alert_signal_ready() call site no longer passes the bare raw "
      "price as its Entry argument",
      not bool(re.search(
          r"alert_signal_ready\(\s*active_symbol,\s*direction,\s*sig_type,\s*final_score,\s*\n\s*price,\s*sl,\s*tp_final,",
          src)))
check("log_signal() call site passes entry=entry_price (not entry=price)",
      bool(re.search(r"entry\s*=\s*entry_price\s*,\s*sl\s*=\s*sl", src)))
check("log_signal() call site no longer passes entry=price",
      not bool(re.search(r"entry\s*=\s*price\s*,\s*sl\s*=\s*sl", src)))


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All alert-entry-consistency regression checks passed.")
