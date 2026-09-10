#!/usr/bin/env python3
"""
Regression tests for the Support/Resistance RVOL (VSA volume strength)
feature (10 ก.ย. 2026, owner's request).

Owner: "เราจะมาดูเรื่องการเปรียบเทียบ VSA volume ระหว่างแนวรับกับแนวต้าน
เพื่อดูว่าแต่ละ zone ไหนมีความแข็งแกร่งมากว่ากัน" (let's compare VSA volume
between support and resistance to see which zone is stronger). Scope
confirmed across two rounds of clarification: nearest support (below price)
and nearest resistance (above price) ONLY -- not every zone; RVOL = that
zone-forming candle's own volume divided by its recent average volume;
display-only in the Telegram Trend Update, never gates a signal, because
(owner's own words) "ถ้าหลุดแนวหมายถึง SL ที่เราตั้งเอาไม่อยู่" (if the
level breaks, the SL we set won't hold -- this is about assessing how much
the SL/zone can be trusted, not about entering/blocking a trade).

New building blocks under test:
  1. signal_engine.compute_rvol_at_idx(df, idx, window) -- RVOL of one
     candle: its own volume / the average of `window` bars strictly before
     it (same averaging convention as the existing is_high_volume()).
  2. signal_engine.scan_harmonic()'s new "d_idx" field -- the .iloc
     position of D's own candle in that timeframe's df, so its volume can
     be looked up.
  3. signal_engine.get_kivanc_swing_zone(..., return_idx=True) -- backward-
     compatible opt-in 4-tuple carrying each swing's .iloc position too.
  4. auto_fibo_entry.AutoFiboEstimate's new swing_high_idx/swing_low_idx
     fields -- same idea, for the Auto Fibo big-picture zone.
  5. signal_engine.find_nearest_zone_rvol() -- combines all three sources,
     picks the nearest support/resistance PURELY by geometric position
     (level vs price), and returns each with its RVOL.
  6. trend_monitor.py wiring -- ALPHA_TREND_ZONE_RVOL_ENABLED (default ON,
     same precedent as ALPHA_TREND_AUTO_FIBO_ENABLED), TrendResult.zone_rvol,
     analyze_trend() computing it, format_trend_message() displaying it.

Run: python3 scripts/test_zone_rvol.py
Exits non-zero on any failure.
"""
import inspect
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.pop("ALPHA_TREND_ZONE_RVOL_ENABLED", None)
os.environ.setdefault("TELEGRAM_TOKEN", "test-token")

import numpy as np
import pandas as pd

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


import signal_engine as se
from signal_engine import compute_rvol_at_idx, find_nearest_zone_rvol, get_kivanc_swing_zone, scan_harmonic
from auto_fibo_entry import compute_auto_fibo


# ═══════════════════════════════════════════════════════════
# 1. compute_rvol_at_idx()
# ═══════════════════════════════════════════════════════════

vol_df = pd.DataFrame({
    "high": [1.0] * 60, "low": [1.0] * 60, "close": [1.0] * 60,
    "volume": [100] * 55 + [500] * 5,
})
# idx=55: window bars strictly before it are iloc[5:55] -- all 100 -> avg
# 100, candle's own volume (iloc[55]) is 500 -> RVOL 5.0.
check("compute_rvol_at_idx: 5x average volume -> RVOL == 5.0",
      compute_rvol_at_idx(vol_df, 55, window=50) == 5.0)
# idx=56: window bars strictly before it are iloc[6:56] -- 49 bars of 100
# plus 1 bar of 500 -> avg = (49*100 + 500)/50 = 108.0 -> candle itself
# (iloc[56]) is 500 -> RVOL = 500/108.
check("compute_rvol_at_idx: window correctly excludes the candle itself",
      abs(compute_rvol_at_idx(vol_df, 56, window=50) - (500 / 108.0)) < 1e-9)

check("compute_rvol_at_idx: None df -> None, never raises",
      compute_rvol_at_idx(None, 5) is None)
check("compute_rvol_at_idx: None idx -> None, never raises",
      compute_rvol_at_idx(vol_df, None) is None)
check("compute_rvol_at_idx: idx < 0 -> None",
      compute_rvol_at_idx(vol_df, -1) is None)
check("compute_rvol_at_idx: idx >= len(df) -> None",
      compute_rvol_at_idx(vol_df, len(vol_df)) is None)
