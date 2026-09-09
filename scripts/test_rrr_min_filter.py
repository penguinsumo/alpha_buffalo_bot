#!/usr/bin/env python3
"""
Regression tests for passes_rrr_min_filter() -- the new default-ON filter
for BTC/NAS100 (owner's explicit request, 9 ก.ย. 2026): "ตอนราคา BTC/NAS100
เป้า TP ต่ำกว่า RRR ไม่เอา" -- reject a BTCUSD/US100 setup outright (before
any alert/log/order) when TP1 (the first/nearest partial target, confirmed
by the owner as the level to check) offers less reward than min_ratio x the
SL's risk. XAUUSD (the main traded SYMBOL) and JPN225/GBPJPY/EURUSD are
completely unaffected by default -- they're not in the symbol set this
filter applies to.

The floor was first asked for as 1:1, then explicitly raised to 2:1
(the new default) after reviewing a real live example that PASSED 1:1 but
the owner still judged too tight: US100, entry=29,525.44, real sl=29,487.1
(the "SL Zone" hi bound shown in Telegram -- format_signal_message()
derives sl_zone_lo/hi from a single sl value, sl_zone_hi == sl), tp1=
29,576.7 -- risk=38.34, reward=51.26, RRR ~= 1.34:1. At the 2:1 default
this exact example is blocked (see check below).

Covers:
  1. passes_rrr_min_filter() as a pure function -- below/at/above the 2:1
     default, symbol scoping (BTCUSD/US100 affected, everything else not,
     by default), kill switch, custom symbol set, custom min_ratio, and
     never-raises on degenerate/missing input (risk<=0, None values).
  2. The exact reported example, pinned numerically, confirming it's
     blocked under the new 2:1 default despite passing a 1:1 floor.
  3. A source guard confirming compute_signal() actually wires this in
     right after SL/TP1 are finalized (before alert_signal_ready()/
     log_signal()) and returns None when it's blocked -- same reasoning
     as test_alert_entry_consistency.py for why this isn't driven through
     the full gate cascade with synthetic OHLCV.

Run: python3 scripts/test_rrr_min_filter.py
Exits non-zero on any failure.
"""
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from signal_engine import passes_rrr_min_filter

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ═══════════════════════════════════════════════════════════
# 1. passes_rrr_min_filter() -- pure function, default 2:1 floor
# ═══════════════════════════════════════════════════════════

# BUY-shaped: entry=100, sl=95 (risk=5)
check("BTCUSD: TP1 reward 9 < risk 5 x 2.0 (RRR 1.8:1) -> BLOCKED (default 2:1)",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 109.0) is False)
check("BTCUSD: TP1 reward exactly == risk x 2.0 (RRR 2:1) -> PASSES (not 'below')",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 110.0) is True)
check("BTCUSD: TP1 reward 20 > risk 5 x 2.0 (RRR 4:1) -> PASSES",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 120.0) is True)
check("BTCUSD: an RRR that would pass a 1:1 floor (reward==risk) still "
      "BLOCKED at the 2:1 default",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 105.0) is False)

check("US100: same bad ratio (RRR 1.8:1) -> BLOCKED (US100 is in the default set)",
      passes_rrr_min_filter("US100", 100.0, 95.0, 109.0) is False)

# SELL-shaped: entry=100, sl=105 (risk=5), tp1 below entry
check("BTCUSD SELL-shaped: TP1 reward 9 < risk 5 x 2.0 -> BLOCKED (direction-agnostic)",
      passes_rrr_min_filter("BTCUSD", 100.0, 105.0, 91.0) is False)
check("BTCUSD SELL-shaped: TP1 reward 10 == risk 5 x 2.0 -> PASSES",
      passes_rrr_min_filter("BTCUSD", 100.0, 105.0, 90.0) is True)

# Symbol scoping -- everything NOT in the default set is unaffected
check("XAUUSD (main traded SYMBOL): same bad ratio -> UNAFFECTED, passes",
      passes_rrr_min_filter("XAUUSD", 100.0, 95.0, 109.0) is True)
check("JPN225: same bad ratio -> UNAFFECTED by default, passes",
      passes_rrr_min_filter("JPN225", 100.0, 95.0, 109.0) is True)
