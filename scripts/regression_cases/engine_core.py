"""Extracted characterization cases; behavior intentionally unchanged."""
from scripts.regression_cases.common import *

def test_upper_sell_not_blocked_by_bullish_context() -> None:
    row = base_row()
    df = frame([row])
    sig = SellSignalEngine().evaluate(df, 0, NY_SESSION, ALLOWED)

    assert_true(sig is not None, "upper-zone V4 SELL must not be blocked by H1/EMA bullish context")
    assert_equal(sig["direction"], "SELL", "upper-zone direction")
    assert_equal(sig["status"], SIGNAL, "SELL engine must emit canonical status")
    assert_equal(sig["tp2_price"], sig["tp"], "SELL tp2 alias")
    assert_true(sig["entry_mode"].startswith("V4_SELL"), "upper-zone SELL must stay V4")
    assert_true(sig["rr_ok"], "fixture should be executable RR")

def test_lower_buy_not_blocked_by_bearish_context() -> None:
    row = base_row()
    row.update(
        {
            "EMA20": 80.0,
            "EMA50": 100.0,
            "Trend_1H_Up": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "HA_Bullish": True,
            "HA_Bearish": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Block_Buy_At_Upper": False,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)

    assert_true(sig is not None, "lower-zone V4 BUY must not be blocked by H1/EMA bearish context")
    assert_equal(sig["direction"], "BUY", "lower-zone direction")
    assert_equal(sig["status"], SIGNAL, "BUY engine must emit canonical status")
    assert_equal(sig["tp1_price"], sig["tp1"], "BUY tp1 alias")
    assert_true(sig["entry_mode"].startswith("V4_BUY"), "lower-zone BUY must stay V4")
    assert_true(sig["rr_ok"], "fixture should be executable RR")

def test_lower_zone_blocks_fresh_sell() -> None:
    row = base_row()
    row.update(
        {
            "V4_Block_Sell_At_Lower": True,
            "V4_Sell_Setup": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Buy_Entry_Zone": True,
            "BB_PRZ_Resistance_Confluence": False,
            "BB_PRZ_Support_Confluence": True,
        }
    )
    sig = SellSignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_equal(sig, None, "lower-zone bullish setup must block fresh SELL")

def test_upper_zone_blocks_fresh_buy() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Block_Buy_At_Upper": True,
            "V4_Buy_Entry_Zone": True,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_equal(sig, None, "upper-zone bearish setup must block fresh BUY")

def test_harmonic_d_prz_is_one_direction_only() -> None:
    context = {
        "found": True,
        "pattern": "Bullish_Symmetric_XABCD",
        "direction": "BUY",
        "state": "ARMED",
        "source": "market_close_map",
        "tunnel_state": "DOWNTREND",
    }
    buy = evaluate_harmonic_bias("BUY", context, require_harmonic=True)
    sell = evaluate_harmonic_bias("SELL", context, require_harmonic=True)

    assert_true(buy.allowed, "bullish harmonic D must license only BUY setup evaluation")
    assert_true(not sell.allowed, "bullish harmonic D must hard-block fresh SELL")
    assert_equal(sell.reason, "HARMONIC_BIAS_BUY_ONLY", "opposite harmonic reason")
    assert_equal(
        buy.tunnel_alignment,
        "C_TO_D_APPROACH_ALIGNED",
        "falling parallel tunnel is the normal approach into bullish D",
    )

def test_confirmed_tunnel_sweep_arms_only_the_aligned_approach() -> None:
    sell = detect_confirmed_tunnel_sweep(
        high=100.10,
        low=98.50,
        close=99.20,
        upper=100.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="DOWNTREND",
    )
    assert_true(sell["SELL"], "upper-tunnel wick and reclaim must arm SELL")
    assert_true(not sell["BUY"], "falling tunnel must not arm BUY at its upper edge")

    no_reclaim = detect_confirmed_tunnel_sweep(
        high=100.50,
        low=99.00,
        close=100.20,
        upper=100.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="DOWNTREND",
    )
    assert_true(not no_reclaim["SELL"], "wick without close back below tunnel is not a sweep/reclaim")

    buy = detect_confirmed_tunnel_sweep(
        high=101.00,
        low=94.90,
        close=95.40,
        upper=105.00,
        lower=95.00,
        tolerance=0.10,
        tunnel_state="UPTREND",
    )
    assert_true(buy["BUY"], "lower-tunnel wick and reclaim must mirror BUY")
    assert_true(not buy["SELL"], "rising tunnel must not arm SELL at its lower edge")

def test_parallel_channel_uses_confirmed_h1_pivots_and_ignores_forming_wick() -> None:
    index = pd.date_range("2026-07-01", periods=30, freq="h", tz="UTC")
    turning_points = {
        0: 105.0,
        5: 120.0,
        9: 90.0,
        13: 110.0,
        17: 80.0,
        21: 100.0,
        25: 70.0,
        29: 85.0,
    }
    center = [0.0] * len(index)
    points = sorted(turning_points)
    for left, right in zip(points, points[1:]):
        start, end = turning_points[left], turning_points[right]
        for position in range(left, right + 1):
            weight = (position - left) / (right - left)
            center[position] = start + (end - start) * weight
    frame = pd.DataFrame(
        {
            "open": center,
            "high": [value + 1.0 for value in center],
            "low": [value - 1.0 for value in center],
            "close": center,
            "volume": [1000.0] * len(index),
        },
        index=index,
    )
    baseline = build_confirmed_parallel_channel(
        frame,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )
    frame_with_news_wick = frame.copy()
    frame_with_news_wick.loc[index[-1], "high"] = 180.0
    after_wick = build_confirmed_parallel_channel(
        frame_with_news_wick,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )

    assert_true(baseline["valid"], "two lower highs/lows must form a valid channel")
    assert_equal(baseline["state"], "DOWNTREND", "falling H1 channel state")
    assert_equal(
        after_wick["anchor_version"],
        baseline["anchor_version"],
        "forming news wick must not repaint confirmed anchors",
    )
    assert_equal(after_wick["upper"], baseline["upper"], "upper channel remains frozen")
    assert_equal(after_wick["lower"], baseline["lower"], "lower channel remains parallel")

    api_frame = frame.reset_index(names="datetime")
    api_channel = build_confirmed_parallel_channel(
        api_frame,
        pivot_bars=3,
        projection_time=api_frame["datetime"].iloc[-1],
        minimum_width=1.0,
    )
    assert_equal(api_channel["upper"], baseline["upper"], "API datetime column projection")
    assert_equal(api_channel["lower"], baseline["lower"], "API channel matches Pine time axis")

    mirrored = frame.copy()
    mirrored["open"] = 250.0 - frame["open"]
    mirrored["close"] = 250.0 - frame["close"]
    mirrored["high"] = 250.0 - frame["low"]
    mirrored["low"] = 250.0 - frame["high"]
    rising = build_confirmed_parallel_channel(
        mirrored,
        pivot_bars=3,
        projection_time=index[-1],
        minimum_width=1.0,
    )
    assert_true(rising["valid"], "mirrored higher highs/lows must form a channel")
    assert_equal(rising["state"], "UPTREND", "rising H1 channel state")

def test_closed_m15_break_invalidates_h1_tunnel_but_forming_wick_does_not() -> None:
    index = pd.date_range("2026-07-14 00:00", periods=3, freq="15min", tz="UTC")
    anchor_ms = int(index[0].timestamp() * 1000)
    channel = {
        "valid": True,
        "state": "DOWNTREND",
        "upper": 109.5,
        "lower": 99.5,
        "slope": -1.0,
        "anchor_time_2": anchor_ms,
        "anchor_price_2": 110.0,
        "parallel_time": anchor_ms,
        "parallel_price": 100.0,
        "anchor_version": anchor_ms,
    }
    frame = pd.DataFrame(
        {
            "open": [105.0, 105.0, 105.0],
            "high": [106.0, 106.0, 150.0],
            "low": [104.0, 104.0, 104.0],
            "close": [105.0, 105.0, 150.0],
        },
        index=index,
    )
    assert_true(
        not confirmed_channel_boundary_broken(channel, frame, tolerance=0.10),
        "forming M15 news wick/close must not invalidate frozen H1 anchors",
    )
    frame.loc[index[1], "close"] = 112.0
    assert_true(
        confirmed_channel_boundary_broken(channel, frame, tolerance=0.10),
        "closed M15 candle above falling upper boundary must invalidate tunnel",
    )

def test_final_gate_owns_market_risk_and_optional_harmonic_only() -> None:
    gate = FinalGate(SessionClock())
    context = {
        "found": True,
        "pattern": "Bullish_Bat",
        "direction": "BUY",
        "state": "ACTIVE",
        "source": "market_close_map",
        "tunnel_state": "FLAT",
    }
    allowed = gate.evaluate(
        NY_SESSION,
        "BUY",
        harmonic_context=context,
        require_harmonic=True,
    )
    blocked = gate.evaluate(
        NY_SESSION,
        "SELL",
        harmonic_context=context,
        require_harmonic=True,
    )
    waiting = gate.evaluate(
        NY_SESSION,
        "BUY",
        harmonic_context={**context, "state": "WAIT_LOCATION"},
        require_harmonic=True,
    )

    assert_true(allowed.allowed, "NY BUY + bullish D must pass the single entry gate")
    assert_true(not blocked.allowed, "SELL must not bypass bullish harmonic bias")
    assert_equal(blocked.reason, "HARMONIC_BIAS_BUY_ONLY", "hard bias reason")
    assert_true(not waiting.allowed, "pattern far from D must not create an entry")
    assert_equal(waiting.reason, "WAIT_HARMONIC_D_PRZ", "wait for D location")

def test_final_gate_does_not_repeat_hour_or_ha_entry_checks() -> None:
    gate = FinalGate(SessionClock())
    asia_outside_legacy_profit_hours = SessionState(
        session="ASIA",
        liquidity="NORMAL",
        bkk_hour=10,
        utc_hour=3,
        timestamp="2026-07-28T03:00:00+00:00",
    )
    row = base_row()
    row.update(
        {
            # A pinbar or M5 sniper trigger is allowed to be the independent
            # V4 trigger.  FinalGate must not require HA_Bullish again.
            "HA_Bullish": False,
            "V4_Buy_Setup": True,
            "V4_Buy_Entry_Zone": True,
        }
    )
    allowed = gate.evaluate(
        asia_outside_legacy_profit_hours,
        "BUY",
        df=frame([row]),
        idx=0,
    )

    assert_true(
        allowed.allowed,
        "closed V4 BUY trigger must survive every open session hour without duplicate HA",
    )
    assert_equal(allowed.reason, "ASIA buy allowed", "permission reason")

def test_low_rr_candidate_waits_in_ea_payload() -> None:
    row = base_row()
    row.update(
        {
            "high": 101.0,
            "BB_Upper": 101.0,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
        }
    )
    sig = BuySignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_true(sig is not None, "low RR V4 setup should remain visible as candidate")
    assert_true(not sig["rr_ok"], "low RR candidate must be rr_ok=false")

    payload_signal = {
        "status": SIGNAL,
        "direction": "BUY",
        "decision": {"action": "BUY", "confidence": 0.7, "score": 6, "grade": "VALID_TRADE"},
        "timestamp": "2026-07-10T15:00:00+00:00",
        "entry_price": sig["entry_price"],
        "sl_price": sig["sl_price"],
        "tp1_price": sig["tp1_price"],
        "tp2_price": sig["tp2_price"],
        "entry": sig["entry"],
        "sl": sig["sl"],
        "tp_final": sig["tp"],
        "entry_mode": sig["entry_mode"],
        "setup_state": sig["setup_state"],
        "scenario_state": sig["setup_state"],
        "engine_v4": sig,
        "gates": {"blueprint_valid": True, "session": "NY"},
        "blueprint": {"is_valid": True, "current_price": sig["entry"]},
    }
    ea = build_ea_payload("XAUUSD", payload_signal)
    assert_equal(ea["action"], "WAIT", "RR below minimum must keep EA waiting")
    assert_true(not ea["rr_ok"], "EA rr_ok must stay false")
    assert_true(ea["entry_mode"].startswith("V4_BUY"), "EA should keep V4 entry mode for diagnostics")
    response = build_api_signal_response("XAUUSD", payload_signal, ea)
    assert_equal(response["status"], BLOCKED, "low-RR candidate must be BLOCKED at API")
    assert_equal(response["direction"], "BUY", "blocked candidate keeps market direction")

def test_vsa_is_evidence_bonus_not_duplicate_ea_hard_gate() -> None:
    row = base_row()
    row.update(
        {
            "EMA20": 80.0,
            "EMA50": 100.0,
            "Trend_1H_Up": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Block_Buy_At_Upper": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "VSA_Buy_Wins": False,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.0,
            "VSA_Sell_Pressure": 0.0,
            "Pine_PA_Bull_Confirmed": False,
            "Pine_PA_Bear_Confirmed": False,
        }
    )
    candidate = BuySignalEngine().evaluate(
        frame([row]),
        0,
        NY_SESSION,
        ALLOWED,
    )
    assert_true(candidate is not None, "accepted trigger must remain visible without VSA")
    assert_true(candidate["rr_ok"], "fixture levels must pass RR")

    ea = build_ea_payload("XAUUSD", _runtime_signal(candidate))
    assert_equal(ea["action"], "OPEN", "EA must not hard-block a selected V4 trigger on VSA")
    assert_true(not ea["vsa_gate_ok"], "VSA absence remains observable as a bonus field")
    assert_equal(
        ea["plan_lifecycle"]["ready_checks"]["vsa_bonus"],
        False,
        "plan reports VSA as bonus rather than readiness gate",
    )

def test_buy_and_sell_share_one_api_schema() -> None:
    buy_row = base_row()
    buy_row.update(
        {
            "EMA20": 80.0,
            "EMA50": 100.0,
            "Trend_1H_Up": False,
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
        }
    )
    candidates = [
        BuySignalEngine().evaluate(frame([buy_row]), 0, NY_SESSION, ALLOWED),
        SellSignalEngine().evaluate(frame([base_row()]), 0, NY_SESSION, ALLOWED),
    ]

    schemas = []
    for candidate in candidates:
        assert_true(candidate is not None, "fixture must create candidate")
        runtime_signal = _runtime_signal(candidate)
        ea = build_ea_payload("XAUUSD", runtime_signal)
        response = build_api_signal_response("XAUUSD", runtime_signal, ea)
        assert_equal(response["status"], SIGNAL, f"{candidate['direction']} should be executable")
        assert_equal(response["direction"], candidate["direction"], "API direction")
        schemas.append(set(response.keys()))

    assert_equal(schemas[0], schemas[1], "BUY and SELL must have identical API keys")

def test_signal_latest_preserves_canonical_contract() -> None:
    row = base_row()
    candidate = SellSignalEngine().evaluate(frame([row]), 0, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "SELL endpoint fixture must create candidate")
    runtime_signal = _runtime_signal(candidate)
    ea = build_ea_payload("XAUUSD", runtime_signal)
    expected = build_api_signal_response("XAUUSD", runtime_signal, ea)
    original_clock_get = runtime.SessionClock.get

    try:
        runtime.SessionClock.get = lambda self, dt=None: NY_SESSION
        _set_latest_signal(expected)
        served = signal_latest(key=API_LICENSE_KEY, symbol="XAU/USD")
    finally:
        runtime.SessionClock.get = original_clock_get
        _set_latest_signal({})

    assert_equal(served["status"], SIGNAL, "endpoint status")
    assert_equal(served["direction"], "SELL", "endpoint direction")
    assert_equal(set(served.keys()), set(expected.keys()), "endpoint schema must stay canonical")

def test_no_signal_has_no_direction_and_ea_waits() -> None:
    signal = {
        "status": NO_SIGNAL,
        "direction": None,
        "decision": {"action": "BUY", "score": 9, "reason": "legacy fallback"},
        "gates": {"blueprint_valid": True, "session": "NY"},
        "blueprint": {"is_valid": True, "current_price": 100.0},
        "entry_price": 100.0,
        "sl_price": 98.0,
        "tp2_price": 110.0,
    }
    ea = build_ea_payload("XAUUSD", signal)
    response = build_api_signal_response("XAUUSD", signal, ea)

    assert_equal(ea["action"], "WAIT", "EA must ignore legacy direction without SIGNAL status")
    assert_equal(response["status"], NO_SIGNAL, "API no-signal status")
    assert_equal(response["direction"], None, "NO_SIGNAL must not claim BUY or SELL")

def test_directional_price_validator_blocks_invalid_buy() -> None:
    result = create_signal(
        status=SIGNAL,
        direction="BUY",
        entry_price=100.0,
        sl_price=101.0,
        tp1_price=105.0,
        tp2_price=110.0,
        score=8,
        reason="bad levels",
    )
    assert_equal(result["status"], BLOCKED, "invalid BUY levels must be blocked")
    assert_equal(result["direction"], "BUY", "validator keeps direction for diagnostics")
    assert_true("INVALID_BUY_LEVELS" in result["reason"], "validator reason")

def test_error_uses_same_schema_and_never_executes() -> None:
    signal = {
        "status": ERROR,
        "direction": None,
        "reason": "DATA_FETCH_ERROR",
        "decision": {"action": "NONE", "score": 0, "reason": "DATA_FETCH_ERROR"},
        "gates": {"blueprint_valid": False, "session": ""},
        "blueprint": {"is_valid": False},
    }
    ea = build_ea_payload("XAUUSD", signal)
    response = build_api_signal_response("XAUUSD", signal, ea)

    assert_equal(ea["action"], "WAIT", "ERROR must never execute")
    assert_equal(response["status"], ERROR, "API error status")
    assert_equal(response["direction"], None, "ERROR must not claim a direction")
    assert_equal(response["reason"], "DATA_FETCH_ERROR", "error reason must be preserved")

def test_choch_promotes_to_v5_journey() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "CHoCH_Bull": True,
            "Pine_Valid_Buy": True,
        }
    )
    rows = [base_row() for _ in range(5)] + [row]
    routed = SignalRouter(
        clock=SessionClock(),
        gate=FinalGate(SessionClock()),
        buy_engine=BuySignalEngine(),
        sell_engine=SellSignalEngine(),
    ).process(frame(rows))

    assert_true(routed, "router should select CHoCH BUY candidate")
    sig = routed[0]
    assert_equal(sig["direction"], "BUY", "CHoCH fixture direction")
    assert_equal(sig["journey_state"], "V5_BUY_JOURNEY", "CHoCH must promote to V5 journey")
    assert_true(sig["bos_confirmed"], "CHoCH promotion must mark BOS confirmed")

