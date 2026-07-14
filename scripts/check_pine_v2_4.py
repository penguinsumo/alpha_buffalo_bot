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
                "requireAsymmetricH1",
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
        "confirmed pivot parallel-channel sweep can arm but never enter alone": all(
            marker in source
            for marker in (
                "f_confirmed_parallel_channel",
                "_fallingStructure",
                "_risingStructure",
                "_highSlopePerMs",
                "_lowSlopePerMs",
                "tunnelAnchorVersion",
                "f_tunnel_context_json",
                '"tunnel_direction":"',
                '"tunnel_anchor_time_1":',
                '"tunnel_parallel_price":',
                "tunnelUpper",
                "tunnelLower",
                "buyTunnelSweepNow",
                "sellTunnelSweepNow",
                "buyTunnelSweepArmed",
                "sellTunnelSweepArmed",
                "buyTunnelLocationOk",
                "sellTunnelLocationOk",
                "BUY_TUNNEL_SWEEP_ARMED",
                "SELL_TUNNEL_SWEEP_ARMED",
                "TUNNEL_SWEEP_GC_RVOL_WALL_PA_HA15_BUY",
                "TUNNEL_SWEEP_GC_RVOL_WALL_PA_HA15_SELL",
            )
        ) and "sellSignal := sellTunnelSweepNow" not in source
        and "buySignal := buyTunnelSweepNow" not in source
        and "tunnelLookback" not in source
        and "f_confirmed_tunnel" not in source,
        "BB and PA trigger": all(
            marker in source
            for marker in ("bbBuyReject", "bbSellReject", "buyReversalPin", "sellReversalPin")
        ) and "bullVsaWall" not in source and "bearVsaWall" not in source,
        "real GC futures RVOL at confirmed support resistance": all(
            marker in source
            for marker in (
                'gcSymbol          = input.symbol("COMEX:GC1!"',
                "f_confirmed_gc",
                "gcResistanceH1",
                "gcSupportH1",
                "gcRvol",
                "gcBuyRvolWall",
                "gcSellRvolWall",
                "gcBuyPermission",
                "gcSellPermission",
            )
        ) and "close + displaySpread" not in source,
        "PRZ reversal requires confirmed M15 or H1 pinbar": all(
            marker in source
            for marker in (
                "h1BullPin",
                "h1BearPin",
                "buyReversalPin = bullPin or h1BullPin",
                "sellReversalPin = bearPin or h1BearPin",
                "buyPaZoneSetup",
                "sellPaZoneSetup",
            )
        ),
        "GC futures RVOL and PA latch at PRZ or tunnel but never enter directly": all(
            marker in source
            for marker in (
                "buyGcRvolWallArmed",
                "sellGcRvolWallArmed",
                "buyGcRvolWallSetup",
                "sellGcRvolWallSetup",
                "buyPaZoneArmed",
                "sellPaZoneArmed",
                "buyPaZoneSetup",
                "sellPaZoneSetup",
                "buyArmSetup = (buyGcRvolWallArmed or buyGcRvolWallSetup) and (buyPaZoneArmed or buyPaZoneSetup)",
                "sellArmSetup = (sellGcRvolWallArmed or sellGcRvolWallSetup) and (sellPaZoneArmed or sellPaZoneSetup)",
                "buyEvidenceLocationOk = buyPrzRouteOk or buyTunnelLocationOk",
                "sellEvidenceLocationOk = sellPrzRouteOk or sellTunnelLocationOk",
                "PRZ_GC_RVOL_WALL_PA_HA15_BUY",
                "PRZ_GC_RVOL_WALL_PA_HA15_SELL",
            )
        ) and "MidContinuationSetup" not in source
        and "MID_VSA" not in source,
        "opposite BOS invalidates PRZ evidence before entry": all(
            marker in source
            for marker in (
                "buyPrzBosThrough = newStructureBar and bearStructureBreak",
                "sellPrzBosThrough = newStructureBar and bullStructureBreak",
                "if buyPrzBosThrough",
                "if sellPrzBosThrough",
                "buyEntryArmed := false",
                "sellEntryArmed := false",
            )
        ),
        "structure runner permission": all(
            marker in source for marker in ("requireStructureRun", "structureBias")
        ),
        "PRZ location is armed before HA confirmation": all(
            marker in source
            for marker in (
                "buyEntryArmed",
                "sellEntryArmed",
                "buyArmSetup",
                "sellArmSetup",
                "entryArmTtl",
            )
        ),
        "mirrored confirmed HA15 second-candle trigger": all(
            marker in source
            for marker in (
                "ha15TwoBearLower",
                "ha15TwoBullHigher",
                "HA15_TWO_BEAR_LOWER_REVERSE",
                "HA15_TWO_BULL_HIGHER_REVERSE",
            )
        ) and "HA5_TWO_" not in source,
        "ACK-gated reverse payload": all(
            marker in source
            for marker in (
                "f_send_reverse_close",
                '"reverse_direction":"',
                '"reverse_signal_id":"',
                "REVERSE_PENDING_SELL_ACK",
                "REVERSE_PENDING_BUY_ACK",
            )
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
