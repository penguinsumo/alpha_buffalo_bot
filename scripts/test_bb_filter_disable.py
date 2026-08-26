#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_BB_FILTER_DISABLE flag on
compute_signal()'s final BB anti-chasing-extremes filter.

Run: python3 scripts/test_bb_filter_disable.py
Exits non-zero on any failure.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from signal_engine import get_bb

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def clear_env():
    os.environ.pop("ALPHA_BB_FILTER_DISABLE", None)


def make_flat_df(n=30, base=100.0, step=0.05):
    closes = [base + i * step for i in range(n)]
    opens = [closes[0] - step] + closes[:-1]
    highs = [max(o, c) + 0.02 for o, c in zip(opens, closes)]
    lows = [min(o, c) - 0.02 for o, c in zip(opens, closes)]
    idx = pd.date_range("2026-08-01", periods=n, freq="15min", tz="UTC")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes}, index=idx)


def bb_filter_blocks(df_15m, direction, price):
    bb_filter_disabled = os.getenv("ALPHA_BB_FILTER_DISABLE", "false").lower() in {
        "1", "true", "yes", "on",
    }
    bb = get_bb(df_15m)
    if not bb_filter_disabled:
        if direction == "BUY" and bb["upper"] < price:
            return True
        if direction == "SELL" and bb["lower"] > price:
            return True
    return False


clear_env()
df = make_flat_df()
bb = get_bb(df)

extended_sell_price = bb["lower"] - 5.0
check("default (flag off): SELL blocked when price is below the lower band",
      bb_filter_blocks(df, "SELL", extended_sell_price) is True)

extended_buy_price = bb["upper"] + 5.0
check("default (flag off): BUY blocked when price is above the upper band",
      bb_filter_blocks(df, "BUY", extended_buy_price) is True)

inside_price = bb["mid"]
check("default (flag off): price inside bands -> SELL not blocked",
      bb_filter_blocks(df, "SELL", inside_price) is False)
check("default (flag off): price inside bands -> BUY not blocked",
      bb_filter_blocks(df, "BUY", inside_price) is False)

os.environ["ALPHA_BB_FILTER_DISABLE"] = "true"
check("flag ON: SELL below lower band is no longer blocked",
      bb_filter_blocks(df, "SELL", extended_sell_price) is False)
check("flag ON: BUY above upper band is no longer blocked",
      bb_filter_blocks(df, "BUY", extended_buy_price) is False)
clear_env()

os.environ["ALPHA_BB_FILTER_DISABLE"] = "false"
explicit_false = bb_filter_blocks(df, "SELL", extended_sell_price)
clear_env()
implicit_default = bb_filter_blocks(df, "SELL", extended_sell_price)
check("ALPHA_BB_FILTER_DISABLE=false explicit == default (no env var)",
      explicit_false == implicit_default == True)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All BB-filter-disable regression checks passed.")