check("compute_rvol_at_idx: idx == 0 (no history before it) -> None",
      compute_rvol_at_idx(vol_df, 0) is None)
no_vol_df = pd.DataFrame({"high": [1.0] * 10, "low": [1.0] * 10, "close": [1.0] * 10})
check("compute_rvol_at_idx: no 'volume' column -> None",
      compute_rvol_at_idx(no_vol_df, 5) is None)
zero_vol_df = pd.DataFrame({"high": [1.0] * 10, "low": [1.0] * 10,
                             "close": [1.0] * 10, "volume": [0] * 10})
check("compute_rvol_at_idx: zero average volume before idx -> None (not "
      "div-by-zero, never raises)",
      compute_rvol_at_idx(zero_vol_df, 5) is None)


# ═══════════════════════════════════════════════════════════
# 2. scan_harmonic()'s new "d_idx" field
# ═══════════════════════════════════════════════════════════

def make_harmonic_df(wick, seg_len=5):
    """Same fixture as test_harmonic_h1_h4_tf.py -- an exact Bullish_ABCD
    (X=200,A=300,B=238.2,C=269.1,D=141.9) shape, D being an "L" (low)
    pivot point."""
    X, A, B, C, D = 200.0, 300.0, 238.2, 269.1, 141.9
    close_points = [220.0, X + wick, A - wick, B + wick, C - wick, D + wick, 155.0]
    closes = [close_points[0]]
    for i in range(1, len(close_points)):
        start, end = close_points[i - 1], close_points[i]
        step = (end - start) / seg_len
        for s in range(1, seg_len + 1):
            closes.append(start + step * s)
    highs = [c + wick for c in closes]
    lows = [c - wick for c in closes]
    return pd.DataFrame({
        "open": closes, "high": highs, "low": lows, "close": closes,
        "volume": [100 + i for i in range(len(closes))],
    })


h_df = make_harmonic_df(wick=0.3)
h_results = [r for r in scan_harmonic(h_df, h_df) if r["name"] == "Bullish_ABCD" and r["tf"] == "H1"]
check("scan_harmonic: found the fixture's Bullish_ABCD match on H1", len(h_results) == 1)
if h_results:
    r = h_results[0]
    check("scan_harmonic: d_idx present and points at D's own candle "
          "(that candle's 'low' == D == prz_mid, since D is an 'L' pivot)",
          "d_idx" in r and abs(float(h_df["low"].iloc[r["d_idx"]]) - r["prz_mid"]) < 1e-6)


# ═══════════════════════════════════════════════════════════
# 3. get_kivanc_swing_zone(return_idx=True) -- backward compatible
# ═══════════════════════════════════════════════════════════

np.random.seed(7)
n = 100
base = 100 + np.cumsum(np.random.randn(n) * 0.01)
kdf = pd.DataFrame({
    "high": base + np.random.rand(n) * 0.5,
    "low":  base - np.random.rand(n) * 0.5,
    "close": base, "open": base,
    "volume": np.random.randint(100, 1000, n),
})

sh_old, sl_old = get_kivanc_swing_zone(kdf, pivot_n=10)
sh_new, sl_new, sh_idx, sl_idx = get_kivanc_swing_zone(kdf, pivot_n=10, return_idx=True)
check("get_kivanc_swing_zone: return_idx=True returns the SAME swing_high/"
      "swing_low as the default return_idx=False call (fully backward "
      "compatible)",
      sh_old == sh_new and sl_old == sl_new)
if sh_idx is not None:
    check("get_kivanc_swing_zone: swing_high_idx points at the real "
          "candle that formed swing_high",
          float(kdf["high"].iloc[sh_idx]) == sh_new)
if sl_idx is not None:
    check("get_kivanc_swing_zone: swing_low_idx points at the real "
          "candle that formed swing_low",
          float(kdf["low"].iloc[sl_idx]) == sl_new)

too_short = pd.DataFrame({"high": [1.0] * 5, "low": [1.0] * 5})
check("get_kivanc_swing_zone: too-short df, return_idx=False -> (None, None)",
      get_kivanc_swing_zone(too_short, pivot_n=10) == (None, None))
check("get_kivanc_swing_zone: too-short df, return_idx=True -> 4-tuple of None",
      get_kivanc_swing_zone(too_short, pivot_n=10, return_idx=True) == (None, None, None, None))


