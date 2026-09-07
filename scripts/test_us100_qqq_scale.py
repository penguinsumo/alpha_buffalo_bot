#!/usr/bin/env python3
"""
Regression test for the US100_QQQ_SCALE fix (2026-09-07, confirmed bug,
not opt-in): the ticker configured for "US100" (EXTRA_SYMBOL_TICKERS,
default "NDX") actually returns QQQ's ETF price from TwelveData, not the
real Nasdaq-100 index/futures level -- confirmed by comparing a live
signal (US100 @ 719.06) against real quotes the same day: NQ=F was
~29,565 and QQQ closed at 718.96, an exact match to the signal price,
not the index.

_get_ohlcv_twelvedata() now scales US100's OHLC columns by
US100_QQQ_SCALE (default 41.1, configurable via
ALPHA_EXTRA_SYMBOL_US100_QQQ_SCALE for recalibration as the QQQ/NDX
ratio drifts) so Entry/SL/TP levels shown are in the right order of
magnitude for NDX/NQ instead of QQQ's ETF price -- while every other
symbol stays byte-identical.

Run: python3 scripts/test_us100_qqq_scale.py
Exits non-zero on any failure.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_values(price):
    return {"values": [
        {"datetime": "2026-09-07 00:00:00", "open": str(price), "high": str(price + 1),
         "low": str(price - 1), "close": str(price)},
        {"datetime": "2026-09-07 01:00:00", "open": str(price), "high": str(price + 1),
         "low": str(price - 1), "close": str(price)},
    ]}


os.environ.setdefault("TELEGRAM_TOKEN", "test-token")
import alpha_buffalo_signal as runtime

check("default US100_QQQ_SCALE is 41.1", runtime.US100_QQQ_SCALE == 41.1)

calls = []


def fake_get(url, params=None, timeout=None):
    calls.append(dict(params))
    sym = params["symbol"]
    if sym == runtime.EXTRA_SYMBOL_TICKERS["US100"]:
        return _FakeResp(_fake_values(718.96))   # the real QQQ-scale price TwelveData returns
    return _FakeResp(_fake_values(2650.0))        # arbitrary, for XAUUSD etc.


original_get = runtime.requests.get
runtime.requests.get = fake_get
try:
    df_us100 = runtime.get_ohlcv("1h", 2, symbol="US100")
    check("US100: close is scaled up by US100_QQQ_SCALE (718.96 * 41.1)",
          abs(float(df_us100["close"].iloc[0]) - round(718.96 * 41.1, 6)) < 1e-6)
    check("US100: high is scaled too (open/high/low/close all uniformly)",
          abs(float(df_us100["high"].iloc[0]) - round(719.96 * 41.1, 6)) < 1e-6)
    check("US100: scaled close now lands in NDX/NQ's real order of magnitude (tens of thousands)",
          float(df_us100["close"].iloc[0]) > 20000)

    calls.clear()
    df_xau = runtime.get_ohlcv("1h", 2, symbol="XAUUSD")
    check("XAUUSD: completely untouched, no scaling applied",
          float(df_xau["close"].iloc[0]) == 2650.0)

    # Recalibration: the multiplier must be overridable without a code
    # change, since the QQQ/NDX ratio drifts over time (QQQ's quarterly
    # dividends aren't reinvested into the index).
    runtime.US100_QQQ_SCALE = 45.0
    calls.clear()
    df_us100_recal = runtime.get_ohlcv("1h", 2, symbol="US100")
    check("US100: scale factor is a runtime value, not hardcoded -- recalibrates without a code change",
          abs(float(df_us100_recal["close"].iloc[0]) - round(718.96 * 45.0, 6)) < 1e-6)
finally:
    runtime.requests.get = original_get
    runtime.US100_QQQ_SCALE = 41.1


print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All US100 QQQ-scale regression checks passed.")
