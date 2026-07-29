"""Market-data resilience and Python command observability regressions."""
from __future__ import annotations

import os
import tempfile
import time

from fastapi import HTTPException

from scripts.regression_cases.common import *


def _cached_market_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range(
                "2026-07-28 00:00",
                periods=60,
                freq="15min",
                tz="UTC",
            ),
            "open": [4000.0 + index for index in range(60)],
            "high": [4001.0 + index for index in range(60)],
            "low": [3999.0 + index for index in range(60)],
            "close": [4000.5 + index for index in range(60)],
        }
    )


def test_tf_cache_defaults_follow_confirmed_candle_cadence() -> None:
    assert_equal(runtime.TF_FETCH_TTL_SECONDS["5min"], 300, "M5 fetch cadence")
    assert_equal(runtime.TF_FETCH_TTL_SECONDS["15min"], 900, "M15 fetch cadence")
    assert_equal(runtime.TF_FETCH_TTL_SECONDS["1h"], 3600, "H1 fetch cadence")
    assert_equal(runtime.TF_FETCH_TTL_SECONDS["4h"], 14400, "H4 fetch cadence")


def test_tf_cache_refreshes_on_candle_boundary() -> None:
    assert_equal(
        runtime._tf_epoch_bucket("15min", 899.0),
        0,
        "M15 remains in the original candle bucket",
    )
    assert_equal(
        runtime._tf_epoch_bucket("15min", 900.0),
        1,
        "M15 refreshes immediately at the next candle boundary",
    )


def test_persisted_tf_cache_recovers_bounded_provider_outage() -> None:
    original_directory = os.environ.get("ALPHA_TF_CACHE_DIR")
    original_fetch = runtime.fetch_twelvedata
    original_cache = dict(runtime.TF_DATA_CACHE)
    try:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["ALPHA_TF_CACHE_DIR"] = directory
            runtime.TF_DATA_CACHE.clear()
            fetched_at = time.time() - 120
            runtime._persist_tf_cache(
                "XAU/USD",
                "15min",
                _cached_market_frame(),
                fetched_at,
            )
            runtime.fetch_twelvedata = lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPException(status_code=502, detail="DATA_FETCH_HTTP_429")
            )

            result = runtime._fetch_cached_tf("XAU/USD", "15min")

            assert_equal(
                result.attrs.get("alpha_data_source"),
                "PERSISTED_FALLBACK",
                "provider outage uses durable cache",
            )
            assert_true(
                100 <= float(result.attrs.get("alpha_data_age_seconds", 0)) <= 180,
                "fallback preserves bounded cache age",
            )
    finally:
        runtime.fetch_twelvedata = original_fetch
        runtime.TF_DATA_CACHE.clear()
        runtime.TF_DATA_CACHE.update(original_cache)
        if original_directory is None:
            os.environ.pop("ALPHA_TF_CACHE_DIR", None)
        else:
            os.environ["ALPHA_TF_CACHE_DIR"] = original_directory


def test_expired_persisted_tf_cache_is_never_used_for_entry() -> None:
    original_directory = os.environ.get("ALPHA_TF_CACHE_DIR")
    original_fetch = runtime.fetch_twelvedata
    original_cache = dict(runtime.TF_DATA_CACHE)
    try:
        with tempfile.TemporaryDirectory() as directory:
            os.environ["ALPHA_TF_CACHE_DIR"] = directory
            runtime.TF_DATA_CACHE.clear()
            fetched_at = (
                time.time()
                - runtime.TF_MAX_STALE_SECONDS["15min"]
                - 60
            )
            runtime._persist_tf_cache(
                "XAU/USD",
                "15min",
                _cached_market_frame(),
                fetched_at,
            )
            runtime.fetch_twelvedata = lambda *args, **kwargs: (_ for _ in ()).throw(
                HTTPException(status_code=502, detail="DATA_FETCH_HTTP_429")
            )

            try:
                runtime._fetch_cached_tf("XAU/USD", "15min")
            except HTTPException as exc:
                assert_equal(exc.detail, "DATA_FETCH_HTTP_429", "original error preserved")
            else:
                raise AssertionError("expired cache must not pass as market data")
    finally:
        runtime.fetch_twelvedata = original_fetch
        runtime.TF_DATA_CACHE.clear()
        runtime.TF_DATA_CACHE.update(original_cache)
        if original_directory is None:
            os.environ.pop("ALPHA_TF_CACHE_DIR", None)
        else:
            os.environ["ALPHA_TF_CACHE_DIR"] = original_directory


def test_entry_freshness_requires_m5_only_for_sniper_mode() -> None:
    quality = {
        "intervals": {
            "15min": {"entry_fresh": True},
            "1h": {"entry_fresh": True},
            "4h": {"entry_fresh": True},
            "5min": {"entry_fresh": False},
        }
    }
    m15_ok, m15_reason = runtime._entry_data_fresh(
        {"entry_mode": "V4_BUY_M15_HA_FLIP"},
        quality,
    )
    sniper_ok, sniper_reason = runtime._entry_data_fresh(
        {"entry_mode": "V4_BUY_M5_SNIPER_RECLAIM"},
        quality,
    )

    assert_true(m15_ok, "M15 trigger must not be blocked by optional M5 outage")
    assert_equal(m15_reason, "ENTRY_DATA_FRESH", "M15 freshness reason")
    assert_true(not sniper_ok, "M5 sniper requires fresh closed M5 data")
    assert_equal(sniper_reason, "STALE_ENTRY_DATA:5min", "M5 stale reason")


def test_python_queue_reports_pipeline_error_instead_of_generic_hold() -> None:
    original_source = runtime.SIGNAL_SOURCE
    try:
        runtime.SIGNAL_SOURCE = "PYTHON"
        result = runtime._publish_python_entry_command(
            {
                "signal": {
                    "status": "ERROR",
                    "reason": "DATA_FETCH_HTTP_429",
                    "decision": {
                        "grade": "ERROR",
                        "error_code": "DATA_FETCH_HTTP_429",
                    },
                },
                "ea": {
                    "action": "WAIT",
                    "execution_state": "WATCH",
                },
            }
        )
        assert_equal(result["action"], "HOLD", "error never creates an OPEN")
        assert_equal(
            result["reason"],
            "PIPELINE_ERROR:DATA_FETCH_HTTP_429",
            "EA queue exposes safe root cause",
        )
    finally:
        runtime.SIGNAL_SOURCE = original_source