def test_no_choch_stays_v4_range() -> None:
    row = base_row()
    row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": True,
            "BB_PRZ_Resistance_Confluence": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "CHoCH_Bull": False,
            "Pine_Valid_Buy": False,
        }
    )
    routed = SignalRouter(
        clock=SessionClock(),
        gate=FinalGate(SessionClock()),
        buy_engine=BuySignalEngine(),
        sell_engine=SellSignalEngine(),
    ).process(frame([base_row() for _ in range(5)] + [row]))

    assert_true(routed, "router should select non-CHoCH BUY candidate")
    assert_equal(routed[0]["journey_state"], "V4_SCALP_RANGE", "no CHoCH must stay V4 range")
    assert_true(not routed[0]["bos_confirmed"], "no CHoCH must not mark BOS confirmed")

def test_session_kivanc_mask_uses_bangkok_asia_hours() -> None:
    index = pd.DatetimeIndex([
        pd.Timestamp("2026-07-10T00:00:00Z"),  # 07:00 BKK
        pd.Timestamp("2026-07-10T08:00:00Z"),  # 15:00 BKK
    ])
    mask = _asia_session_mask(index)
    assert_true(bool(mask.iloc[0]), "07:00 BKK must use ASIA 0.618-0.786 map")
    assert_true(not bool(mask.iloc[1]), "15:00 BKK must use deep 0.720-0.886 map")

