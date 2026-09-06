#!/usr/bin/env python3
"""
Regression tests for the opt-in ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED
feature: batching the Trend Update messages for the extra symbols (BTC/
US100/JPN225) into ONE combined Telegram message on a shared ~2h timer,
instead of each symbol sending its own independently-timed message.

Covers:
  - new config defaults are OFF/unchanged unless explicitly set (digest
    disabled by default, interval floors at 7200s/2h)
  - trend_monitor.format_multi_symbol_trend_digest() renders all given
    symbols' session/price/bias/per-TF state/Pressure flags into one
    message
  - run_extra_symbol_pass() always returns the computed TrendResult (or
    None on a data/error early-exit) regardless of digest mode
  - digest mode ON: run_extra_symbol_pass() does NOT send its own
    per-symbol Trend Update message (that responsibility moves to
    signal_loop()'s batched send) -- but BUY/SELL signal messages are
    STILL sent immediately, per symbol, completely unaffected
  - digest mode OFF (default): behavior is unchanged from before this
    feature -- each symbol still sends its own Trend Update on its own
    independent per-symbol timer
  - _extra_trend_digest_allowed() is a single SHARED throttle (unlike the
    per-symbol _extra_trend_last_sent dict) -- one send resets the timer
    for all symbols at once

Run: python3 scripts/test_extra_trend_digest.py
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
    env = os.environ.copy()
    env.pop("ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED", None)
    env.pop("ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_INTERVAL_SEC", None)
    env.setdefault("TELEGRAM_TOKEN", "test-token")
    if env_overrides:
        env.update(env_overrides)
    code = (
        "import json, alpha_buffalo_signal as m; "
        "print(json.dumps({"
        "'digest_enabled': m.EXTRA_TREND_DIGEST_ENABLED, "
        "'digest_interval': m.EXTRA_TREND_DIGEST_INTERVAL_SEC, "
        "}))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"subprocess failed: {result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


# ── Defaults: digest OFF, 2h interval ───────────────────────────────────
d = read_defaults_in_subprocess()
check("default: ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED is False",
      d["digest_enabled"] is False)
check("default: ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_INTERVAL_SEC floors at 2 hours",
      d["digest_interval"] == 7200)

d2 = read_defaults_in_subprocess({
    "ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED": "true",
    "ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_INTERVAL_SEC": "3600",
})
check("override: ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED=true takes effect",
      d2["digest_enabled"] is True)
check("override: custom digest interval respected",
      d2["digest_interval"] == 3600)

# ── format_multi_symbol_trend_digest(): renders all symbols in one message ─
from trend_monitor import format_multi_symbol_trend_digest, TrendResult, TFTrend

def _mk_trend(symbol, bias, session="NY", price=12345.6, pressure=""):
    tf = TFTrend(tf="M15", state="Impulse Up", emoji="⬆️", pressure=pressure,
                 ema20=1.0, ema50=1.0, price=price)
    tf_plain = TFTrend(tf="H1", state="Sideways", emoji="➡️", pressure="",
                        ema20=1.0, ema50=1.0, price=price)
    tf_h4 = TFTrend(tf="H4", state="Pullback Down", emoji="↗️", pressure="",
                     ema20=1.0, ema50=1.0, price=price)
    return TrendResult(symbol=symbol, session=session, price=price,
                        m15=tf, h1=tf_plain, h4=tf_h4, bias=bias,
                        action="WAIT_AND_SEE", timestamp="2026-09-06T00:00:00")

results = [
    ("BTCUSD", _mk_trend("BTCUSD", "BUY", price=80000.0, pressure="Buying Pressure")),
    ("US100",  _mk_trend("US100",  "NEUTRAL", price=20000.0)),
    ("JPN225", _mk_trend("JPN225", "SELL", price=39000.0)),
]
digest_msg = format_multi_symbol_trend_digest(results)
check("digest message mentions every symbol",
      all(sym in digest_msg for sym, _ in results))
check("digest message is a single combined message (one header)",
      digest_msg.count("TREND UPDATE (Multi-Symbol)") == 1)
check("digest message shows each symbol's bias",
      "Bias: BUY" in digest_msg and "Bias: NEUTRAL" in digest_msg and "Bias: SELL" in digest_msg)
check("digest message carries Pressure flags when present",
      "Buying Pressure" in digest_msg)
check("digest message includes the standard disclaimer",
      "Not financial advice" in digest_msg)

empty_digest = format_multi_symbol_trend_digest([])
check("digest message with zero symbols still renders header/footer without crashing",
      "TREND UPDATE (Multi-Symbol)" in empty_digest and "Not financial advice" in empty_digest)

# ── run_extra_symbol_pass(): return value + digest-mode gating ─────────
import alpha_buffalo_signal as runtime

sent = []
def fake_send_telegram(msg, chat_id=None):
    sent.append((chat_id, msg))

def fake_get_ohlcv(interval, bars, symbol=None):
    import pandas as pd, numpy as np
    n = 60
    idx = pd.date_range("2026-01-01", periods=n, freq="h", tz="UTC")
    price = np.linspace(100, 110, n)
    return pd.DataFrame({
        "open": price, "high": price + 1, "low": price - 1, "close": price,
        "volume": np.ones(n),
    }, index=idx)

original_send = runtime.send_telegram
original_get_ohlcv = runtime.get_ohlcv
original_compute_signal = runtime.compute_signal
runtime.send_telegram = fake_send_telegram
runtime.get_ohlcv = fake_get_ohlcv
runtime.compute_signal = lambda *a, **k: None  # no BUY/SELL signal this pass
runtime.EXTRA_NOTIFY_IDS = ["-100111"]

try:
    # -- digest mode OFF (default): per-symbol trend update still sent,
    #    still returns the TrendResult too
    runtime.EXTRA_TREND_DIGEST_ENABLED = False
    runtime.EXTRA_TREND_UPDATE_ENABLED = True
    runtime.EXTRA_TREND_UPDATE_MIN_INTERVAL_SEC = 0
    runtime._extra_trend_last_sent = {}
    sent.clear()
    trend = runtime.run_extra_symbol_pass("BTCUSD")
    check("digest OFF: run_extra_symbol_pass() returns a TrendResult",
          trend is not None and trend.symbol == "BTCUSD")
    check("digest OFF: per-symbol Trend Update was sent (old behavior unchanged)",
          any("TREND UPDATE" in m and "Multi-Symbol" not in m for _, m in sent))

    # -- digest mode ON: per-symbol trend update NOT sent, but TrendResult
    #    is still returned for signal_loop() to batch
    runtime.EXTRA_TREND_DIGEST_ENABLED = True
    sent.clear()
    trend2 = runtime.run_extra_symbol_pass("BTCUSD")
    check("digest ON: run_extra_symbol_pass() still returns a TrendResult",
          trend2 is not None and trend2.symbol == "BTCUSD")
    check("digest ON: NO per-symbol Trend Update is sent from run_extra_symbol_pass()",
          not any("TREND UPDATE" in m for _, m in sent))

    # -- BUY/SELL signal sending is unaffected by digest mode either way
    fake_sig = type("Sig", (), dict(
        direction="BUY", signal_type="V5_SNIPER", entry=100.0, sl=99.0,
        tp_final=105.0, partial=[], pattern="", score=8,
    ))()
    runtime.compute_signal = lambda *a, **k: fake_sig
    for digest_flag in (False, True):
        runtime.EXTRA_TREND_DIGEST_ENABLED = digest_flag
        sent.clear()
        runtime.run_extra_symbol_pass("BTCUSD")
        check(f"signal message still sent when EXTRA_TREND_DIGEST_ENABLED={digest_flag}",
              any("BTCUSD" in m or "Signal" in m or chat_id == "-100111" for chat_id, m in sent))

    # -- missing data -> returns None (unchanged early-exit contract)
    runtime.get_ohlcv = lambda *a, **k: None
    none_trend = runtime.run_extra_symbol_pass("BTCUSD")
    check("run_extra_symbol_pass() returns None when OHLCV data is unavailable",
          none_trend is None)

finally:
    runtime.send_telegram = original_send
    runtime.get_ohlcv = original_get_ohlcv
    runtime.compute_signal = original_compute_signal

# ── _extra_trend_digest_allowed(): single SHARED throttle across symbols ─
runtime.EXTRA_TREND_DIGEST_ENABLED = True
runtime.EXTRA_TREND_DIGEST_INTERVAL_SEC = 100
runtime._extra_trend_digest_last_sent = 0.0

check("digest throttle: first call is allowed",
      runtime._extra_trend_digest_allowed() is True)
check("digest throttle: immediate second call is blocked (shared, not per-symbol)",
      runtime._extra_trend_digest_allowed() is False)

runtime.EXTRA_TREND_DIGEST_ENABLED = False
check("digest throttle: disabled flag blocks every call regardless of timing",
      runtime._extra_trend_digest_allowed() is False)

print()
if FAILS:
    print(f"{len(FAILS)} FAILED: {FAILS}")
    sys.exit(1)
print("All extra-symbols trend-digest regression checks passed.")
