"""Adaptive hourly stats regressions.

Protects: closing a position records into HourlyStats keyed by UTC close
hour; the summary is diagnostic-only (no allowed/blocked field, nothing an
engine_v4 caller could mistake for a gate); and the stats survive a
manager restart via the same state-file persistence used for positions and
risk (per EA_EXECUTION_CONTRACT.md's persistent-volume note).
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from scripts.regression_cases.common import *

from runtime_layers.hourly_stats import HourlyStats


def test_hourly_stats_unit_is_neutral_until_min_samples():
    stats = HourlyStats()
    stats.record(10, 1.0)
    stats.record(10, 1.0)
    # Only 2 samples recorded; default min_samples=5 -> must stay neutral.
    assert_equal(stats.wr(10), 0.5, "thin hourly bucket must report neutral 0.5 win rate")
    assert_equal(stats.sample_count(10), 2, "sample count must reflect recorded trades")


def test_hourly_stats_unit_computes_win_rate_once_enough_samples():
    stats = HourlyStats()
    for pnl in (1.0, 1.0, 1.0, -1.0, -1.0):
        stats.record(14, pnl)
    assert_equal(stats.wr(14, min_samples=5), 0.6, "3 wins / 5 trades = 0.6 win rate")
    assert_true(abs(stats.avg_pnl(14) - 0.2) < 1e-9, "avg R should be (1+1+1-1-1)/5 = 0.2")


def test_lifecycle_close_records_into_hourly_stats():
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="hourly-1", ticket="200", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=110, filled_at="2026-07-10T14:50:00+00:00",
    )
    close = manager.close_external("XAUUSD", r_multiple=2.0)
    assert_equal(close["status"], "CLOSED", "close_external must close the position")

    summary = manager.hourly_stats_summary("XAUUSD", min_samples=1)
    assert_true(len(summary) == 1, "exactly one hour bucket should have a sample")
    hour_key = next(iter(summary))
    assert_equal(summary[hour_key]["samples"], 1, "one closed trade recorded")
    assert_equal(summary[hour_key]["win_rate"], 1.0, "single winning trade -> 1.0 win rate")


def test_hourly_stats_summary_has_no_gating_fields():
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="hourly-2", ticket="201", direction="SELL",
        entry=100, sl=102, tp1=95, tp2=90, filled_at="2026-07-10T14:50:00+00:00",
    )
    manager.close_external("XAUUSD", r_multiple=-1.0)
    summary = manager.hourly_stats_summary("XAUUSD", min_samples=1)
    for bucket in summary.values():
        assert_true("allowed" not in bucket, "hourly stats bucket must not carry 'allowed'")
        assert_true("blocked" not in bucket, "hourly stats bucket must not carry 'blocked'")


def test_hourly_stats_survive_manager_restart():
    with tempfile.TemporaryDirectory() as tmp:
        state_file = Path(tmp) / "state.json"
        first = ExecutionLifecycleManager(state_file=state_file)
        first.register_fill(
            symbol="XAUUSD", signal_id="hourly-3", ticket="202", direction="BUY",
            entry=100, sl=98, tp1=105, tp2=110, filled_at="2026-07-10T14:50:00+00:00",
        )
        first.close_external("XAUUSD", r_multiple=1.0)

        reloaded = ExecutionLifecycleManager(state_file=state_file)
        summary_before = first.hourly_stats_summary("XAUUSD", min_samples=1)
        summary_after = reloaded.hourly_stats_summary("XAUUSD", min_samples=1)
        assert_equal(summary_after, summary_before, "hourly stats must survive a manager restart via the state file")
