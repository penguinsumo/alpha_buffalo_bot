#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_BTC_BINANCE_VOLUME_ENABLED feature:
sourcing BTC's OHLCV from Binance's public klines API (real traded volume)
instead of TwelveData (no volume column at all for BTC/USD).

Covers:
  - binance_feed.resolve_binance_symbol() ticker mapping
  - binance_feed.get_ohlcv_binance() parses a real-shaped klines payload
    into the same DataFrame contract get_ohlcv() uses (UTC index, float
    open/high/low/close/volume), maps "15min" -> "15m", and returns None
    on any failure/empty response without raising
  - alpha_buffalo_signal.get_ohlcv(): flag OFF (default) -> BTC keeps
    calling TwelveData exactly as before, byte-identical; every other
    symbol (XAUUSD/EURUSD/US100/JPN225) is untouched regardless of the
    flag
  - alpha_buffalo_signal.get_ohlcv(): flag ON -> BTC is routed to
    binance_feed.get_ohlcv_binance() instead, and the returned frame
    carries a real, non-zero volume column
  - with real volume present, the previously-dormant volume-aware
    analysis already built into trend_monitor.calc_tf_trend() (Pressure)
    and signal_engine.analyze_structure()/is_high_volume() (vol_spike,
    vol_drop, VSA volume_confirmed) actually activates instead of being a
    silent no-op

