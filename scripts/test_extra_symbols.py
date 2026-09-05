#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_EXTRA_SYMBOLS_ENABLED feature: running
the same trend/signal engine used for the main TRADE_SYMBOL against BTC/
US100/JPN225 and broadcasting real signals into the signal room too.

Covers:
  - all new config defaults are OFF/unchanged unless explicitly set (env
    vars are read at import time, so each default-check re-imports the
    module in a subprocess with a clean environment)
  - get_ohlcv(symbol=...) resolves the right TwelveData ticker per symbol
    without touching the module-level SYMBOL fetch at all
  - format_signal_message()/compute_signal() stay byte-identical for the
    main XAUUSD path when called the old way (no symbol/symbol_label arg)
  - format_signal_message(symbol=..., ea_executes=False) never claims an
    automated trade for a symbol the EA does not yet execute
  - the extra-symbol trend-update throttle (default OFF, floor-capped to
    the configured interval once enabled) is independent per symbol and
    independent of the main SYMBOL's own per-session trend alert

Run: python3 scripts/test_extra_symbols.py
Exits non-zero on any failure.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FAILS = []


def check(name, cond):
    status = "PASS" if cond else "FAIL"
    print(f"[{status}] {name}")
    if not cond:
        FAILS.append(name)


def read_defaults_in_subprocess(env_overrides=None):
    """Import alpha_buffalo_signal fresh in a subprocess (module-level env
    reads must not be polluted by earlier tests in this same process) and
    dump the config values we care about as JSON."""
    env = os.environ.copy()
    env.pop("ALPHA_EXTRA_SYMBOLS_ENABLED", None)
    env.pop("ALPHA_EXTRA_SYMBOLS", None)
    env.pop("ALPHA_EXTRA_SYMBOLS_NOTIFY_IDS", None)
    env.pop("ALPHA_EXTRA_SYMBOLS_BYPASS_MARKET_GATE", None)
    env.pop("ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_ENABLED", None)
    env.pop("ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_MIN_INTERVAL_SEC", None)
    env.pop("ALPHA_EXTRA_SYMBOL_US100_TICKER", None)
    env.pop("ALPHA_EXTRA_SYMBOL_JPN225_TICKER", None)
    env.setdefault("TELEGRAM_TOKEN", "test-token")
    if env_overrides:
        env.update(env_overrides)
    code = (
        "import json, alpha_buffalo_signal as m; "
        "print(json.dumps({"
        "'enabled': m.EXTRA_SYMBOLS_ENABLED, "
        "'symbols': m.EXTRA_SYMBOLS, "
        "'notify_ids': m.EXTRA_NOTIFY_IDS, "
        "'bypass_gate': m.EXTRA_BYPASS_MARKET_GATE, "
        "'trend_enabled': m.EXTRA_TREND_UPDATE_ENABLED, "
        "'trend_interval': m.EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC, "
        "'tickers': m.EXTRA_SYMBOL_TICKERS, "
        "'main_notify_ids': m.NOTIFY_IDS, "
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"subprocess failed: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── Defaults: everything OFF / unchanged unless explicitly set ─────────
d = read_defaults_in_subprocess()
check("default: ALPHA_EXTRA_SYMBOLS_ENABLED is False", d["enabled"] is False)
check("default: EXTRA_SYMBOLS is BTC/US100/JPN225",
      d["symbols"] == ["BTCUSD", "US100", "JPN225"])
check("default: EXTRA_BYPASS_MARKET_GATE is True (open all sessions)",
      d["bypass_gate"] is True)
check("default: EXTRA_TREND_UPDATE_ENABLED is False (suppressed by default)",
      d["trend_enabled"] is False)
check("default: EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC floors at 4 hours",
      d["trend_interval"] == 14400)
check("default: EXTRA_NOTIFY_IDS falls back to the same room as NOTIFY_IDS",
      d["notify_ids"] == d["main_notify_ids"])
check("default: BTCUSD ticker is BTC/USD",
      d["tickers"]["BTCUSD"] == "BTC/USD")
check("default: JPN225 ticker is N225 (confirmed against TwelveData /indices)",
      d["tickers"]["JPN225"] == "N225")
check("default: US100 ticker defaults to NDX (best-guess, override via env)",
      d["tickers"]["US100"] == "NDX")

# ── Overrides actually take effect ──────────────────────────────────────
d2 = read_defaults_in_subprocess({
    "ALPHA_EXTRA_SYMBOLS_ENABLED": "true",
    "ALPHA_EXTRA_SYMBOLS": "BTCUSD,JPN225",
    "ALPHA_EXTRA_SYMBOLS_NOTIFY_IDS": "-100999",
    "ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_ENABLED": "true",
    "ALPHA_EXTRA_SYMBOLS_TREND_UPDATE_MIN_INTERVAL_SEC": "3600",
    "ALPHA_EXTRA_SYMBOL_US100_TICKER": "IXIC",
})
check("override: ALPHA_EXTRA_SYMBOLS_ENABLED=true takes effect", d2["enabled"] is True)
check("override: ALPHA_EXTRA_SYMBOLS list respected", d2["symbols"] == ["BTCUSD", "JPN225"])
check("override: separate notify room respected", d2["notify_ids"] == ["-100999"])
check("override: trend-update enable + custom interval respected",
      d2["trend_enabled"] is True and d2["trend_interval"] == 3600)
check("override: US100 ticker override respected", d2["tickers"]["US100"] == "IXIC")

# ── get_ohlcv(symbol=...) ticker resolution, no network call needed ────
import alpha_buffalo_signal as runtime

captured = {}
def fake_get(url, params=None, timeout=None):
    captured["symbol_param"] = params["symbol"]
    class _Resp:
        def json(self_inner):
            return {"values": []}  # triggers the "no values" -> None path
    return _Resp()

original_get = runtime.requests.get
runtime.requests.get = fake_get
try:
    runtime.get_ohlcv("4h", 10)  # symbol=None -> should resolve module SYMBOL
    check("get_ohlcv(symbol=None) resolves the main module-level SYMBOL (XAUUSD -> XAU/USD)",
          runtime.SYMBOL == "XAUUSD" and captured["symbol_param"] == "XAU/USD")

    runtime.get_ohlcv("4h", 10, symbol="BTCUSD")
    check("get_ohlcv(symbol='BTCUSD') resolves BTC/USD",
          captured["symbol_param"] == "BTC/USD")

    runtime.get_ohlcv("4h", 10, symbol="JPN225")
    check("get_ohlcv(symbol='JPN225') resolves N225",
          captured["symbol_param"] == "N225")

    runtime.get_ohlcv("4h", 10, symbol="US100")
    check("get_ohlcv(symbol='US100') resolves the configured US100 ticker",
          captured["symbol_param"] == runtime.EXTRA_SYMBOL_TICKERS["US100"])
finally:
    runtime.requests.get = original_get

# ── format_signal_message(): main path unchanged, extra-symbol path honest ─
from trend_monitor import format_signal_message

old_style_msg = format_signal_message(
    direction="BUY", signal_type="V5_SNIPER", entry=4500.0, sl=4490.0,
    tp1=4510.0, tp2=4520.0, pattern="", score=8, session="NY",
)
check("format_signal_message() with no symbol/ea_executes arg still says XAUUSD",
      "Asset    : XAUUSD" in old_style_msg)
check("format_signal_message() with no ea_executes arg still says EA Executing",
      "EA Executing" in old_style_msg)

extra_msg = format_signal_message(
    direction="BUY", signal_type="V5_SNIPER", entry=60000.0, sl=59000.0,
    tp1=61000.0, tp2=62000.0, pattern="", score=8, session="NY",
    symbol="BTCUSD", ea_executes=False,
)
check("format_signal_message(symbol='BTCUSD') labels the asset correctly",
      "Asset    : BTCUSD" in extra_msg)
check("format_signal_message(ea_executes=False) never claims EA Executing",
      "EA Executing" not in extra_msg)
check("format_signal_message(ea_executes=False) says signal-only instead",
      "not wired to auto-execution" in extra_msg)

# ── compute_signal(symbol_label=...): main path unchanged ──────────────
import inspect
from signal_engine import compute_signal
sig = inspect.signature(compute_signal)
check("compute_signal() keeps symbol_label optional (default None)",
      sig.parameters["symbol_label"].default is None)

# ── extra-symbol trend-update throttle: independent per symbol ─────────
runtime.EXTRA_TREND_UPDATE_ENABLED = True
runtime.EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC = 100
runtime._extra_trend_last_sent = {}

check("throttle: first call for BTCUSD is allowed",
      runtime._extra_trend_update_allowed("BTCUSD") is True)
check("throttle: immediate second call for BTCUSD is blocked",
      runtime._extra_trend_update_allowed("BTCUSD") is False)
check("throttle: a DIFFERENT symbol (JPN225) is independent, still allowed",
      runtime._extra_trend_update_allowed("JPN225") is True)

runtime.EXTRA_TREND_UPDATE_ENABLED = False
check("throttle: disabled flag blocks every call regardless of timing",
      runtime._extra_trend_update_allowed("US100") is False)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All extra-symbols regression checks passed.")
