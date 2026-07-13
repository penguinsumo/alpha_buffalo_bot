#!/usr/bin/env python3
"""Regression checks for XABCD -> Newday -> Scenario route preservation."""

from pathlib import Path
import sys
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harmonic_detector import (  # noqa: E402
    HarmonicPoint,
    HarmonicDetector,
    build_prz,
    classify_symmetric_xabcd_route,
    validate_xabcd,
    xabcd_ratios,
)
from scenario_scanner import ScenarioScanner  # noqa: E402
from scripts.daily_market_scan import zone_to_harmonic_context  # noqa: E402
from kivanc_vsaob import PivotPoint  # noqa: E402


def main() -> None:
    # Approximate X/A/B/C/D prices and ratios visible in the supplied chart.
    points = HarmonicPoint(
        x=4213.57,
        a=3958.22,
        b=4092.07,
        c=3958.22,
        d=4204.08,
        x_idx=10,
        a_idx=40,
        b_idx=70,
        c_idx=100,
        d_idx=130,
    )
    ratios = xabcd_ratios(points)
    assert 0.52 < ratios["XAB"] < 0.54
    assert 1.82 < ratios["BCD"] < 1.85
    assert 0.95 < ratios["XAD"] < 0.98

    chart_pattern = {
        "direction": "SELL", "priority": 1, "reliability": "high",
        "XA": (0.50, 0.55), "AB": (0.95, 1.05),
        "BC": (1.80, 1.90), "CD": (0.95, 0.98),
    }
    assert validate_xabcd(points, chart_pattern)
    route_name, route_pattern = classify_symmetric_xabcd_route(points)
    assert route_name == "Bearish_Symmetric_XABCD"
    assert route_pattern["direction"] == "SELL"

    pivots = [
        PivotPoint(points.x_idx, points.x, "high"),
        PivotPoint(points.a_idx, points.a, "low"),
        PivotPoint(points.b_idx, points.b, "high"),
        PivotPoint(points.c_idx, points.c, "low"),
        PivotPoint(points.d_idx, points.d, "high"),
    ]
    with patch("harmonic_detector.extract_swings", return_value=pivots):
        detected = HarmonicDetector(pivot_n=3).scan(pd.DataFrame({"close": [4046.92]}))
    assert any(zone.pattern_name == route_name for zone in detected)

    zone = build_prz(points, route_name, route_pattern)
    assert zone.x_point == points.x
    assert zone.c_point == points.c
    assert zone.d_idx == points.d_idx
    assert zone.ratios == ratios

    newday = zone_to_harmonic_context(zone, "4H")
    assert newday.found is True
    assert newday.x_point == round(points.x, 3)
    assert newday.d_point == round(points.d, 3)
    assert newday.ratios["BCD"] == round(ratios["BCD"], 6)

    market_map = {
        "harmonic_context": (
            newday.model_dump() if hasattr(newday, "model_dump") else newday.dict()
        )
    }
    routed = ScenarioScanner()._harmonic_from_market_map(market_map, points.d)
    assert routed["found"] is True
    assert routed["x"] == points.x
    assert routed["a"] == points.a
    assert routed["b"] == points.b
    assert routed["c"] == points.c
    assert routed["d_point"] == points.d
    assert routed["ratios"]["XAD"] == round(ratios["XAD"], 6)

    print("PASS detector retains X/A/B/C/D and displayed ratios")
    print("PASS Newday market map serializes the complete harmonic route")
    print("PASS Scenario scanner restores the complete route from Newday")
    print("Summary: 3/3 harmonic route checks passed")


if __name__ == "__main__":
    main()
