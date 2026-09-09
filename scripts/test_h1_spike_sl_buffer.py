#!/usr/bin/env python3
"""
Regression tests for widening detect_h1_spike_at_kivanc()'s SL buffer
(9 ก.ย. 2026, owner's request): "จุด Stoploss อยู่ใต้เส้นคาดการณ์ sweep บน
kivanc ไป 2-3 usd" (the Stoploss point should sit below the sweep-prediction
line at the Kivanc zone by 2-3 USD).

detect_h1_spike_at_kivanc() is the one function that combines BOTH "sweep"
(an H1 candle with a >=60% wick -- the "sweep-prediction" candle) AND
"Kivanc" (at_kivanc: that candle's wick tip must land inside fib_zone, the
Kivanc/harmonic PRZ zone) into a single SL -- exactly what the owner
described. Its SL used to be a flat `wick_tip -/+ 0.30`; the owner confirmed
0.30 was too tight (sat almost right on the sweep wick with near-zero
room), asked it widened to 2-3 USD, and asked it be configurable rather
than hardcoded -- ALPHA_H1_SPIKE_SL_BUFFER, default 2.5.

Explicitly OUT OF SCOPE, owner-confirmed: resolve_sweep_wick_entry()'s
separate M15 session-sweep buffer and resolve_kivanc_minor_sl_fallback()'s
separate minor-Kivanc-swing buffer -- both also default to 0.30 but are
unrelated mechanisms the owner asked NOT to change here.

Run: python3 scripts/test_h1_spike_sl_buffer.py
Exits non-zero on any failure.
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def make_buy_spike_df():
    """3-bar df_1h: iloc[-2] is a BUY sweep candle -- wick_down/rng > 0.60,
    low=100.0, landing inside a Kivanc/PRZ fib_zone of [99, 101]."""
    return pd.DataFrame({
        "open":  [109.5, 105.0, 104.5],
        "high":  [110.0, 105.2, 104.8],
        "low":   [108.0, 100.0, 104.0],
        "close": [109.0, 104.5, 104.3],
    })


def make_sell_spike_df():
    """3-bar df_1h: iloc[-2] is a SELL sweep candle -- wick_up/rng > 0.60,
    high=105.0, landing inside a Kivanc/PRZ fib_zone of [104, 106]."""
    return pd.DataFrame({
        "open":  [95.0, 100.0, 100.5],
        "high":  [96.0, 105.0, 100.8],
        "low":   [94.0, 99.8,  100.2],
        "close": [95.5, 100.5, 100.6],
    })


# ═══════════════════════════════════════════════════════════
# 1. Default buffer (module constant, env unset) -- 2.5
# ═══════════════════════════════════════════════════════════
os.environ.pop("ALPHA_H1_SPIKE_SL_BUFFER", None)
import signal_engine as se

check("module default: H1_SPIKE_SL_BUFFER == 2.5 with no env override",
      se.H1_SPIKE_SL_BUFFER == 2.5)

buy_res = se.detect_h1_spike_at_kivanc(make_buy_spike_df(), "BUY", 101.0, 99.0)
check("BUY: spike found (wick_down > 0.60, low inside fib_zone)",
      buy_res["found"] is True)
check("BUY: SL == wick low (100.0) - 2.5 default buffer == 97.5 "
      "(was 99.7 under the old 0.30 buffer)",
      buy_res["sl"] == 97.5)

sell_res = se.detect_h1_spike_at_kivanc(make_sell_spike_df(), "SELL", 106.0, 104.0)
check("SELL: spike found (wick_up > 0.60, high inside fib_zone)",
      sell_res["found"] is True)
check("SELL: SL == wick high (105.0) + 2.5 default buffer == 107.5 "
      "(was 105.3 under the old 0.30 buffer)",
      sell_res["sl"] == 107.5)


# ═══════════════════════════════════════════════════════════
# 2. Explicit sl_buffer kwarg overrides the module default for one call
# ═══════════════════════════════════════════════════════════
buy_custom = se.detect_h1_spike_at_kivanc(make_buy_spike_df(), "BUY", 101.0, 99.0, sl_buffer=3.0)
check("BUY: explicit sl_buffer=3.0 overrides the 2.5 default -> SL == 97.0",
      buy_custom["sl"] == 97.0)

buy_old = se.detect_h1_spike_at_kivanc(make_buy_spike_df(), "BUY", 101.0, 99.0, sl_buffer=0.30)
check("BUY: explicit sl_buffer=0.30 reproduces the OLD pre-fix SL == 99.7",
      buy_old["sl"] == 99.7)


# ═══════════════════════════════════════════════════════════
# 3. Env var ALPHA_H1_SPIKE_SL_BUFFER overrides the module default
# ═══════════════════════════════════════════════════════════
os.environ["ALPHA_H1_SPIKE_SL_BUFFER"] = "3.5"
import importlib
se_reloaded = importlib.reload(se)
check("env ALPHA_H1_SPIKE_SL_BUFFER=3.5 -> module constant picks it up",
      se_reloaded.H1_SPIKE_SL_BUFFER == 3.5)
buy_env = se_reloaded.detect_h1_spike_at_kivanc(make_buy_spike_df(), "BUY", 101.0, 99.0)
check("env override actually reaches detect_h1_spike_at_kivanc()'s SL "
      "(no sl_buffer kwarg passed) -> 100.0 - 3.5 == 96.5",
      buy_env["sl"] == 96.5)
os.environ.pop("ALPHA_H1_SPIKE_SL_BUFFER", None)
importlib.reload(se)  # restore the 2.5 default for anything importing next


# ═══════════════════════════════════════════════════════════
# 4. Non-spike / no-zone-match paths unaffected (still return found=False)
# ═══════════════════════════════════════════════════════════
check("no spike, no zone match -> found=False, unaffected by the buffer change",
      se.detect_h1_spike_at_kivanc(make_buy_spike_df(), "BUY", 50.0, 40.0)["found"] is False)
check("None df -> found=False, never raises",
      se.detect_h1_spike_at_kivanc(None, "BUY", 101.0, 99.0)["found"] is False)


# ═══════════════════════════════════════════════════════════
# 5. Source guards: scope confirmed -- only this one function changed
# ═══════════════════════════════════════════════════════════
src_fn = inspect.getsource(se.detect_h1_spike_at_kivanc)
check("detect_h1_spike_at_kivanc(): no more hardcoded 0.30 literal",
      "0.30" not in src_fn)
check("detect_h1_spike_at_kivanc(): SL now derived from the configurable buffer",
      "c[\"low\"]-buf" in src_fn and "c[\"high\"]+buf" in src_fn)
check("module: H1_SPIKE_SL_BUFFER reads ALPHA_H1_SPIKE_SL_BUFFER, default 2.5",
      'ALPHA_H1_SPIKE_SL_BUFFER", "2.5"' in inspect.getsource(se))

src_sweep_wick = inspect.getsource(se.resolve_sweep_wick_entry)
check("resolve_sweep_wick_entry(): UNCHANGED, still its own separate 0.30 "
      "default -- owner confirmed this M15 session-sweep buffer is a "
      "different, unrelated mechanism and should not change here",
      "buffer=0.30" in src_sweep_wick)

src_kivanc_minor = inspect.getsource(se.resolve_kivanc_minor_sl_fallback)
check("resolve_kivanc_minor_sl_fallback(): UNCHANGED, still its own "
      "separate 0.30 default -- owner confirmed this minor-Kivanc-swing "
      "buffer is a different, unrelated mechanism and should not change here",
      "buffer=0.30" in src_kivanc_minor)

src_compute_signal = inspect.getsource(se.compute_signal)
check("compute_signal()'s call site passes no explicit sl_buffer -- picks "
      "up H1_SPIKE_SL_BUFFER/env automatically",
      "sl_buffer=" not in src_compute_signal.split("detect_h1_spike_at_kivanc(")[1][:200]
      if "detect_h1_spike_at_kivanc(" in src_compute_signal else False)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All H1-spike-at-Kivanc SL buffer regression checks passed.")