def test_indicators_do_not_read_future_daily_or_h1_bars() -> None:
    index = pd.date_range("2026-06-01", periods=420, freq="15min", tz="UTC")
    base = pd.DataFrame(index=index)
    base["open"] = [100.0 + (i % 40) * 0.1 for i in range(len(index))]
    base["close"] = base["open"] + 0.05
    base["high"] = base[["open", "close"]].max(axis=1) + 0.3
    base["low"] = base[["open", "close"]].min(axis=1) - 0.3
    base["volume"] = 100.0
    changed = base.copy()
    changed.loc[index[360]:, "high"] += 500.0
    changed.loc[index[360]:, "low"] -= 500.0

    original_indicators = add_indicators(base)
    changed_indicators = add_indicators(changed)
    for column in ("Swing_H", "Swing_L", "Fib_072", "Fib_0886", "Trend_1H_Up"):
        left = original_indicators.loc[:index[359], column]
        right = changed_indicators.loc[:index[359], column]
        assert_true(left.equals(right), f"future candles changed historical {column}")

def test_deep_buy_requires_wall_then_reclaim() -> None:
    wall = _deep_state_row()
    wall.update({"Deep_Buy_Wall_Candidate": True, "VSA_Buy_Wins": True})
    wait = _deep_state_row()
    wait.update({"low": 100.5, "high": 102.1, "close": 101.8})
    reclaim = _deep_state_row()
    reclaim.update({"low": 101.5, "high": 103.2, "close": 103.0, "HA_Bullish": True})

    result = _apply_deep_sweep_reclaim_state(frame([wall, wait, reclaim]))
    assert_true(not bool(result["Deep_Buy_Reclaim_Trigger"].iloc[0]), "1.00 wall candle is SETUP, not entry")
    assert_true(not bool(result["Deep_Buy_Reclaim_Trigger"].iloc[1]), "price below 0.886 must keep waiting")
    assert_true(bool(result["Deep_Buy_Reclaim_Trigger"].iloc[2]), "break of wall high inside 0.886-0.720 must trigger BUY")
    assert_equal(float(result["Deep_Buy_Wall_Low"].iloc[2]), 99.0, "BUY wall must preserve sweep wick low")