# ═══════════════════════════════════════════════════════════
# 4. AutoFiboEstimate.swing_high_idx / swing_low_idx
# ═══════════════════════════════════════════════════════════

af_prices = [50.0] + [50.0 + i * 0.5 for i in range(148)] + [120.0]
af_df = pd.DataFrame({
    "open": af_prices, "high": af_prices, "low": af_prices, "close": af_prices,
    "volume": [100] * len(af_prices),
})
est = compute_auto_fibo(af_df)
check("compute_auto_fibo(): returns a real estimate on the 150-bar fixture",
      est is not None)
if est is not None:
    check("AutoFiboEstimate: swing_high_idx/swing_low_idx populated",
          est.swing_high_idx is not None and est.swing_low_idx is not None)
    check("AutoFiboEstimate: swing_high_idx points at the real candle "
          "(df['high'].iloc[idx] == swing_high, exact)",
          float(af_df["high"].iloc[est.swing_high_idx]) == est.swing_high)
    check("AutoFiboEstimate: swing_low_idx points at the real candle "
          "(df['low'].iloc[idx] == swing_low, exact)",
          float(af_df["low"].iloc[est.swing_low_idx]) == est.swing_low)


# ═══════════════════════════════════════════════════════════
# 5. find_nearest_zone_rvol() -- geometric nearest-support/resistance pick
# ═══════════════════════════════════════════════════════════

df_1h_fixture = pd.DataFrame({
    "high": [1.0] * 20, "low": [1.0] * 20, "close": [1.0] * 20,
    "volume": [50, 60, 70, 200, 90, 100, 110, 400, 130, 140,
               150, 160, 170, 180, 190, 200, 210, 220, 230, 240],
})
df_4h_fixture = pd.DataFrame({
    "high": [1.0] * 20, "low": [1.0] * 20, "close": [1.0] * 20,
    "volume": [50, 500, 70, 80, 90, 100, 110, 120, 130, 600,
               150, 160, 170, 180, 190, 200, 210, 220, 230, 240],
})

price = 100.0
prz_list = [
    {"direction": "SELL", "tf": "H4", "d_idx": 5, "prz_mid": 90.0},   # support candidate
    {"direction": "BUY",  "tf": "H1", "d_idx": 7, "prz_mid": 110.0},  # resistance candidate
]
result = find_nearest_zone_rvol(
    price, prz_list, df_1h_fixture, df_4h_fixture,
    kivanc_swing_high=120.0, kivanc_swing_low=95.0,
    kivanc_high_idx=3, kivanc_low_idx=2,
    auto_fibo=None,
)
check("find_nearest_zone_rvol: geometric classification ignores the "
      "harmonic pattern's own 'direction' label -- a SELL PRZ below price "
      "(90) still counts as SUPPORT, a BUY PRZ above price (110) still "
      "counts as RESISTANCE",
      result["support"] is not None and result["resistance"] is not None)
check("find_nearest_zone_rvol: nearest support is the CLOSEST level below "
      "price (95 from Kivanc, not 90 from the harmonic PRZ, even though "
      "the PRZ was listed first)",
      result["support"]["level"] == 95.0 and result["support"]["source"] == "Kivanc H1")
check("find_nearest_zone_rvol: nearest resistance is the CLOSEST level "
      "above price (110 from the harmonic PRZ, not 120 from Kivanc)",
      result["resistance"]["level"] == 110.0 and result["resistance"]["source"] == "Harmonic H1")
check("find_nearest_zone_rvol: support RVOL computed from df_1h at "
      "kivanc_low_idx=2 (matches compute_rvol_at_idx directly)",
      result["support"]["rvol"] == compute_rvol_at_idx(df_1h_fixture, 2, 50))
check("find_nearest_zone_rvol: resistance RVOL computed from df_1h at "
      "d_idx=7 (Harmonic H1 -> df_1h, not df_4h)",
      result["resistance"]["rvol"] == compute_rvol_at_idx(df_1h_fixture, 7, 50))

no_candidates = find_nearest_zone_rvol(
    price, [], None, None, None, None, None, None, None,
)
check("find_nearest_zone_rvol: no candidates at all -> both None, never raises",
      no_candidates == {"support": None, "resistance": None})

