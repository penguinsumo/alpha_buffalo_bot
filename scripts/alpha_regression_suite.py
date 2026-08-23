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
from scripts.regression_cases.data_runtime import *
from scripts.regression_cases.newday_runtime import *
from scripts.regression_cases.session_gate_runtime import *
from scripts.regression_cases.fundamental_runtime import *
from scripts.regression_cases.hourly_stats_runtime import *
from scripts.regression_cases.diagnostic_symbols_runtime import *

TESTS = [
    test_upper_sell_not_blocked_by_bullish_context,
    test_lower_buy_not_blocked_by_bearish_context,
    test_lower_zone_blocks_fresh_sell,
    test_upper_zone_blocks_fresh_buy,
    test_harmonic_d_prz_is_post_bos_target_only,
    test_decision_engine_never_uses_harmonic_for_entry_direction,
    test_confirmed_tunnel_sweep_arms_only_the_aligned_approach,
    test_parallel_channel_uses_confirmed_h1_pivots_and_ignores_forming_wick,
    test_closed_m15_break_invalidates_h1_tunnel_but_forming_wick_does_not,
    test_final_gate_owns_market_risk_and_never_harmonic,
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
    test_cf_ready_without_structure_does_not_promote_v5,
    test_bos_without_next_prz_does_not_invent_tp2,
    test_v5_continuation_never_creates_fresh_sell_order,
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
    test_runtime_telegram_signal_only_suppresses_all_monitor_updates,
    test_incomplete_pipeline_payload_never_sends_zero_price_trend,
    test_trend_formatter_reads_canonical_fallback_fields,
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
    test_confirmed_h1_green_dot_opens_m15_demand_prz_without_waiting_evidence,
    test_m5_sniper_sweep_requires_closed_kivanc_bb_prz_reclaim_and_mirrors,
    test_m5_two_point_reclaim_confirms_multibar_kivanc_reaction,
    test_m5_sniper_ignores_forming_provider_candle,
    test_scanner_prz_ignores_forming_candle_extremes,
    test_full_scenario_scan_populates_h1_prz_a_and_b,
    test_opposite_bos_cancels_prz_memory_before_ha_flip,
    test_v4_pattern_comparison_routes_only_to_owner,
    test_tf_cache_defaults_follow_confirmed_candle_cadence,
    test_tf_cache_refreshes_on_candle_boundary,
    test_persisted_tf_cache_recovers_bounded_provider_outage,
    test_expired_persisted_tf_cache_is_never_used_for_entry,
    test_entry_freshness_requires_m5_only_for_sniper_mode,
    test_python_queue_reports_pipeline_error_instead_of_generic_hold,
    test_daily_limit_cooldown_ends_at_next_utc_midnight,
    test_daily_limit_circuit_breaker_skips_duplicate_network_calls,
    test_newday_diagnostic_reports_unavailable_without_a_map,
    test_newday_diagnostic_reads_the_latest_generated_map,
    test_newday_bias_is_a_hint_never_a_gate,
    test_newday_diagnostic_never_raises_on_corrupt_map_file,
    test_buy_offhours_default_is_still_a_hard_block,
    test_buy_offhours_soft_gate_allows_with_reduced_risk,
    test_buy_offhours_soft_gate_still_respects_risk_gates,
    test_sell_is_never_touched_by_the_buy_offhours_policy,
    test_fundamental_diagnostic_never_raises_offline,
    test_fundamental_bias_has_no_gating_fields,
    test_fundamental_bias_degrades_to_neutral_without_network,
    test_hourly_stats_unit_is_neutral_until_min_samples,
    test_hourly_stats_unit_computes_win_rate_once_enough_samples,
    test_lifecycle_close_records_into_hourly_stats,
    test_hourly_stats_summary_has_no_gating_fields,
    test_hourly_stats_survive_manager_restart,
    test_diagnostic_symbols_disabled_by_default,
    test_diagnostic_broadcast_is_noop_while_feature_flag_is_off,
    test_diagnostic_broadcast_uses_owner_audience_and_never_claims_ea_execution,
    test_diagnostic_unverified_symbol_carries_a_warning,
    test_diagnostic_dedup_is_isolated_from_production_signal_key,
    test_diagnostic_loop_never_calls_the_ea_command_publisher,
    test_diagnostic_watch_message_never_leaks_engine_internals,
    test_diagnostic_watch_broadcast_skips_when_a_real_open_exists,
    test_diagnostic_watch_broadcast_sends_once_per_symbol,
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