def test_deep_sell_requires_wall_then_reclaim() -> None:
    wall = _deep_state_row()
    wall.update(
        {
            "open": 119.0,
            "high": 121.0,
            "low": 118.0,
            "close": 119.0,
            "Deep_Sell_Wall_Candidate": True,
            "VSA_Sell_Wins": True,
        }
    )
    wait = _deep_state_row()
    wait.update({"open": 118.8, "high": 119.2, "low": 118.1, "close": 118.4})
    reclaim = _deep_state_row()
    reclaim.update({"open": 118.0, "high": 118.2, "low": 116.8, "close": 117.0, "HA_Bearish": True})

    result = _apply_deep_sweep_reclaim_state(frame([wall, wait, reclaim]))
    assert_true(not bool(result["Deep_Sell_Reclaim_Trigger"].iloc[0]), "1.00 wall candle is SETUP, not entry")
    assert_true(not bool(result["Deep_Sell_Reclaim_Trigger"].iloc[1]), "price above 0.886 must keep waiting")
    assert_true(bool(result["Deep_Sell_Reclaim_Trigger"].iloc[2]), "break of wall low inside 0.886-0.720 must trigger SELL")
    assert_equal(float(result["Deep_Sell_Wall_High"].iloc[2]), 121.0, "SELL wall must preserve sweep wick high")

