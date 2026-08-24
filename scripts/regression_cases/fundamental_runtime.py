"""Fundamental context regressions.

Protects the contract that fundamental/context.py is diagnostic-only and
network-failure-safe: it must never raise (all four sources fail closed to
a neutral snapshot when offline, matching this sandboxed CI's lack of
outbound network), and its combined-adjustment helper must carry no
allowed/blocked field a caller could mistakenly wire up as an entry gate.
"""
from __future__ import annotations

import contextlib
from unittest.mock import patch

from scripts.regression_cases.common import *

import fundamental.cot as cot_module
import fundamental.dxy as dxy_module
import fundamental.fear_greed as fg_module
import fundamental.news as news_module
from fundamental.context import fundamental_bias_for_direction, fundamental_diagnostic


@contextlib.contextmanager
def _force_network_failure():
    """Force every fundamental source to hit its except-branch, regardless
    of whether the machine running this test actually has internet access.
    This keeps the test deterministic in any CI environment, per the
    project's own Integration Contract requirement that regression tests
    not depend on live network availability (DO_NOT_PORT_DIRECTLY.md's
    'Online Backtest Scripts' warning applies here too).
    """

    def _raise(*args, **kwargs):
        raise ConnectionError("network disabled for regression test")

    # Reset each module's cache so a warm cache from an earlier test (or an
    # earlier run in the same process) can't mask a broken fallback path.
    dxy_module._dxy_cache = None
    dxy_module._cache_time = None
    fg_module._cache_value = 50
    fg_module._cache_label = "Neutral"
    fg_module._cache_time = None
    cot_module._cot_cache = {}
    cot_module._cache_time = None
    news_module._news_cache = []
    news_module._cache_time = None

    with patch("requests.get", side_effect=_raise), patch(
        "fundamental.dxy._fetch_dxy_yfinance", return_value=None
    ):
        yield


def test_fundamental_diagnostic_never_raises_offline():
    with _force_network_failure():
        diag = fundamental_diagnostic()
    assert_true(isinstance(diag, dict), "fundamental_diagnostic must return a dict")
    for key in ("dxy", "fear_greed", "cot", "news"):
        assert_true(key in diag, f"fundamental diagnostic missing {key}")


def test_fundamental_bias_has_no_gating_fields():
    with _force_network_failure():
        for direction in ("BUY", "SELL"):
            result = fundamental_bias_for_direction(direction)
            assert_true("allowed" not in result, f"{direction}: fundamental bias must not carry 'allowed'")
            assert_true("blocked" not in result, f"{direction}: fundamental bias must not carry 'blocked'")
            assert_true(isinstance(result["total_adj"], int), "total_adj must be an int")
            assert_true(isinstance(result["news_safe"], bool), "news_safe must be a bool")


def test_fundamental_bias_degrades_to_neutral_without_network():
    with _force_network_failure():
        buy = fundamental_bias_for_direction("BUY")
        sell = fundamental_bias_for_direction("SELL")
    assert_equal(buy["total_adj"], 0, "offline BUY adjustment must degrade to neutral (0)")
    assert_equal(sell["total_adj"], 0, "offline SELL adjustment must degrade to neutral (0)")
    assert_true(buy["news_safe"], "offline news check must fail open to safe=True, never block trading")


def test_fundamental_cache_durations_default_to_the_timeframe_tier_table():
    """Timeframe-tier pass: defaults must match FINAL_RUNTIME_MAP.md's tier
    table (DXY/News ~H1, Fear&Greed ~H2, COT ~daily) when no env override is
    set -- this is a behavior-preserving regression, not a new default.
    """
    assert_equal(dxy_module.CACHE_MINUTES, 60, "DXY cache must default to the H1 tier (60 min)")
    assert_equal(news_module.CACHE_MINUTES, 60, "News cache must default to the H1 tier (60 min)")
    assert_equal(fg_module.CACHE_MINUTES, 120, "Fear&Greed cache must default to the H2 tier (120 min)")
    assert_equal(cot_module.CACHE_HOURS, 24, "COT cache must default to the daily tier (24h)")


def test_fundamental_cache_durations_are_env_configurable():
    """Each source's TTL must be read from an env var (not hardcoded), so a
    deploy can retune cadence without a code change. Verified at the source
    level rather than via importlib.reload, so this can't corrupt the module
    state other tests in this file depend on.
    """
    import inspect

    checks = [
        (dxy_module, "FUNDAMENTAL_DXY_CACHE_MINUTES"),
        (fg_module, "FUNDAMENTAL_FEAR_GREED_CACHE_MINUTES"),
        (news_module, "FUNDAMENTAL_NEWS_CACHE_MINUTES"),
        (cot_module, "FUNDAMENTAL_COT_CACHE_HOURS"),
    ]
    for module, env_name in checks:
        source = inspect.getsource(module)
        assert_true(
            f'os.getenv("{env_name}"' in source,
            f"{module.__name__} must read its cache duration from {env_name}",
        )
