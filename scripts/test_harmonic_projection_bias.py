#!/usr/bin/env python3
"""Regression checks for predictive XABC->D pattern and phase bias."""
from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine_v4.harmonic_bias_gate import evaluate_harmonic_bias  # noqa: E402
from harmonic_detector import (  # noqa: E402
    HARMONIC_PATTERNS,
    HarmonicPoint,
    HarmonicProjectionPoint,
    project_harmonic_from_xabc,
    select_harmonic_projection,
    validate_xabcd,
)
from scripts.daily_market_scan import projection_to_harmonic_context  # noqa: E402


POINTS = HarmonicProjectionPoint(
    x=4023.95,
    a=4141.63,
    b=4074.32,
    c=4121.19,
    x_idx=10,
    a_idx=20,
    b_idx=30,
    c_idx=40,
    reversal_direction="BUY",
)


def by_family(candidates: list[dict], family: str) -> dict:
    return next(item for item in candidates if item["family"] == family)


def main() -> None:
    # Official reciprocal AB=CD geometry: C=.618 requires a 1.618 BC
    # projection so that CD equals AB. This intentionally rejects the video's
    # contradictory .618 + 1.272 pairing as an execution formula.
    reciprocal_points = HarmonicProjectionPoint(
        x=90.0,
        a=100.0,
        b=110.0,
        c=103.82,
        x_idx=1,
        a_idx=2,
        b_idx=3,
        c_idx=4,
        reversal_direction="SELL",
    )
    reciprocal = by_family(
        project_harmonic_from_xabc(reciprocal_points, 113.82),
        "ABCD",
    )
    assert reciprocal["state"] == "ACTIVE"
    assert round(reciprocal["ratios"]["BC_AB"], 3) == 0.618
    assert round(reciprocal["ratios"]["TARGET_CD_BC"], 3) == 1.618
    assert reciprocal["tp1"] == reciprocal_points.c
    assert reciprocal["tp2"] == reciprocal_points.a
    assert reciprocal["confirmation_required"] == [
        "PRZ_REVERSAL_CANDLE",
        "DIRECTIONAL_CANDLE_BREAKOUT",
        "HTF_STRUCTURE_ALIGNMENT",
    ]
    reciprocal_context = projection_to_harmonic_context(
        {
            **reciprocal,
            "selected_pattern": reciprocal["pattern"],
            "candidates": [reciprocal],
        },
        "1H",
    )
    assert reciprocal_context.projection_mode == "FORMING_ABC_TO_D"
    assert reciprocal_context.tp1 == reciprocal_points.c
    assert reciprocal_context.tp2 == reciprocal_points.a
    assert reciprocal_context.stop_reference == "CONFIRMATION_CANDLE_EXTREME"
    assert reciprocal_context.statistics_status == "INSUFFICIENT_SAMPLE"
    assert reciprocal_context.statistics_sample_size == 0
    assert reciprocal_context.candidate_patterns[0]["ratio_model"] == (
        "RECIPROCAL_AB_EQUALS_CD"
    )

    completed_bearish = HarmonicPoint(
        x=90.0,
        a=100.0,
        b=110.0,
        c=103.82,
        d=113.82,
        x_idx=0,
        a_idx=1,
        b_idx=2,
        c_idx=3,
        d_idx=4,
    )
    assert validate_xabcd(completed_bearish, HARMONIC_PATTERNS["Bearish_ABCD"])
    assert not validate_xabcd(completed_bearish, HARMONIC_PATTERNS["Bullish_ABCD"])
    contradictory_video_pair = HarmonicPoint(
        **{
            **completed_bearish.__dict__,
            # .618 BC retracement x 1.272 projects CD=.786 AB, not AB=CD.
            "d": 103.82 + (6.18 * 1.272),
        }
    )
    assert not validate_xabcd(
        contradictory_video_pair,
        HARMONIC_PATTERNS["Bearish_ABCD"],
    )

    # Stay clearly outside the detector's 0.5% ARMED buffer so this verifies
    # the predictive C->D phase, not the near-D transition.
    early = project_harmonic_from_xabc(POINTS, 4080.0)
    gartley_early = by_family(early, "Gartley")
    assert gartley_early["state"] == "FORMING"
    assert round(gartley_early["ratios"]["XAB"], 3) == 0.572
    assert gartley_early["direction"] == "BUY"
    assert gartley_early["approach_direction"] == "SELL"

    forming_context = {
        **gartley_early,
        "found": True,
        "tunnel_state": "DOWNTREND",
    }
    approach_sell = evaluate_harmonic_bias("SELL", forming_context, require_harmonic=True)
    counter_buy = evaluate_harmonic_bias("BUY", forming_context, require_harmonic=True)
    assert approach_sell.allowed is True
    assert approach_sell.phase == "C_TO_D"
    assert counter_buy.allowed is False
    assert counter_buy.reason == "HARMONIC_FORMING_SELL_ONLY"

    near_chart_price = project_harmonic_from_xabc(POINTS, 3995.06)
    extended = by_family(near_chart_price, "Extended_XABCD")
    crab = by_family(near_chart_price, "Crab")
    gartley_passed = by_family(near_chart_price, "Gartley")
    assert extended["state"] == "ACTIVE"
    assert crab["state"] in {"ARMED", "FORMING"}
    assert gartley_passed["state"] == "PASSED"
    assert 1.24 < extended["current_xad"] < 1.25
    assert 2.68 < extended["current_bcd"] < 2.70
    selected_near_chart = select_harmonic_projection(near_chart_price)
    assert selected_near_chart is not None
    assert selected_near_chart["family"] == "Crab"
    assert selected_near_chart["state"] == "FORMING"
    assert selected_near_chart["morph_state"] == "ADVANCED_AFTER_PASSED_PRZ"
    assert "ABCD" in selected_near_chart["morph_from"]
    assert "Gartley" in selected_near_chart["morph_from"]
    assert selected_near_chart["morph_to"] == "Crab"
    keep_approaching = evaluate_harmonic_bias(
        "SELL",
        {**selected_near_chart, "found": True, "tunnel_state": "DOWNTREND"},
        require_harmonic=True,
    )
    assert keep_approaching.allowed is True

    near_projected_d = project_harmonic_from_xabc(POINTS, 3975.58)
    crab_at_d = by_family(near_projected_d, "Crab")
    assert crab_at_d["state"] == "ARMED"
    d_context = {**crab_at_d, "found": True, "tunnel_state": "DOWNTREND"}
    reversal_buy = evaluate_harmonic_bias("BUY", d_context, require_harmonic=True)
    continuation_sell = evaluate_harmonic_bias("SELL", d_context, require_harmonic=True)
    assert reversal_buy.allowed is True
    assert reversal_buy.phase == "D_REVERSAL"
    assert continuation_sell.allowed is False
    assert continuation_sell.reason == "HARMONIC_BIAS_BUY_ONLY"

    fallback_only = evaluate_harmonic_bias(
        "BUY",
        {**extended, "found": True, "tunnel_state": "DOWNTREND"},
        require_harmonic=True,
    )
    assert fallback_only.allowed is False
    assert fallback_only.reason == "HARMONIC_CANDIDATE_CONTEXT_ONLY"

    broken_tunnel = evaluate_harmonic_bias(
        "SELL",
        {**forming_context, "tunnel_state": "UPTREND"},
        require_harmonic=True,
    )
    assert broken_tunnel.allowed is False
    assert broken_tunnel.reason == "WAIT_PARALLEL_TUNNEL_ALIGNMENT"

    explicit_break = evaluate_harmonic_bias(
        "SELL",
        {**forming_context, "tunnel_broken": True},
        require_harmonic=True,
    )
    assert explicit_break.allowed is False
    assert explicit_break.reason == "HARMONIC_TUNNEL_BROKEN"

    print("PASS reciprocal AB=CD projects .618 C retrace to 1.618 BC")
    print("PASS AB=CD requires candle breakout and HTF structure confirmation")
    print("PASS Newday preserves AB=CD targets, evidence and statistics status")
    print("PASS completed AB=CD rejects the contradictory .618 + 1.272 pair")
    print("PASS confirmed XABC predicts Gartley before D exists")
    print("PASS forming C->D phase allows only tunnel-aligned SELL approach")
    print("PASS price passing Gartley advances to named Crab, not broad fallback")
    print("PASS projected Crab D/PRZ switches hard bias to BUY reversal")
    print("PASS broad Extended XABCD remains context-only")
    print("PASS broken parallel tunnel blocks the forming route")
    print("PASS explicit tunnel break invalidates the projected route")
    print("PASS morph metadata records measured passed-PRZ candidate ladder")
    print("Summary: 12/12 predictive harmonic checks passed")


if __name__ == "__main__":
    main()