def test_deep_reclaim_engines_use_wall_for_sl() -> None:
    buy_row = base_row()
    buy_row.update(
        {
            "V4_Buy_Setup": True,
            "V4_Sell_Setup": False,
            "V4_Buy_Entry_Zone": True,
            "V4_Sell_Entry_Zone": False,
            "V4_Block_Buy_At_Upper": False,
            "VSA_Buy_Wins": True,
            "VSA_Sell_Wins": False,
            "VSA_Buy_Pressure": 0.8,
            "VSA_Sell_Pressure": 0.2,
            "Pine_PA_Bull_Confirmed": True,
            "Pine_PA_Bear_Confirmed": False,
            "BB_PRZ_Support_Confluence": False,
            "BB_PRZ_Resistance_Confluence": False,
            "Deep_Buy_Reclaim_Trigger": True,
            "Deep_Buy_Wall_Low": 96.0,
            "Deep_Buy_Wall_High": 99.0,
            "Micro_Lot0_Low": 98.0,
            "Kivanc_Scenario_State": "READY_BUY_RECLAIM",
        }
    )
    buy = BuySignalEngine().evaluate(frame([buy_row]), 0, NY_SESSION, ALLOWED)
    assert_true(buy is not None, "deep BUY reclaim must create candidate")
    assert_equal(buy["entry_mode"], "V4_BUY_DEEP_100_WALL_RECLAIM", "deep BUY entry mode")
    assert_true(buy["sl"] < 96.0, "deep BUY SL must sit below VSA wall low")
    assert_equal(buy["vsa_wall_low"], 96.0, "deep BUY wall evidence")

    sell_row = base_row()
    sell_row.update(
        {
            "BB_PRZ_Resistance_Confluence": False,
            "Deep_Sell_Reclaim_Trigger": True,
            "Deep_Sell_Wall_Low": 101.0,
            "Deep_Sell_Wall_High": 104.0,
            "Micro_Lot0_High": 102.0,
            "Kivanc_Scenario_State": "READY_SELL_RECLAIM",
        }
    )
    sell = SellSignalEngine().evaluate(frame([sell_row]), 0, NY_SESSION, ALLOWED)
    assert_true(sell is not None, "deep SELL reclaim must create candidate")
    assert_equal(sell["entry_mode"], "V4_SELL_DEEP_100_WALL_RECLAIM", "deep SELL entry mode")
    assert_true(sell["sl"] > 104.0, "deep SELL SL must sit above VSA wall high")
    assert_equal(sell["vsa_wall_high"], 104.0, "deep SELL wall evidence")