Run: python3 scripts/test_binance_btc_volume.py
Exits non-zero on any failure.
"""
import os
import subprocess
import sys
import json

import numpy as np
import pandas as pd

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


# ── binance_feed.resolve_binance_symbol() ───────────────────────────────
from binance_feed import resolve_binance_symbol, get_ohlcv_binance

check("resolve_binance_symbol('BTCUSD') -> BTCUSDT", resolve_binance_symbol("BTCUSD") == "BTCUSDT")
check("resolve_binance_symbol('BTC/USD') -> BTCUSDT", resolve_binance_symbol("BTC/USD") == "BTCUSDT")
check("resolve_binance_symbol('btcusd') case-insensitive -> BTCUSDT", resolve_binance_symbol("btcusd") == "BTCUSDT")
check("resolve_binance_symbol('BTCUSDT') passthrough", resolve_binance_symbol("BTCUSDT") == "BTCUSDT")

# ── Default base URL: the no-geo-block market-data mirror, NOT api.binance.com ─
# Regression guard for a real bug caught by live-testing this from the sandbox:
# api.binance.com (and its api1/2/3/-gcp mirrors) returned HTTP 451 "Service
# unavailable from a restricted location" -- Binance's own compliance geo-block,
# which applies by source IP/region and could just as easily hit Railway.
# data-api.binance.vision is Binance's dedicated read-only market-data mirror
# (no geo-block, confirmed live) and must stay the default.
import binance_feed as _bf_module
check("default BINANCE_API_BASE is the no-geo-block data-api.binance.vision mirror (NOT api.binance.com)",
      _bf_module.BINANCE_API_BASE == "https://data-api.binance.vision")


def _fake_klines_rows(n=30, start_ms=1_700_000_000_000, step_ms=3_600_000):
    rows = []
    for i in range(n):
        o = 60000.0 + i
        h = o + 50
        l = o - 50
        c = o + 10
        vol = 100.0 + (500.0 if i == n - 1 else 0.0)  # spike on the last bar
        t = start_ms + i * step_ms
        rows.append([t, str(o), str(h), str(l), str(c), str(vol),
                     t + step_ms - 1, "0", 10, "0", "0", "0"])
    return rows


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload
    def json(self):
        return self._payload


# ── get_ohlcv_binance(): happy path ──────────────────────────────────────
import binance_feed as bf

captured = {}
def fake_get_ok(url, params=None, timeout=None):
    captured["url"] = url
    captured["params"] = dict(params)
    return _FakeResp(_fake_klines_rows(30))

original_requests_get = bf.requests.get
bf.requests.get = fake_get_ok
try:
    df = get_ohlcv_binance("15min", 30, symbol="BTCUSD")
    check("get_ohlcv_binance() maps '15min' -> Binance's '15m' interval",
          captured["params"]["interval"] == "15m")
    check("get_ohlcv_binance() resolves BTCUSD -> BTCUSDT in the request",
          captured["params"]["symbol"] == "BTCUSDT")
    check("get_ohlcv_binance() hits the public klines endpoint",
          captured["url"].endswith("/api/v3/klines"))
    check("get_ohlcv_binance() returns a DataFrame", isinstance(df, pd.DataFrame))
    check("get_ohlcv_binance() returns the expected columns",
          list(df.columns) == ["open", "high", "low", "close", "volume"])
    check("get_ohlcv_binance() index is UTC-aware",
          df.index.tz is not None and str(df.index.tz) == "UTC")
    check("get_ohlcv_binance() OHLCV columns are float",
          all(df[c].dtype == float for c in ["open", "high", "low", "close", "volume"]))
    check("get_ohlcv_binance() carries REAL non-zero volume (unlike TwelveData BTC/USD)",
          float(df["volume"].sum()) > 0)
    check("get_ohlcv_binance() volume spike on the last bar is preserved",
          float(df["volume"].iloc[-1]) > float(df["volume"].iloc[:-1].mean()) * 1.5)
finally:
    bf.requests.get = original_requests_get

# ── get_ohlcv_binance(): failure modes never raise ──────────────────────
def fake_get_empty(url, params=None, timeout=None):
    return _FakeResp([])

def fake_get_error_dict(url, params=None, timeout=None):
    return _FakeResp({"code": -1121, "msg": "Invalid symbol."})

def fake_get_raises(url, params=None, timeout=None):
    raise RuntimeError("network down")

bf.requests.get = fake_get_empty
try:
    check("get_ohlcv_binance() returns None on an empty klines list",
          get_ohlcv_binance("1h", 10, symbol="BTCUSD") is None)
finally:
    bf.requests.get = original_requests_get

bf.requests.get = fake_get_error_dict
try:
    check("get_ohlcv_binance() returns None on a Binance error payload (dict, not list)",
          get_ohlcv_binance("1h", 10, symbol="BTCUSD") is None)
finally:
    bf.requests.get = original_requests_get

bf.requests.get = fake_get_raises
try:
    check("get_ohlcv_binance() swallows a network exception and returns None",
          get_ohlcv_binance("1h", 10, symbol="BTCUSD") is None)
finally:
    bf.requests.get = original_requests_get


# ── alpha_buffalo_signal.get_ohlcv(): flag default OFF, unchanged ──────
def read_flag_in_subprocess(env_overrides=None):
    env = os.environ.copy()
    env.pop("ALPHA_BTC_BINANCE_VOLUME_ENABLED", None)
    env.setdefault("TELEGRAM_TOKEN", "test-token")
    if env_overrides:
        env.update(env_overrides)
    code = "import json, alpha_buffalo_signal as m; print(json.dumps({'flag': m.BTC_BINANCE_VOLUME_ENABLED}))"
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env,
                             capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        raise RuntimeError(f"subprocess failed: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])

d = read_flag_in_subprocess()
check("default: ALPHA_BTC_BINANCE_VOLUME_ENABLED is False", d["flag"] is False)
d2 = read_flag_in_subprocess({"ALPHA_BTC_BINANCE_VOLUME_ENABLED": "true"})
check("override: ALPHA_BTC_BINANCE_VOLUME_ENABLED=true takes effect", d2["flag"] is True)

import alpha_buffalo_signal as runtime

# runtime.requests and bf.requests are the SAME module object (both files
# just `import requests`), so patching .get on either name patches both --
# one fake dispatching on the URL is what actually distinguishes the two
# real endpoints being hit.
twelvedata_calls = []
binance_calls = []
def fake_dispatch_get(url, params=None, timeout=None):
    if "binance" in url:
        binance_calls.append(dict(params))
        return _FakeResp(_fake_klines_rows(30))
    twelvedata_calls.append(dict(params))
    return _FakeResp({"values": []})

original_runtime_requests_get = runtime.requests.get
runtime.requests.get = fake_dispatch_get
try:
    # Flag OFF (module default from a fresh import above) -> BTC still
    # goes through TwelveData exactly as before.
    check("flag defaults to False on the imported runtime module",
          runtime.BTC_BINANCE_VOLUME_ENABLED is False)
    runtime.get_ohlcv("4h", 10, symbol="BTCUSD")
    check("flag OFF: BTC still calls TwelveData (byte-identical old path)",
          len(twelvedata_calls) == 1 and twelvedata_calls[0]["symbol"] == "BTC/USD")
    check("flag OFF: Binance is never called for BTC", len(binance_calls) == 0)

    runtime.get_ohlcv("4h", 10, symbol="XAUUSD")
    check("flag OFF: non-BTC symbols still call TwelveData regardless of the new flag",
          len(twelvedata_calls) == 2 and twelvedata_calls[1]["symbol"] == "XAU/USD")

    # Flag ON -> BTC routes to Binance; other symbols still untouched.
    runtime.BTC_BINANCE_VOLUME_ENABLED = True
    twelvedata_calls.clear()
    binance_calls.clear()
    df_btc = runtime.get_ohlcv("15min", 30, symbol="BTCUSD")
    check("flag ON: BTC is routed to Binance instead of TwelveData",
          len(binance_calls) == 1 and len(twelvedata_calls) == 0)
    check("flag ON: BTC frame carries real, non-zero volume",
          df_btc is not None and float(df_btc["volume"].sum()) > 0)

    runtime.get_ohlcv("4h", 10, symbol="US100")
    check("flag ON: non-BTC symbols (e.g. US100) are completely unaffected, still TwelveData",
          len(twelvedata_calls) == 1 and twelvedata_calls[0]["symbol"] == runtime.EXTRA_SYMBOL_TICKERS["US100"])
finally:
    runtime.requests.get = original_runtime_requests_get
    runtime.BTC_BINANCE_VOLUME_ENABLED = False

# ── flag ON + Binance fails -> falls back to TwelveData, never returns ──
# None outright. Regression guard for a real bug this caught: without a
# fallback, a Binance-side failure (rate limit, outage, a region getting
# geo-blocked) would silently kill BTC's ENTIRE signal pipeline the moment
# this flag is on -- strictly worse than today's reliable-but-no-volume
# TwelveData-only path. get_ohlcv() must fall back, not fail outright.
def fake_binance_down_dispatch_get(url, params=None, timeout=None):
    if "binance" in url:
        binance_calls.append(dict(params))
        return _FakeResp({"code": 0, "msg": "Service unavailable from a restricted location"})
    twelvedata_calls.append(dict(params))
    return _FakeResp({"values": [
        {"datetime": "2026-01-01 00:00:00", "open": "60000", "high": "60100",
         "low": "59900", "close": "60050"}
    ]})

runtime.requests.get = fake_binance_down_dispatch_get
runtime.BTC_BINANCE_VOLUME_ENABLED = True
twelvedata_calls.clear()
binance_calls.clear()
try:
    df_fallback = runtime.get_ohlcv("15min", 30, symbol="BTCUSD")
    check("flag ON + Binance down: falls back to TwelveData instead of returning None",
          df_fallback is not None and len(twelvedata_calls) == 1)
    check("flag ON + Binance down: TwelveData fallback still resolves BTC/USD ticker correctly",
          twelvedata_calls[0]["symbol"] == "BTC/USD")
    check("flag ON + Binance down: fallback frame has no volume (matches today's known TwelveData behavior)",
          "volume" not in df_fallback.columns)
finally:
    runtime.requests.get = original_runtime_requests_get
    runtime.BTC_BINANCE_VOLUME_ENABLED = False


# ── Downstream: real volume activates previously-dormant analysis ──────
from trend_monitor import calc_tf_trend, PRESSURE_SELL, PRESSURE_BUY
from signal_engine import is_high_volume

def _make_df(n, base=60000.0, spike_last=True, direction=1):
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    closes = base + np.arange(n) * direction * 5.0
    opens = closes - direction * 3.0
    highs = np.maximum(opens, closes) + 5
    lows = np.minimum(opens, closes) - 5
    vols = np.full(n, 100.0)
    if spike_last:
        vols[-1] = 1000.0  # clear >1.5x / >1.8x spike vs the trailing average
    return pd.DataFrame({"open": opens, "high": highs, "low": lows,
                          "close": closes, "volume": vols}, index=idx)

# Without a volume column at all (today's real BTC/TwelveData situation):
df_no_vol = _make_df(60, direction=1, spike_last=True).drop(columns=["volume"])
tf_no_vol = calc_tf_trend(df_no_vol, "M15")
check("without volume data: Pressure detection stays a silent no-op (dormant today for BTC)",
      tf_no_vol.pressure == "")
check("without volume data: is_high_volume() safely returns False (no KeyError)",
      is_high_volume(df_no_vol) is False)

# With real Binance-sourced volume (bullish candle + volume spike):
df_with_vol = _make_df(60, direction=1, spike_last=True)
tf_with_vol = calc_tf_trend(df_with_vol, "M15")
check("with real volume: Pressure detection activates (Buying Pressure on a bullish spike bar)",
      tf_with_vol.pressure == PRESSURE_BUY)

df_with_vol_sell = _make_df(60, direction=-1, spike_last=True)
tf_with_vol_sell = calc_tf_trend(df_with_vol_sell, "M15")
check("with real volume: Pressure detection activates (Selling Pressure on a bearish spike bar)",
      tf_with_vol_sell.pressure == PRESSURE_SELL)

check("with real volume: is_high_volume() detects the spike bar",
      is_high_volume(df_with_vol) is True)


# ── /debug/binance_check endpoint: lets anyone curl the deployed service ─
# to confirm ITS OWN outbound network can reach Binance -- not just whatever
# environment developed/tested this feature. Root cause: api.binance.com
# geo-blocked one environment already; this is how a Railway deploy can be
# verified directly instead of only inferred from logs after the fact.
from fastapi.testclient import TestClient
client = TestClient(runtime.app)

original_get_ohlcv_binance = runtime.get_ohlcv_binance
def fake_ok_binance(*a, **kw):
    return _make_df(10, direction=1, spike_last=False)
runtime.get_ohlcv_binance = fake_ok_binance
try:
    resp = client.get("/debug/binance_check")
    body = resp.json()
    check("/debug/binance_check: 200 OK", resp.status_code == 200)
    check("/debug/binance_check: ok=True when Binance reachable", body.get("ok") is True)
    check("/debug/binance_check: reports the base URL in use", body.get("base_url") == bf.BINANCE_API_BASE)
    check("/debug/binance_check: reports real numbers (rows/last_close/last_volume), not just a boolean",
          "rows" in body and "last_close" in body and "last_volume" in body)
finally:
    runtime.get_ohlcv_binance = original_get_ohlcv_binance

def fake_down_binance(*a, **kw):
    return None
runtime.get_ohlcv_binance = fake_down_binance
try:
    resp2 = client.get("/debug/binance_check")
    body2 = resp2.json()
    check("/debug/binance_check: ok=False when Binance unreachable, still 200 (not a 500)",
          resp2.status_code == 200 and body2.get("ok") is False)
    check("/debug/binance_check: failure response still names the base URL for debugging",
          body2.get("base_url") == bf.BINANCE_API_BASE)
finally:
    runtime.get_ohlcv_binance = original_get_ohlcv_binance

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All Binance BTC volume regression checks passed.")
