"""Diagnostic-only multi-symbol (NAS100, BTC) regressions.

Protects the core safety contract of the diagnostic expansion scan added in
alpha_buffalo_signal.py: it is read-only, OWNER-Telegram-only, defaults OFF,
never claims the EA is executing, and can never be confused with (or
suppress/be suppressed by) the production XAUUSD signal path. See
MULTI_SYMBOL_NAS100_BTC_PROPOSAL.md for the full rollout plan this guards.
"""
from __future__ import annotations

import inspect

from scripts.regression_cases.common import *

import alpha_buffalo_signal as runtime


def _open_diagnostic_payload(symbol: str = "BTCUSD") -> dict:
    return {
        "symbol": symbol,
        "signal": {
            "decision": {"score": 8},
        },
        "ea": {
            "action": "OPEN",
            "direction": "BUY",
            "entry": 60000.0,
            "sl": 59000.0,
            "tp_final": 62000.0,
            "score": 8,
            "session": "NY",
            "signal_id": "diag-1",
            "directional_levels_ok": True,
            "levels_ready": True,
            "rr_ok": True,
        },
    }


def test_diagnostic_symbols_disabled_by_default():
    assert_true(
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED is False,
        "diagnostic multi-symbol scan must default to disabled",
    )


def test_diagnostic_broadcast_is_noop_while_feature_flag_is_off():
    original_flag = runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED
    original_send = runtime.send_telegram_message
    sent = []
    try:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = False
        runtime.send_telegram_message = lambda *a, **k: sent.append(a) or True
        result = runtime.maybe_broadcast_diagnostic_signal(
            _open_diagnostic_payload(), verified_data_symbol=True
        )
        assert_true(result is False, "diagnostic broadcast must stay silent while flag is off")
        assert_true(not sent, "no Telegram send should be attempted while flag is off")
    finally:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = original_flag
        runtime.send_telegram_message = original_send


def test_diagnostic_broadcast_uses_owner_audience_and_never_claims_ea_execution():
    original_flag = runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED
    original_send = runtime.send_telegram_message
    original_market_open = runtime._telegram_market_is_open
    original_enabled = runtime._telegram_enabled
    calls = []
    try:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = True
        runtime._telegram_market_is_open = lambda *a, **k: True
        runtime._telegram_enabled = lambda audience="GROUP": True
        runtime.send_telegram_message = (
            lambda text, **kwargs: calls.append((text, kwargs.get("audience"))) or True
        )
        result = runtime.maybe_broadcast_diagnostic_signal(
            _open_diagnostic_payload(symbol="BTCUSD"), verified_data_symbol=True
        )
        assert_true(result is True, "a fresh diagnostic OPEN candidate should send once enabled")
        assert_equal(len(calls), 1, "exactly one Telegram send expected")
        text, audience = calls[0]
        assert_equal(audience, "OWNER", "diagnostic broadcast must never use the public GROUP audience")
        assert_true("EA Executing" not in text, "diagnostic message must never claim the EA executed")
        assert_true("EA executing" not in text, "diagnostic message must never claim the EA is executing")
        assert_true(
            "Not routed to EA" in text,
            "diagnostic message must explicitly say it was not routed to the EA",
        )
        assert_true("DIAGNOSTIC" in text, "diagnostic message must be clearly labeled")
    finally:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = original_flag
        runtime.send_telegram_message = original_send
        runtime._telegram_market_is_open = original_market_open
        runtime._telegram_enabled = original_enabled


def test_diagnostic_unverified_symbol_carries_a_warning():
    original_flag = runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED
    original_send = runtime.send_telegram_message
    original_market_open = runtime._telegram_market_is_open
    original_enabled = runtime._telegram_enabled
    calls = []
    try:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = True
        runtime._telegram_market_is_open = lambda *a, **k: True
        runtime._telegram_enabled = lambda audience="GROUP": True
        runtime.send_telegram_message = (
            lambda text, **kwargs: calls.append(text) or True
        )
        runtime.maybe_broadcast_diagnostic_signal(
            _open_diagnostic_payload(symbol="NAS100"), verified_data_symbol=False
        )
        assert_true(calls, "expected a diagnostic Telegram send for the unverified symbol")
        assert_true(
            "unverified" in calls[0].lower(),
            "an unverified data-provider symbol must carry an explicit warning",
        )
    finally:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = original_flag
        runtime.send_telegram_message = original_send
        runtime._telegram_market_is_open = original_market_open
        runtime._telegram_enabled = original_enabled


def test_diagnostic_dedup_is_isolated_from_production_signal_key():
    original_flag = runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED
    original_send = runtime.send_telegram_message
    original_market_open = runtime._telegram_market_is_open
    original_enabled = runtime._telegram_enabled
    original_prod_key = runtime.LAST_TELEGRAM_SIGNAL_KEY
    original_diag_keys = dict(runtime.LAST_TELEGRAM_DIAGNOSTIC_KEYS)
    try:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = True
        runtime._telegram_market_is_open = lambda *a, **k: True
        runtime._telegram_enabled = lambda audience="GROUP": True
        runtime.send_telegram_message = lambda *a, **k: True
        runtime.LAST_TELEGRAM_SIGNAL_KEY = "should-not-be-touched"
        runtime.maybe_broadcast_diagnostic_signal(
            _open_diagnostic_payload(symbol="BTCUSD"), verified_data_symbol=True
        )
        assert_equal(
            runtime.LAST_TELEGRAM_SIGNAL_KEY,
            "should-not-be-touched",
            "diagnostic broadcast must never write to the production XAUUSD dedup slot",
        )
        assert_true(
            "BTCUSD" in runtime.LAST_TELEGRAM_DIAGNOSTIC_KEYS,
            "diagnostic dedup must be tracked per symbol",
        )
    finally:
        runtime.ALPHA_DIAGNOSTIC_SYMBOLS_ENABLED = original_flag
        runtime.send_telegram_message = original_send
        runtime._telegram_market_is_open = original_market_open
        runtime._telegram_enabled = original_enabled
        runtime.LAST_TELEGRAM_SIGNAL_KEY = original_prod_key
        runtime.LAST_TELEGRAM_DIAGNOSTIC_KEYS = original_diag_keys


_EA_PUBLISHER_NAME = "_publish_python_" + "entry_command"
_LATEST_CACHE_WRITER_NAME = "_set_latest_" + "signal"


def test_diagnostic_loop_never_calls_the_ea_command_publisher():
    """Structural guard: the diagnostic scan function's body must not call
    the function that queues an EA command, nor the one that overwrites the
    production /signal/latest cache, regardless of future edits to its body.
    (Names are split above so this assertion checks the real call, not a
    docstring mention of the same name.)
    """
    source = inspect.getsource(runtime._diagnostic_multi_symbol_loop)
    assert_true(
        _EA_PUBLISHER_NAME not in source,
        "diagnostic loop must never call the EA command publisher",
    )
    assert_true(
        _LATEST_CACHE_WRITER_NAME not in source,
        "diagnostic loop must never overwrite the production latest-signal cache",
    )