def test_zone_pinbar_requires_later_break_and_mirrors() -> None:
    buy_rows = [
        {
            "open": 99.0, "high": 101.0, "low": 98.0, "close": 100.0,
            "Zone_Buy_Pinbar_Candidate": True, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": True, "In_Session_Kivanc_Sell_Zone": False,
            "HA_Bullish": True, "HA_Bearish": False,
            "VSA_Buy_Wins": True, "VSA_Sell_Wins": False,
        },
        {
            "open": 100.0, "high": 102.0, "low": 99.5, "close": 101.5,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": True, "In_Session_Kivanc_Sell_Zone": False,
            "HA_Bullish": True, "HA_Bearish": False,
            "VSA_Buy_Wins": True, "VSA_Sell_Wins": False,
        },
    ]
    buy = _apply_zone_pinbar_break_state(frame(buy_rows))
    assert_true(not bool(buy["Zone_Buy_Pinbar_Trigger"].iloc[0]), "pinbar candle is setup only")
    assert_true(bool(buy["Zone_Buy_Pinbar_Trigger"].iloc[1]), "later high break triggers BUY")
    assert_equal(float(buy["Zone_Buy_Wall_Low"].iloc[1]), 98.0, "BUY preserves wick wall")

    sell_rows = [
        {
            "open": 101.0, "high": 102.0, "low": 99.0, "close": 100.0,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": True,
            "In_Session_Kivanc_Buy_Zone": False, "In_Session_Kivanc_Sell_Zone": True,
            "HA_Bullish": False, "HA_Bearish": True,
            "VSA_Buy_Wins": False, "VSA_Sell_Wins": True,
        },
        {
            "open": 100.0, "high": 100.5, "low": 97.5, "close": 98.5,
            "Zone_Buy_Pinbar_Candidate": False, "Zone_Sell_Pinbar_Candidate": False,
            "In_Session_Kivanc_Buy_Zone": False, "In_Session_Kivanc_Sell_Zone": True,
            "HA_Bullish": False, "HA_Bearish": True,
            "VSA_Buy_Wins": False, "VSA_Sell_Wins": True,
        },
    ]
    sell = _apply_zone_pinbar_break_state(frame(sell_rows))
    assert_true(bool(sell["Zone_Sell_Pinbar_Trigger"].iloc[1]), "later low break triggers SELL")
    assert_equal(float(sell["Zone_Sell_Wall_High"].iloc[1]), 102.0, "SELL preserves wick wall")
