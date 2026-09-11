#!/usr/bin/env python3
"""
Regression tests for signal_engine.compute_heikin_ashi() / heikin_ashi_reversed()
(11 ก.ย. 2026, owner's request -- "TF15 ย่อยของ v4 เมื่อถึงแนวขอบ Bollinger
ต้องทำคู่ขนานดูราคาล่าสุด ... TP เป็น upperline BB 15 นาที หรือมี Heikin
Ashi แดง สองแท่ง"). Heikin Ashi never existed in this codebase before --
this is the first time it's computed anywhere.

Covers:
  1. compute_heikin_ashi() -- hand-computed values for a small fixed OHLC
     series, empty/None input never raises.
  2. heikin_ashi_reversed() -- BUY (red x2) / SELL (green x2) detection,
     a single reversed candle is NOT enough, not-enough-bars and unknown-
     direction guards, never raises.

Run: python3 scripts/test_heikin_ashi.py
Exits non-zero on any failure.
"""
import os
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


import pandas as pd
from signal_engine import compute_heikin_ashi, heikin_ashi_reversed


# ═══════════════════════════════════════════════════════════
# 1. compute_heikin_ashi()
# ═══════════════════════════════════════════════════════════

check("compute_heikin_ashi: None df -> empty DataFrame with the right "
      "columns, never raises",
      list(compute_heikin_ashi(None).columns) == ["ha_open", "ha_close"]
      and len(compute_heikin_ashi(None)) == 0)
check("compute_heikin_ashi: empty df -> empty result, never raises",
      len(compute_heikin_ashi(pd.DataFrame(columns=["open", "high", "low", "close"]))) == 0)

# Hand-computed fixture: 3 bars.
# Bar0: O=100 H=105 L=98  C=102 -> ha_close=(100+105+98+102)/4=101.25
#                                   ha_open =(100+102)/2=101.0
# Bar1: O=102 H=108 L=101 C=106 -> ha_close=(102+108+101+106)/4=104.25
#                                   ha_open =(101.0+101.25)/2=101.125
# Bar2: O=106 H=107 L=99  C=100 -> ha_close=(106+107+99+100)/4=103.0
#                                   ha_open =(101.125+104.25)/2=102.6875
df3 = pd.DataFrame({
    "open":  [100.0, 102.0, 106.0],
    "high":  [105.0, 108.0, 107.0],
    "low":   [98.0,  101.0, 99.0],
    "close": [102.0, 106.0, 100.0],
})
ha3 = compute_heikin_ashi(df3)
check("compute_heikin_ashi: bar0 ha_close = (O+H+L+C)/4",
      abs(ha3["ha_close"].iloc[0] - 101.25) < 1e-9)
check("compute_heikin_ashi: bar0 ha_open seeded from (open+close)/2",
      abs(ha3["ha_open"].iloc[0] - 101.0) < 1e-9)
check("compute_heikin_ashi: bar1 ha_close = (O+H+L+C)/4",
      abs(ha3["ha_close"].iloc[1] - 104.25) < 1e-9)
check("compute_heikin_ashi: bar1 ha_open = avg(prev ha_open, prev ha_close)",
      abs(ha3["ha_open"].iloc[1] - 101.125) < 1e-9)
check("compute_heikin_ashi: bar2 ha_close = (O+H+L+C)/4",
      abs(ha3["ha_close"].iloc[2] - 103.0) < 1e-9)
check("compute_heikin_ashi: bar2 ha_open recurses two levels deep correctly",
      abs(ha3["ha_open"].iloc[2] - 102.6875) < 1e-9)
check("compute_heikin_ashi: bar0 is green (ha_close > ha_open, 101.25 > 101.0)",
      ha3["ha_close"].iloc[0] > ha3["ha_open"].iloc[0])
check("compute_heikin_ashi: bar2 is green (ha_close 103.0 > ha_open 102.6875)",
      ha3["ha_close"].iloc[2] > ha3["ha_open"].iloc[2])


# ═══════════════════════════════════════════════════════════
# 2. heikin_ashi_reversed()
# ═══════════════════════════════════════════════════════════

def _flat_then_move(prices):
    """Build a simple OHLC df from a list of closes -- open=prev close
    (or itself for bar0), high=max(open,close)+0.1, low=min(open,close)-0.1.
    Enough to produce clean, predictable Heikin Ashi colors for testing."""
    opens = [prices[0]] + prices[:-1]
    highs = [max(o, c) + 0.1 for o, c in zip(opens, prices)]
    lows = [min(o, c) - 0.1 for o, c in zip(opens, prices)]
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": prices})


# A clean downtrend (each close lower than the last) -> every HA candle red.
df_down = _flat_then_move([110, 108, 106, 104, 102, 100])
check("heikin_ashi_reversed: BUY position, last 2 candles red in a clean "
      "downtrend -> True",
      heikin_ashi_reversed(df_down, "BUY", n=2) is True)
check("heikin_ashi_reversed: SELL position in the SAME downtrend -> False "
      "(red candles don't reverse a SELL)",
      heikin_ashi_reversed(df_down, "SELL", n=2) is False)

# A clean uptrend -> every HA candle green.
df_up = _flat_then_move([100, 102, 104, 106, 108, 110])
check("heikin_ashi_reversed: SELL position, last 2 candles green in a "
      "clean uptrend -> True",
      heikin_ashi_reversed(df_up, "SELL", n=2) is True)
check("heikin_ashi_reversed: BUY position in the SAME uptrend -> False",
      heikin_ashi_reversed(df_up, "BUY", n=2) is False)

# Only the LAST candle reversed (uptrend then one down tick) -- not enough
# for n=2, since only 1 of the last 2 is red.
df_one_red = _flat_then_move([100, 102, 104, 106, 108, 106])
check("heikin_ashi_reversed: only 1 of the last 2 candles reversed -> "
      "False (needs n consecutive)",
      heikin_ashi_reversed(df_one_red, "BUY", n=2) is False)

check("heikin_ashi_reversed: fewer bars than n -> False, never raises",
      heikin_ashi_reversed(_flat_then_move([100]), "BUY", n=2) is False)
check("heikin_ashi_reversed: unknown direction -> False, never raises",
      heikin_ashi_reversed(df_down, "SIDEWAYS", n=2) is False)
check("heikin_ashi_reversed: None df -> False, never raises",
      heikin_ashi_reversed(None, "BUY", n=2) is False)
check("heikin_ashi_reversed: n=3 requires 3 consecutive -- clean downtrend "
      "(5+ bars) still satisfies it",
      heikin_ashi_reversed(df_down, "BUY", n=3) is True)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
else:
    print(f"ALL PASSED")
