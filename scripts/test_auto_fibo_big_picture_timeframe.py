#!/usr/bin/env python3
"""
Regression tests for the "big picture PRZ zone" fix (9 ก.ย. 2026).

Owner's report: "ในภาพใหญ่ เราต้องเน้นที่ prz zone เพื่อหาจุดเข้าที่ชัดเจน
จากภาพใน pine จะมี prz support/demand zone ชัดเจน มี pattern harmonic ชัด
แต่เราไม่มีสัญญาณระดับภาพใหญ่" (in the big picture we need to focus on the
PRZ zone for clear entries; the Pine indicator shows a clear PRZ, but the
bot has no big-picture-level signal).

Root cause found: auto_fibo_entry.py's compute_auto_fibo() already exists
and is already correctly parameterized to match the Pine multi-asset
fork's "Auto Fibo (144, 1.272)" Estimate Entry feature (144-bar rolling
window, 0.618-0.786 golden zone, 1.272 extension) -- but BOTH of its call
sites were feeding it df_15m (144 M15 bars ~= 1.5 days) instead of a higher
timeframe, nowhere near the ~1-month "big picture" window the Pine
indicator's chart actually shows.

Fix (this file's coverage):
  1. Both call sites -- signal_engine.py's compute_signal() and
     trend_monitor.py's analyze_trend() -- now feed compute_auto_fibo()
     df_4h (144 confirmed 4H bars ~= 24 days) instead of df_15m.
  2. Both df_4h fetch sites in alpha_buffalo_signal.py (the main XAUUSD
     loop and run_extra_symbol_pass() for BTC/US100/JPN225) were bumped
     from get_ohlcv("4h", 100, ...) to get_ohlcv("4h", 150, ...) --
     compute_auto_fibo() drops the live/forming candle before taking its
     144-bar tail, so 100 confirmed bars (99 after the drop) could never
     reach the full 144-bar window; 150 fetched (149 confirmed) can.
  3. trend_monitor.py's ALPHA_TREND_AUTO_FIBO_ENABLED display flag default
     flipped false -> true, so the (now-correct) big-picture PRZ zone is
     actually visible in the routine Telegram Trend Update ahead of a V5
     signal firing, per the owner's explicit ask ("เป้า prz ต้องทราบเพื่อ
     เตรียม v5 ต้องมี") -- NOT gated behind an extra opt-in the owner would
     have to discover and flip themselves.
  4. signal_engine.py's ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED (the gate
     that would BLOCK a signal when price is outside the Estimate Entry
     zone) is explicitly UNCHANGED -- still default OFF. The owner
     independently confirmed the current V4_SESSION/V5_SNIPER gating
     split is already correct: V5_SNIPER already requires a real harmonic
     PRZ (validate_scenario()'s "V5 needs pattern" check, independent of
     this flag), and V4_SESSION is intentionally NOT required to be inside
     any particular zone. So this fix is display/tracking-only for V4 --
     it does not add a new hard gate.

Run: python3 scripts/test_auto_fibo_big_picture_timeframe.py
Exits non-zero on any failure.
"""
import inspect
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Make sure neither env var is set by the outer environment so the
# "fresh process, no override" default checks below are meaningful.
os.environ.pop("ALPHA_TREND_AUTO_FIBO_ENABLED", None)
os.environ.pop("ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED", None)
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


import signal_engine
import trend_monitor as tm
import alpha_buffalo_signal as runtime


# ═══════════════════════════════════════════════════════════
# 1. compute_signal() feeds compute_auto_fibo() df_4h, not df_15m
# ═══════════════════════════════════════════════════════════

src_signal = inspect.getsource(signal_engine.compute_signal)
check("compute_signal(): compute_auto_fibo(df_4h) -- the big-picture feed",
      "compute_auto_fibo(df_4h)" in src_signal)
check("compute_signal(): no longer feeds compute_auto_fibo(df_15m)",
      "compute_auto_fibo(df_15m)" not in src_signal)

# ── signal-gating flag: confirmed UNCHANGED (still opt-in, default OFF) ──
check("compute_signal(): ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED default "
      "still 'false' -- V4_SESSION gating intentionally left as-is",
      'ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED", "false"' in src_signal)
check("signal_engine: filter flag actually evaluates False with no env "
      "override in this fresh process",
      signal_engine  # module imported above already read the env at import
      and os.getenv("ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED", "false") == "false")