only_support = find_nearest_zone_rvol(
    price, [], df_1h_fixture, df_4h_fixture,
    kivanc_swing_high=None, kivanc_swing_low=80.0,
    kivanc_high_idx=None, kivanc_low_idx=1,
    auto_fibo=None,
)
check("find_nearest_zone_rvol: only a support candidate present -> "
      "resistance stays None (not crash / not fabricated)",
      only_support["support"] is not None and only_support["resistance"] is None)


# ═══════════════════════════════════════════════════════════
# 6. trend_monitor.py wiring
# ═══════════════════════════════════════════════════════════

import trend_monitor as tm

check("trend_monitor: ALPHA_TREND_ZONE_RVOL_ENABLED defaults to True in a "
      "fresh process with no env override (same default-ON precedent as "
      "ALPHA_TREND_AUTO_FIBO_ENABLED)",
      tm.ZONE_RVOL_ENABLED is True)
check("trend_monitor: default is driven by the 'true' default string",
      'ALPHA_TREND_ZONE_RVOL_ENABLED", "true"' in inspect.getsource(tm))

np.random.seed(42)
n = 200
base = 2000 + np.cumsum(np.random.randn(n)) * 2
def make_ohlcv(base):
    return pd.DataFrame({
        "open": base, "high": base + np.random.rand(n) * 3,
        "low": base - np.random.rand(n) * 3, "close": base + np.random.randn(n),
        "volume": np.random.randint(100, 1000, n),
    })
df_4h_e2e = make_ohlcv(base)
df_1h_e2e = make_ohlcv(base)
df_15m_e2e = make_ohlcv(base)

tr = tm.analyze_trend(df_4h_e2e, df_1h_e2e, df_15m_e2e, symbol="XAUUSD")
check("analyze_trend(): TrendResult carries a zone_rvol dict end-to-end "
      "on a realistic multi-timeframe fixture",
      tr.zone_rvol is not None and ("support" in tr.zone_rvol and "resistance" in tr.zone_rvol))

msg = tm.format_trend_message(tr)
check("format_trend_message(): shows the Zone Strength (RVOL) block when "
      "zone_rvol has data",
      "Zone Strength (RVOL)" in msg)

# Kill switch: disabling the flag must silence both computation and display.
os.environ["ALPHA_TREND_ZONE_RVOL_ENABLED"] = "false"
import importlib
tm_off = importlib.reload(tm)
check("trend_monitor: ALPHA_TREND_ZONE_RVOL_ENABLED=false disables the flag",
      tm_off.ZONE_RVOL_ENABLED is False)
tr_off = tm_off.analyze_trend(df_4h_e2e, df_1h_e2e, df_15m_e2e, symbol="XAUUSD")
check("analyze_trend(): kill switch off -> zone_rvol stays None (no "
      "computation done at all)",
      tr_off.zone_rvol is None)
msg_off = tm_off.format_trend_message(tr_off)
check("format_trend_message(): kill switch off -> no Zone Strength block "
      "in the Trend Update",
      "Zone Strength (RVOL)" not in msg_off)
os.environ.pop("ALPHA_TREND_ZONE_RVOL_ENABLED", None)
importlib.reload(tm)  # restore true default for anything importing tm later


# ═══════════════════════════════════════════════════════════
# 7. Source guards
# ═══════════════════════════════════════════════════════════

src_analyze = inspect.getsource(__import__("trend_monitor").analyze_trend)
check("analyze_trend(): zone_rvol computation is wrapped in try/except, "
      "same as auto_fibo, so a computation issue can never break the "
      "Trend Update",
      "zone_rvol = None" in src_analyze and "except Exception" in src_analyze)
check("analyze_trend(): calls scan_harmonic(df_1h, df_4h) to gather "
      "harmonic PRZ candidates for the RVOL comparison",
      "scan_harmonic(df_1h, df_4h)" in src_analyze)
check("analyze_trend(): calls get_kivanc_swing_zone(..., return_idx=True)",
      "return_idx=True" in src_analyze)

src_format = inspect.getsource(__import__("trend_monitor").format_trend_message)
check("format_trend_message(): Zone Strength block gated behind "
      "ZONE_RVOL_ENABLED, same pattern as the Auto Fibo block",
      "ZONE_RVOL_ENABLED and tr.zone_rvol" in src_format)


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Support/Resistance RVOL (zone strength) regression checks passed.")
