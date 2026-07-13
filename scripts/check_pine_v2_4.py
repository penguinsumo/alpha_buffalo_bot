#!/usr/bin/env python3
"""Lightweight contract checks for the TradingView Pine implementation."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PINE = ROOT / "tradingview" / "alpha_buff_gold_analyzer_v2_4.pine"


def check_balanced_delimiters(source: str) -> None:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[tuple[str, int]] = []
    quote: str | None = None
    escaped = False
    line = 1

    for character in source:
        if character == "\n":
            line += 1
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
            continue
        if character in {'"', "'"}:
            quote = character
        elif character in pairs:
            stack.append((character, line))
        elif character in pairs.values():
            assert stack, f"unexpected {character!r} on line {line}"
            opener, opener_line = stack.pop()
            assert pairs[opener] == character, (
                f"{opener!r} on line {opener_line} closed by {character!r}"
            )

    assert quote is None, "unterminated string literal"
    assert not stack, f"unclosed delimiter {stack[-1]}"


def main() -> None:
    source = PINE.read_text(encoding="utf-8")
    check_balanced_delimiters(source)
    lines = source.splitlines()

    # Pine indentation is syntax. A duplicated header with no body is a
    # compile error even when parentheses and brackets remain balanced.
    duplicate_if_headers = [
        (index + 1, line.strip())
        for index, line in enumerate(lines[:-1])
        if line.lstrip().startswith(("if ", "else if "))
        and line.strip() == lines[index + 1].strip()
    ]
    assert not duplicate_if_headers, (
        f"duplicate empty conditional header(s): {duplicate_if_headers}"
    )

    checks = {
        "Pine v6": "//@version=6" in source,
        "all MTF requests use confirmed lookahead pattern": (
            source.count("request.security(")
            == source.count("lookahead=barmerge.lookahead_on")
        ),
        "directional BUY context": all(
            marker in source for marker in ("buyContextScore", "dxyBuy", "buyContextOk")
        ),
        "directional SELL context": all(
            marker in source for marker in ("sellContextScore", "dxySell", "sellContextOk")
        ),
        "asymmetric H1 entry source": all(
            marker in source
            for marker in (
                "buyH1NormalCandle",
                "sellH1HeikinAshi",
                "buyH1Permission",
                "sellH1Permission",
                "H1_CANDLE_15M_TRIGGER",
                "H1_HA_15M_TRIGGER",
            )
        ),
        "legacy shared confluence removed": "confluence_ok" not in source,
        "cluster merge and scoring": all(
            marker in source
            for marker in (
                "buyClusterCount",
                "sellClusterCount",
                "buyClusterScore",
                "sellClusterScore",
            )
        ),
        "confirmed swing high/low anchors": all(
            marker in source
            for marker in (
                "f_confirmed_swing_range",
                "ta.pivothigh",
                "ta.pivotlow",
                "ta.valuewhen",
                "swingPivotBars",
            )
        ) and "f_confirmed_range(" not in source,
        "mirrored sweep/reclaim": all(
            marker in source
            for marker in (
                "buySweepArmed",
                "sellSweepArmed",
                "buyReclaimNow",
                "sellReclaimNow",
            )
        ),
        "BB and VSA trigger": all(
            marker in source
            for marker in ("bbBuyReject", "bbSellReject", "bullVsaWall", "bearVsaWall")
        ),
        "structure runner permission": all(
            marker in source for marker in ("requireStructureRun", "structureBias")
        ),
        "mirrored HA5 exit": all(
            marker in source for marker in ("HA5_TWO_BEAR", "HA5_TWO_BULL")
        ),
        "market alert guard": source.count("if marketOpen") >= 3,
        "trade state reset": all(
            marker in source for marker in ("tradeDirection := 0", "cooldown := cooldownBars")
        ),
        "opposing PRZ target before R fallback": all(
            marker in source
            for marker in (
                "f_target_above",
                "f_target_below",
                "SUPPLY_PRZ",
                "DEMAND_PRZ",
                "R_FALLBACK",
            )
        ),
        "projected PRZ boxes": all(
            marker in source
            for marker in (
                "buyDBox",
                "buyH4Box",
                "buyH1Box",
                "sellDBox",
                "sellH4Box",
                "sellH1Box",
                "demandClusterBox",
                "supplyClusterBox",
                "projectionBars",
                'showHistoricalBands = input.bool(false',
                "boxHistoryBars",
                "labelX",
            )
        ),
        "EA receives final commands only": all(
            marker in source
            for marker in (
                "float _exit",
                "float noExitPrice = na",
                '\"status\":\"SIGNAL\"',
                '\"action\":\"',
                '\"signal_id\":\"',
                "f_send_signal(\"OPEN\"",
                "f_send_signal(\"CLOSE\"",
            )
        ) and '\"status\":\"NO_SIGNAL\"' not in source
        and 'tradeEntry, na,' not in source
        and 'input(\"OPEN\"' not in source,
    }

    failures = [name for name, passed in checks.items() if not passed]
    for name, passed in checks.items():
        print(f"{'PASS' if passed else 'FAIL'} {name}")
    if failures:
        raise SystemExit(f"{len(failures)} Pine contract check(s) failed")
    print(f"Summary: {len(checks)}/{len(checks)} checks passed")


if __name__ == "__main__":
    main()
