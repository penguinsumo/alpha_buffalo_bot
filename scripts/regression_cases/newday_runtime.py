"""Newday market-map runtime regressions.

Protects the contract that newday context (runtime_layers/newday.py) is
diagnostic-only: it must degrade to "not available" cleanly when no map
exists on disk, and it must never be capable of vetoing a direction --
callers get an `aligned` hint, not a pass/fail gate.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

from scripts.regression_cases.common import *

from runtime_layers.newday import (
    load_latest_newday_map,
    newday_bias_for_direction,
    newday_diagnostic,
)


def _write_fake_map(map_dir: str, symbol: str, date_str: str, **overrides) -> None:
    payload = {
        "symbol": symbol,
        "map_date": date_str,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "daily_bias": "BULLISH",
        "asian_high": 2500.0,
        "asian_low": 2480.0,
        "previous_day_high": 2510.0,
        "previous_day_low": 2470.0,
        "previous_day_close": 2495.0,
        "projected_high": 2520.0,
        "projected_low": 2460.0,
    }
    payload.update(overrides)
    os.makedirs(map_dir, exist_ok=True)
    path = os.path.join(map_dir, f"{symbol}_{date_str}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh)


def test_newday_diagnostic_reports_unavailable_without_a_map():
    with tempfile.TemporaryDirectory() as tmp:
        with unittest_mock_patch_env(tmp):
            diag = newday_diagnostic("XAUUSD")
            assert diag["available"] is False, diag
            assert load_latest_newday_map("XAUUSD") is None


def test_newday_diagnostic_reads_the_latest_generated_map():
    with tempfile.TemporaryDirectory() as tmp:
        with unittest_mock_patch_env(tmp):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _write_fake_map(tmp, "XAUUSD", today, daily_bias="BEARISH")
            diag = newday_diagnostic("XAUUSD")
            assert diag["available"] is True, diag
            assert diag["daily_bias"] == "BEARISH", diag
            assert diag["stale"] is False, diag


def test_newday_bias_is_a_hint_never_a_gate():
    with tempfile.TemporaryDirectory() as tmp:
        with unittest_mock_patch_env(tmp):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            _write_fake_map(tmp, "XAUUSD", today, daily_bias="BULLISH")

            aligned = newday_bias_for_direction("XAUUSD", "BUY")
            disagreeing = newday_bias_for_direction("XAUUSD", "SELL")

            # The contract is: this function only ever returns an
            # informational `aligned` flag (True/False/None). It has no
            # `allowed`/`blocked` field at all -- there is nothing here
            # a caller could wire up as an entry veto even by mistake.
            assert "allowed" not in aligned
            assert "blocked" not in aligned
            assert aligned["aligned"] is True
            assert disagreeing["aligned"] is False


def test_newday_diagnostic_never_raises_on_corrupt_map_file():
    with tempfile.TemporaryDirectory() as tmp:
        with unittest_mock_patch_env(tmp):
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            os.makedirs(tmp, exist_ok=True)
            path = os.path.join(tmp, f"XAUUSD_{today}.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write("{not valid json")
            diag = newday_diagnostic("XAUUSD")
            assert diag["available"] is False, diag


class unittest_mock_patch_env:
    """Tiny context manager to set/restore ALPHA_MARKET_MAP_DIR without
    pulling in unittest.mock.patch.dict just for one env var."""

    def __init__(self, value: str):
        self._value = value
        self._prev = None

    def __enter__(self):
        self._prev = os.environ.get("ALPHA_MARKET_MAP_DIR")
        os.environ["ALPHA_MARKET_MAP_DIR"] = self._value
        return self

    def __exit__(self, *exc):
        if self._prev is None:
            os.environ.pop("ALPHA_MARKET_MAP_DIR", None)
        else:
            os.environ["ALPHA_MARKET_MAP_DIR"] = self._prev
        return False