check("GBPJPY: same bad ratio -> UNAFFECTED by default, passes",
      passes_rrr_min_filter("GBPJPY", 100.0, 95.0, 109.0) is True)

# Kill switch
check("BTCUSD: enabled=False -> passes even with a bad ratio",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 109.0, enabled=False) is True)

# Custom symbol set
check("custom symbols={'JPN225'}: JPN225 with bad ratio -> now BLOCKED",
      passes_rrr_min_filter("JPN225", 100.0, 95.0, 109.0, symbols={"JPN225"}) is False)
check("custom symbols={'JPN225'}: BTCUSD is now UNAFFECTED (not in the custom set)",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 109.0, symbols={"JPN225"}) is True)

# Custom min_ratio -- explicit override, independent of the 2.0 default
check("min_ratio=1.0 (the original ask): reward==risk -> PASSES",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 105.0, min_ratio=1.0) is True)
check("min_ratio=1.0: reward < risk -> still BLOCKED",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 104.0, min_ratio=1.0) is False)
check("min_ratio=1.5: reward=risk*1.2 -> BLOCKED (below the custom floor)",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 106.0, min_ratio=1.5) is False)
check("min_ratio=1.5: reward=risk*1.5 exactly -> PASSES",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, 107.5, min_ratio=1.5) is True)

# Degenerate / missing input -- never raises, passes through True
check("risk<=0 (sl == entry) -> passes, never raises",
      passes_rrr_min_filter("BTCUSD", 100.0, 100.0, 110.0) is True)
check("entry_price=None -> passes, never raises",
      passes_rrr_min_filter("BTCUSD", None, 95.0, 109.0) is True)
check("sl=None -> passes, never raises",
      passes_rrr_min_filter("BTCUSD", 100.0, None, 109.0) is True)
check("tp1_price=None -> passes, never raises",
      passes_rrr_min_filter("BTCUSD", 100.0, 95.0, None) is True)


# ═══════════════════════════════════════════════════════════
# 2. The exact reported example -- pinned numerically
# ═══════════════════════════════════════════════════════════

ENTRY = 29525.44
SL    = 29487.1   # the real sl -- format_signal_message()'s "SL Zone" hi bound
TP1   = 29576.7
RISK  = abs(ENTRY - SL)     # 38.34
REWARD = abs(TP1 - ENTRY)   # 51.26

check("reported example: RRR ~= 1.34:1 (passes a 1:1 floor)",
      1.3 < REWARD / RISK < 1.4)
check("reported example: BLOCKED at the new 2:1 default despite passing 1:1",
      passes_rrr_min_filter("US100", ENTRY, SL, TP1) is False)
check("reported example: would have PASSED under the original 1:1 ask",
      passes_rrr_min_filter("US100", ENTRY, SL, TP1, min_ratio=1.0) is True)


# ═══════════════════════════════════════════════════════════
# 3. Source guard: compute_signal() wires this in and returns None
# ═══════════════════════════════════════════════════════════

import signal_engine

src = inspect.getsource(signal_engine.compute_signal)
m = re.search(
    r"passes_rrr_min_filter\(active_symbol,\s*entry_price,\s*sl,\s*tp1_price.*?\breturn None\b",
    src, re.S)
check("compute_signal() calls passes_rrr_min_filter(active_symbol, entry_price, "
      "sl, tp1_price, ...) and returns None when it's blocked",
      m is not None)
check("compute_signal() reads the kill switch from ALPHA_RRR_MIN_FILTER_ENABLED",
      "ALPHA_RRR_MIN_FILTER_ENABLED" in src)
check("compute_signal() reads the minimum ratio from ALPHA_RRR_MIN_RATIO, "
      "defaulting to 2.0",
      'ALPHA_RRR_MIN_RATIO", "2.0"' in src)
check("compute_signal() reads the configurable symbol set from "
      "ALPHA_RRR_MIN_FILTER_SYMBOLS, defaulting to BTCUSD,US100",
      'ALPHA_RRR_MIN_FILTER_SYMBOLS", "BTCUSD,US100"' in src)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All RRR-min-filter regression checks passed.")