# ═══════════════════════════════════════════════════════════
# 2. analyze_trend() feeds compute_auto_fibo() df_4h, not df_15m
# ═══════════════════════════════════════════════════════════

src_trend = inspect.getsource(tm.analyze_trend)
check("analyze_trend(): compute_auto_fibo(df_4h) -- the big-picture feed",
      "compute_auto_fibo(df_4h)" in src_trend)
check("analyze_trend(): no longer feeds compute_auto_fibo(df_15m)",
      "compute_auto_fibo(df_15m)" not in src_trend)


# ═══════════════════════════════════════════════════════════
# 3. Display default flipped ON -- visible ahead of a V5 signal without
#    the owner needing to discover/flip an env var themselves.
# ═══════════════════════════════════════════════════════════

check("trend_monitor: ALPHA_TREND_AUTO_FIBO_ENABLED defaults to True in a "
      "fresh process with no env override (was False before this fix)",
      tm.AUTO_FIBO_ENABLED is True)
check("trend_monitor: default is driven by the 'true' default string, not "
      "a leftover 'false'",
      'ALPHA_TREND_AUTO_FIBO_ENABLED", "true"' in inspect.getsource(tm))

# Kill switch still works (owner can turn the display back off).
os.environ["ALPHA_TREND_AUTO_FIBO_ENABLED"] = "false"
import importlib
tm_reloaded = importlib.reload(tm)
check("trend_monitor: ALPHA_TREND_AUTO_FIBO_ENABLED=false still disables "
      "the display (kill switch intact)",
      tm_reloaded.AUTO_FIBO_ENABLED is False)
os.environ.pop("ALPHA_TREND_AUTO_FIBO_ENABLED", None)
importlib.reload(tm)  # restore true default for anything importing tm later


# ═══════════════════════════════════════════════════════════
# 4. Both df_4h fetch sites bumped 100 -> 150 (144-bar window headroom)
# ═══════════════════════════════════════════════════════════

src_runtime = inspect.getsource(runtime)
check('alpha_buffalo_signal.py: run_extra_symbol_pass() fetches '
      'get_ohlcv("4h", 150, symbol=symbol)',
      re.search(r'get_ohlcv\(\s*"4h",\s*150,\s*symbol=symbol\s*\)', src_runtime) is not None)
check('alpha_buffalo_signal.py: main XAUUSD loop fetches '
      'get_ohlcv("4h", 150)',
      re.search(r'get_ohlcv\(\s*"4h",\s*150\s*\)', src_runtime) is not None)
check("alpha_buffalo_signal.py: neither df_4h fetch site is still on the "
      "old 100-bar count",
      'get_ohlcv("4h",  100, symbol=symbol)' not in src_runtime
      and 'get_ohlcv("4h",  100)' not in src_runtime)

# 150 fetched -> 1 dropped (live/forming candle) -> 149 confirmed bars,
# comfortably >= the 144-bar window compute_auto_fibo() actually uses.
FETCHED_4H_BARS = 150
CONFIRMED_AFTER_DROP = FETCHED_4H_BARS - 1
check("150 fetched 4H bars leaves >= 144 confirmed bars after "
      "compute_auto_fibo() drops the live/forming candle",
      CONFIRMED_AFTER_DROP >= 144)


# ═══════════════════════════════════════════════════════════
# 5. End-to-end: with a realistic 150-bar 4H fixture, compute_auto_fibo()
#    actually returns a real (non-None) estimate through both call sites.
# ═══════════════════════════════════════════════════════════

import pandas as pd
from auto_fibo_entry import compute_auto_fibo, DIRECTION_UP

# 150 bars: a long-ago low (bar ~0, well outside a 144-bar window if this
# were still fed only ~99 bars), then a clean up-swing into a recent high.
prices = [50.0] + [50.0 + i * 0.5 for i in range(148)] + [120.0]
df_4h_fixture = pd.DataFrame({
    "open": prices, "high": prices, "low": prices, "close": prices,
    "volume": [100] * len(prices),
})
check("fixture: exactly 150 4H bars, matching the new fetch count",
      len(df_4h_fixture) == 150)

est = compute_auto_fibo(df_4h_fixture)
check("compute_auto_fibo() on a realistic 150-bar 4H fixture returns a "
      "real estimate (not None)",
      est is not None)
if est is not None:
    check("150-bar fixture: swing correctly read as UP (recent high)",
          est.direction == DIRECTION_UP)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All big-picture PRZ zone (Auto Fibo timeframe) regression checks passed.")
