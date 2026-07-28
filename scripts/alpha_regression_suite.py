#!/usr/bin/env python3
"""Compatibility runner for the grouped Alpha Buffalo regression suite."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.regression_cases.engine_core import *
from scripts.regression_cases.lifecycle import *
from scripts.regression_cases.telegram import *
from scripts.regression_cases.prz_runtime import *

TESTS = [
    test_upper_sell_not_blocked_by_bullish_context,
    test_lower_buy_not_blocked_by_bearish_context,
    test_lower_zone_blocks_fresh_sell,
    test_upper_zone_blocks_fresh_buy,
    test_harmonic_d_prz_is_one_direction_only,
    test_confirmed_tunnel_sweep_arms_only_the_aligned_approach,
    test_parallel_channel_uses_confirmed_h1_pivots_and_ignores_forming_wick,
    test_closed_m15_break_invalidates_h1_tunnel_but_forming_wick_does_not,
    test_final_gate_owns_market_risk_and_optional_harmonic_only,
    test_final_gate_does_not_repeat_hour_or_ha_entry_checks,
    test_low_rr_candidate_waits_in_ea_payload,
    test_vsa_is_evidence_bonus_not_duplicate_ea_hard_gate,
    test_buy_and_sell_share_one_api_schema,
    test_signal_latest_preserves_canonical_contract,
    test_no_signal_has_no_direction_and_ea_waits,
    test_directional_price_validator_blocks_invalid_buy,
    test_error_uses_same_schema_and_never_executes,
    test_choch_promotes_to_v5_journey,
    test_no_choch_stays_v4_range,
    test_session_kivanc_mask_uses_bangkok_asia_hours,
    test_indicators_do_not_read_future_daily_or_h1_bars,
    test_deep_buy_requires_wall_then_reclaim,
    test_deep_sell_requires_wall_then_reclaim,
    test_deep_reclaim_engines_use_wall_for_sl,
    test_zone_pinbar_requires_later_break_and_mirrors,
    test_ha5_uses_two_closed_bars,
    test_live_m5_extreme_detects_tp1_between_polls,
    test_lifecycle_buy_tp1_be_then_ha5_exit_is_idempotent,
    test_lifecycle_sell_mirror_and_hard_risk_gate,
    test_active_position_forces_ea_to_management_only,
    test_execution_api_fill_and_ack_round_trip,
    test_telegram_public_output_hides_engine_internals,
    test_pine_monitor_does_not_promote_blocked_buy_inside_sell_ha_context,
    test_confirmed_open_is_the_only_public_directional_setup,
    test_pine_payload_is_silent_on_every_telegram_destination,
    test_closed_market_suppresses_all_telegram,
    test_h1_prz_wait_confirmation_does_not_require_harmonic,
    test_trend_update_is_hourly_except_h1_ema_or_rsi_regime_cross,
    test_weekend_is_hard_closed_before_session_resolution,
    test_seasonal_bangkok_sessions_survive_conflict_resolution,
    test_closed_market_pipeline_is_canonical_and_skips_data_fetch,
    test_configured_holiday_blocks_session_and_telegram,
    test_weekend_direct_sender_never_calls_telegram_network,
    test_every_repository_telegram_sender_uses_central_closed_gate,
    test_recent_m15_prz_touch_is_observable_without_forcing_v4_open,
    test_h1_prz_location_memory_waits_for_evidence_and_ha_flip,
    test_armed_buy_accepts_pinbar_break_but_rejects_forming_m15_ha,
    test_m5_sniper_sweep_requires_closed_kivanc_bb_prz_reclaim_and_mirrors,
    test_m5_sniper_ignores_forming_provider_candle,
    test_scanner_prz_ignores_forming_candle_extremes,
    test_full_scenario_scan_populates_h1_prz_a_and_b,
    test_opposite_bos_cancels_prz_memory_before_ha_flip,
    test_v4_pattern_comparison_routes_only_to_owner,
]

def main() -> int:
    failures = []
    for test in TESTS:
        try:
            test()
            print(f"PASS {test.__name__}")
        except Exception as exc:
            failures.append((test.__name__, exc))
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")

    print(f"\nSummary: {len(TESTS) - len(failures)} passed, {len(failures)} failed")
    if failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
