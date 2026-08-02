"""Extracted characterization cases; behavior intentionally unchanged."""
from scripts.regression_cases.common import *

def test_recent_m15_prz_touch_is_observable_without_forcing_v4_open() -> None:
    row = base_row()
    row.update(
        {
            "Deep_Buy_PRZ_Context": True,
            "Deep_Sell_PRZ_Context": False,
            "In_Pine_PRZ_Support": False,
            "In_Pine_PRZ_Resistance": False,
            "Near_BB_Lower": True,
            "Near_BB_Upper": False,
            "Bull_Sweep": False,
            "Bear_Sweep": False,
            "HA_Bull_Reversal": False,
            "HA_Bear_Reversal": False,
            "Bullish_Pinbar": False,
            "Bearish_Pinbar": False,
            "Deep_Buy_Reclaim_Trigger": False,
            "Deep_Sell_Reclaim_Trigger": False,
            "Zone_Buy_Pinbar_Trigger": False,
            "Zone_Sell_Pinbar_Trigger": False,
            "Kivanc_Scenario_State": "BUY_ZONE_ARMED",
            "V4_Demand_PRZ_Layer_Count": 1,
            "V4_Buy_Setup": False,
            "V4_Sell_Setup": False,
            "VSA_Buy_Wins": False,
            "VSA_Sell_Wins": True,
        }
    )
    diagnostic = runtime._engine_v4_wait_diagnostics(frame([row]))

    assert_true(diagnostic["recent_prz_touch"], "recent wick overlap must remain observable")
    assert_equal(diagnostic["context_direction"], "BUY", "demand PRZ context direction")
    assert_equal(diagnostic["status"], "WAIT_CONFIRM", "touch alone must not create OPEN")
    assert_true("PRZ_LAYERS_1_OF_2" in diagnostic["missing_buy"], "owner sees PRZ layer gap")
    assert_true("EVIDENCE_0_OF_3" in diagnostic["missing_buy"], "owner sees aggregate evidence gap")
    assert_true(not diagnostic["v4_selected"], "diagnostics cannot manufacture a trade")

def test_h1_prz_location_memory_waits_for_evidence_and_ha_flip() -> None:
    class Blueprint:
        htf_prz_support_low = 97.5
        htf_prz_support_high = 98.5
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 97.5
        prz_a_support_high = 98.5
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0

    touch = base_row()
    touch.update(
        {
            "high": 102.0,
            "low": 98.0,
            "Bullish_Pinbar": True,
            "Near_BB_Lower": True,
            "Bearish_Pinbar": False,
            "HA_Bull_Reversal": False,
            "HA_Bear_Reversal": False,
            "Deep_Buy_PRZ_Context": False,
            "Deep_Sell_PRZ_Context": False,
            "Bull_Sweep": False,
            "Bear_Sweep": False,
            "Deep_Buy_Reclaim_Trigger": False,
            "Deep_Sell_Reclaim_Trigger": False,
            "Zone_Buy_Pinbar_Trigger": False,
            "Zone_Sell_Pinbar_Trigger": False,
            "In_Session_Kivanc_Buy_Zone": False,
            "In_Session_Kivanc_Sell_Zone": False,
            "Micro_BOS_Up": False,
            "V4_Buy_Setup": False,
            "V4_Sell_Setup": False,
        }
    )
    wait = dict(touch)
    wait.update(
        {
            "high": 105.0,
            "low": 101.0,
            "close": 103.0,
            "Bullish_Pinbar": False,
        }
    )
    trigger = dict(wait)
    trigger.update(
        {
            "high": 106.0,
            "low": 102.0,
            "close": 104.0,
            "BB_Mid": 110.0,
            "BB_Upper": 120.0,
            "HA_Bull_Reversal": True,
        }
    )

    result = runtime._overlay_blueprint_prz_memory(
        frame([touch, wait, trigger]),
        Blueprint(),
        lock_bars=4,
    )

    assert_true(bool(result["In_H1_PRZ_Support"].iloc[0]), "H1 wick overlap is a V4 location")
    assert_true(bool(result["V4_Buy_Location_Memory"].iloc[2]), "location survives for four M15 bars")
    assert_equal(int(result["V4_Buy_Evidence_Score"].iloc[2]), 3, "PRZ plus pinbar evidence")
    assert_true(bool(result["V4_Buy_Memory_Trigger"].iloc[2]), "HA flip releases the setup")
    assert_true(bool(result["V4_Buy_Setup"].iloc[2]), "V4 receives the delayed H1 PRZ setup")
    candidate = BuySignalEngine().evaluate(result, 2, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "confirmed memory setup reaches BUY engine")
    assert_equal(
        candidate["entry_mode"],
        "V4_BUY_M15_HA_FLIP",
        "execution identifies the restored PRZ path",
    )
    assert_true(candidate["sl"] < candidate["entry"], "latched PRZ wall provides valid SL")

    diagnostic = runtime._engine_v4_wait_diagnostics(result)
    assert_true("H1 DEMAND PRZ" in diagnostic["location_sources"], "owner sees H1 source")
    assert_equal(diagnostic["buy_evidence_score"], 3, "owner sees evidence score")

def test_armed_buy_accepts_pinbar_break_but_rejects_forming_m15_ha() -> None:
    class Blueprint:
        htf_prz_support_low = 97.5
        htf_prz_support_high = 98.5
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 97.5
        prz_a_support_high = 98.5
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0

    touch = base_row()
    touch.update(
        {
            "low": 98.0,
            "high": 102.0,
            "Bullish_Pinbar": True,
            "Near_BB_Lower": True,
            "HA_Bull_Reversal": False,
            "Zone_Buy_Pinbar_Trigger": False,
            "Deep_Buy_PRZ_Context": False,
            "Deep_Sell_PRZ_Context": False,
            "V4_Buy_Setup": False,
            "V4_Sell_Setup": False,
        }
    )
    pinbar_break = dict(touch)
    pinbar_break.update(
        {
            "low": 99.0,
            "high": 106.0,
            "close": 104.0,
            "Bullish_Pinbar": False,
            "Near_BB_Lower": False,
            "Zone_Buy_Pinbar_Trigger": True,
            "Zone_Buy_Wall_Low": 98.0,
            "BB_Mid": 110.0,
            "BB_Upper": 120.0,
        }
    )
    result = runtime._overlay_blueprint_prz_memory(
        frame([touch, pinbar_break]),
        Blueprint(),
        lock_bars=4,
    )
    assert_true(bool(result["V4_Buy_Armed"].iloc[-1]), "two PRZ layers plus evidence arms BUY")
    assert_equal(int(result["V4_Buy_Evidence_Score"].iloc[-1]), 3, "pinbar plus BB evidence")
    assert_equal(
        result["V4_Buy_Trigger_Source"].iloc[-1],
        "BULL_PINBAR_HIGH_BREAK",
        "pinbar high break is an independent trigger",
    )
    candidate = BuySignalEngine().evaluate(result, 1, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "pinbar break reaches BUY engine without HA flip")
    assert_equal(candidate["entry_mode"], "V4_BUY_PINBAR_HIGH_BREAK", "pinbar entry mode")

    forming = frame([touch])
    forming.index = pd.DatetimeIndex(
        [pd.Timestamp.now(tz="UTC").floor("15min") + pd.Timedelta(hours=1)]
    )
    forming.loc[forming.index[-1], "HA_Bull_Reversal"] = True
    forming_result = runtime._overlay_blueprint_prz_memory(
        forming,
        Blueprint(),
        lock_bars=4,
    )
    assert_true(bool(forming_result["V4_Buy_Armed"].iloc[-1]), "forming bar may preserve ARMED")
    assert_true(
        not bool(forming_result["V4_Buy_HA_Trigger"].iloc[-1]),
        "forming M15 HA flip cannot open",
    )

def test_confirmed_h1_green_dot_opens_m15_demand_prz_without_waiting_evidence() -> None:
    class Blueprint:
        htf_prz_support_low = 97.5
        htf_prz_support_high = 101.0
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 97.5
        prz_a_support_high = 101.0
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0

    touch = base_row()
    touch.update(
        {
            "open": 99.5,
            "high": 101.0,
            "low": 98.0,
            "close": 100.0,
            "Bullish_Pinbar": False,
            "Near_BB_Lower": False,
            "VSA_Buy_Wins": False,
            "Bull_OB": False,
            "In_Session_Kivanc_Buy_Zone": False,
            "HA_Bull_Reversal": False,
            "Zone_Buy_Pinbar_Trigger": False,
            "V4_Buy_M5_Sniper_Evidence": False,
            "Deep_Buy_PRZ_Context": False,
            "Deep_Sell_PRZ_Context": False,
            "BB_Mid": 105.0,
            "BB_Upper": 110.0,
        }
    )
    dot_bar = dict(touch)
    dot_bar.update({"open": 100.0, "high": 102.0, "low": 99.0, "close": 101.0})
    m15 = frame([touch, dot_bar])
    m15.index = pd.DatetimeIndex(
        [
            pd.Timestamp("2026-07-10 14:45", tz="UTC"),
            pd.Timestamp("2026-07-10 15:00", tz="UTC"),
        ]
    )
    h1 = pd.DataFrame(
        [
            {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0},
            {"open": 101.0, "high": 102.0, "low": 99.0, "close": 100.0},
        ],
        index=pd.DatetimeIndex(
            [
                pd.Timestamp("2026-07-10 14:00", tz="UTC"),
                pd.Timestamp("2026-07-10 15:00", tz="UTC"),
            ]
        ),
    )

    result = runtime._overlay_blueprint_prz_memory(
        m15,
        Blueprint(),
        lock_bars=4,
        df_1h=h1,
    )
    assert_equal(
        int(result["V4_Buy_Evidence_Score"].iloc[-1]),
        0,
        "green-dot fast path must not manufacture evidence",
    )
    assert_true(
        bool(result["V4_Buy_H1_Green_Dot"].iloc[-1]),
        "confirmed bullish H1 candle appears on its M15 close boundary",
    )
    assert_true(
        bool(result["V4_Buy_Green_Dot_Trigger"].iloc[-1]),
        "closed M15 green dot releases remembered two-layer demand PRZ",
    )
    assert_equal(
        result["V4_Buy_Trigger_Source"].iloc[-1],
        "M15_PRZ_GREEN_DOT",
        "green-dot source remains explicit",
    )
    candidate = BuySignalEngine().evaluate(result, 1, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "green-dot fast path reaches BUY engine")
    assert_equal(
        candidate["entry_mode"],
        "V4_BUY_M15_PRZ_GREEN_DOT",
        "EA can identify the M15 green-dot entry",
    )

    forming_m15 = m15.tail(1).copy()
    forming_time = pd.Timestamp.now(tz="UTC").floor("15min") + pd.Timedelta(hours=1)
    forming_m15.index = pd.DatetimeIndex([forming_time])
    forming_h1 = pd.DataFrame(
        [
            {"open": 98.0, "high": 102.0, "low": 97.0, "close": 101.0},
            {"open": 101.0, "high": 102.0, "low": 99.0, "close": 100.0},
        ],
        index=pd.DatetimeIndex(
            [forming_time - pd.Timedelta(hours=1), forming_time]
        ),
    )
    forming_result = runtime._overlay_blueprint_prz_memory(
        forming_m15,
        Blueprint(),
        lock_bars=4,
        df_1h=forming_h1,
    )
    assert_true(
        bool(forming_result["V4_Buy_H1_Green_Dot"].iloc[-1]),
        "confirmed H1 permission may be visible while M15 is forming",
    )
    assert_true(
        not bool(forming_result["V4_Buy_Green_Dot_Trigger"].iloc[-1]),
        "forming M15 candle cannot send an order",
    )

def test_m5_sniper_sweep_requires_closed_kivanc_bb_prz_reclaim_and_mirrors() -> None:
    class BuyBlueprint:
        htf_prz_support_low = 88.0
        htf_prz_support_high = 92.0
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 88.0
        prz_a_support_high = 92.0
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0
        kivanc_boundary_low = 0.0
        kivanc_boundary_high = 0.0
        kivanc_fibo_0618 = 0.0
        kivanc_fibo_0786 = 0.0
        kivanc_fibo_0886 = 0.0

    buy_rows = []
    for position in range(3):
        row = base_row()
        row.update(
            {
                "open": 99.0,
                "high": 101.0,
                "low": 96.0,
                "close": 98.0,
                "BB_Lower": 80.0,
                "BB_Upper": 110.0,
                "Fib_0786": 90.0,
                "Fib_0618": 93.0,
                "Deep_Buy_PRZ_Context": False,
                "Deep_Sell_PRZ_Context": False,
                "Bull_Sweep": False,
                "Bear_Sweep": False,
                "Bullish_Pinbar": False,
                "Bearish_Pinbar": False,
                "Deep_Buy_Reclaim_Trigger": False,
                "Deep_Sell_Reclaim_Trigger": False,
                "Zone_Buy_Pinbar_Trigger": False,
                "Zone_Sell_Pinbar_Trigger": False,
                "In_Session_Kivanc_Buy_Zone": False,
                "In_Session_Kivanc_Sell_Zone": False,
                "Near_BB_Lower": False,
                "Near_BB_Upper": False,
                "Micro_BOS_Up": False,
                "V4_Buy_Setup": False,
                "V4_Sell_Setup": False,
                "HA_Bull_Reversal": False,
                "HA_Bear_Reversal": False,
            }
        )
        buy_rows.append(row)
    buy_rows[-1].update({"low": 89.0, "high": 101.0, "close": 96.0})
    m15_buy = frame(buy_rows)

    m5_buy = pd.DataFrame(
        [
            {"open": 98.0, "high": 99.0, "low": 96.0, "close": 97.0},
            {"open": 97.0, "high": 98.0, "low": 95.0, "close": 96.0},
            {"open": 96.0, "high": 97.0, "low": 94.0, "close": 95.0},
            {"open": 96.0, "high": 97.0, "low": 95.0, "close": 96.0},
            # Completed $12 wick sweep: prior low, Kivanc 90 and H1 BB 91 reclaimed.
            {"open": 100.0, "high": 101.0, "low": 89.0, "close": 96.0},
            # Provider-forming row is always ignored.
            {"open": 96.0, "high": 97.0, "low": 95.0, "close": 96.0},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )
    h1_buy = pd.DataFrame(
        [
            {
                "open": 100.0,
                "high": 102.0,
                "low": 98.0,
                "close": 100.0,
                "BB_Lower": 91.0,
                "BB_Upper": 109.0,
            }
            for _ in range(6)
        ],
        index=pd.date_range("2026-07-10 10:00", periods=6, freq="1h", tz="UTC"),
    )

    buy_result = runtime._overlay_blueprint_prz_memory(
        m15_buy,
        BuyBlueprint(),
        lock_bars=4,
        df_5m=m5_buy,
        df_1h=h1_buy,
    )
    assert_true(
        bool(buy_result["V4_Buy_M5_Sniper_Evidence"].iloc[-1]),
        "closed M5 BUY wick is sniper evidence",
    )
    assert_equal(
        buy_result["V4_Buy_M5_Sniper_BB_TF"].iloc[-1],
        "H1",
        "H1 lower BB may confirm when M15 BB is not touched",
    )
    assert_equal(
        int(buy_result["V4_Buy_Evidence_Score"].iloc[-1]),
        3,
        "PRZ location plus M5 sniper reaches evidence minimum",
    )
    assert_true(
        bool(buy_result["V4_Buy_Memory_Trigger"].iloc[-1]),
        "closed M5 sniper independently releases the armed BUY",
    )
    buy_candidate = BuySignalEngine().evaluate(buy_result, 2, NY_SESSION, ALLOWED)
    assert_true(buy_candidate is not None, "confirmed BUY sniper reaches engine")
    assert_equal(
        buy_candidate["entry_mode"],
        "V4_BUY_M5_SNIPER_RECLAIM",
        "BUY order retains its sniper source",
    )

    class SellBlueprint(BuyBlueprint):
        htf_prz_support_low = 0.0
        htf_prz_support_high = 0.0
        htf_prz_resistance_low = 108.0
        htf_prz_resistance_high = 112.0
        prz_a_support_low = 0.0
        prz_a_support_high = 0.0
        prz_a_resistance_low = 108.0
        prz_a_resistance_high = 112.0

    sell_rows = []
    for position in range(3):
        row = base_row()
        row.update(
            {
                "open": 101.0,
                "high": 104.0,
                "low": 99.0,
                "close": 102.0,
                "BB_Lower": 90.0,
                "BB_Upper": 109.0,
                "Fib_R_0786": 110.0,
                "Fib_R_0618": 107.0,
                "Deep_Buy_PRZ_Context": False,
                "Deep_Sell_PRZ_Context": False,
                "Bull_Sweep": False,
                "Bear_Sweep": False,
                "Bullish_Pinbar": False,
                "Bearish_Pinbar": False,
                "Deep_Buy_Reclaim_Trigger": False,
                "Deep_Sell_Reclaim_Trigger": False,
                "Zone_Buy_Pinbar_Trigger": False,
                "Zone_Sell_Pinbar_Trigger": False,
                "In_Session_Kivanc_Buy_Zone": False,
                "In_Session_Kivanc_Sell_Zone": False,
                "Near_BB_Lower": False,
                "Near_BB_Upper": False,
                "VSA_Sell_Wins": False,
                "Micro_BOS_Down": False,
                "V4_Buy_Setup": False,
                "V4_Sell_Setup": False,
                "HA_Bull_Reversal": False,
                "HA_Bear_Reversal": False,
            }
        )
        sell_rows.append(row)
    sell_rows[-1].update({"low": 99.0, "high": 111.0, "close": 104.0})
    m15_sell = frame(sell_rows)
    m5_sell = pd.DataFrame(
        [
            {"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0},
            {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0},
            {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0},
            {"open": 104.0, "high": 105.0, "low": 103.0, "close": 104.0},
            {"open": 100.0, "high": 111.0, "low": 99.0, "close": 104.0},
            {"open": 104.0, "high": 105.0, "low": 103.0, "close": 104.0},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )
    sell_result = runtime._overlay_blueprint_prz_memory(
        m15_sell,
        SellBlueprint(),
        lock_bars=4,
        df_5m=m5_sell,
        df_1h=None,
    )
    assert_true(
        bool(sell_result["V4_Sell_M5_Sniper_Evidence"].iloc[-1]),
        "SELL sniper is the exact mirror",
    )
    assert_equal(
        int(sell_result["V4_Sell_Evidence_Score"].iloc[-1]),
        3,
        "supply location plus SELL sniper reaches evidence minimum",
    )
    assert_true(
        bool(sell_result["V4_Sell_Memory_Trigger"].iloc[-1]),
        "closed M5 sniper independently releases the armed SELL",
    )
    sell_candidate = SellSignalEngine().evaluate(sell_result, 2, NY_SESSION, ALLOWED)
    assert_true(sell_candidate is not None, "confirmed SELL sniper reaches engine")
    assert_equal(
        sell_candidate["entry_mode"],
        "V4_SELL_M5_SNIPER_RECLAIM",
        "SELL order retains its sniper source",
    )

    # WATCH promotion: an ordinary completed rejection wick does not need to
    # be a $10 one-candle spike or touch BB and Fib in the same candle.  The
    # surrounding two-layer PRZ + evidence gate still applies before OPEN.
    watch_buy_m5 = pd.DataFrame(
        [
            {"open": 98.0, "high": 99.0, "low": 96.0, "close": 97.0},
            {"open": 97.0, "high": 98.0, "low": 95.0, "close": 96.0},
            {"open": 96.0, "high": 97.0, "low": 94.0, "close": 95.0},
            {"open": 95.0, "high": 96.0, "low": 94.5, "close": 95.0},
            # Closed lower wick reclaims Fib 90; range is only $4.5.
            {"open": 92.0, "high": 94.0, "low": 89.5, "close": 93.5},
            {"open": 93.5, "high": 94.0, "low": 93.0, "close": 93.5},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )
    watch_buy_result = runtime._overlay_blueprint_prz_memory(
        m15_buy,
        BuyBlueprint(),
        lock_bars=4,
        df_5m=watch_buy_m5,
        df_1h=None,
    )
    assert_equal(
        watch_buy_result["V4_Buy_M5_Sniper_Mode"].iloc[-1],
        "WICK_RECLAIM",
        "closed lower wick promotes WATCH_BUY",
    )
    watch_buy_candidate = BuySignalEngine().evaluate(
        watch_buy_result, 2, NY_SESSION, ALLOWED
    )
    assert_true(
        watch_buy_candidate is not None,
        "WATCH_BUY plus closed M5 wick reaches BUY engine",
    )
    assert_equal(
        watch_buy_candidate["entry_mode"],
        "V4_BUY_M5_WICK_RECLAIM",
        "EA receives explicit BUY wick source",
    )
    assert_true(
        watch_buy_candidate["entry_price"]
        < watch_buy_candidate["tp1_price"]
        <= watch_buy_candidate["tp2_price"],
        "BUY wick signal always exposes directionally valid TP levels",
    )

    watch_sell_rows = [dict(row) for row in sell_rows]
    for row in watch_sell_rows:
        row["BB_Upper"] = 120.0
    watch_sell_m5 = pd.DataFrame(
        [
            {"open": 102.0, "high": 104.0, "low": 101.0, "close": 103.0},
            {"open": 103.0, "high": 105.0, "low": 102.0, "close": 104.0},
            {"open": 104.0, "high": 106.0, "low": 103.0, "close": 105.0},
            {"open": 105.0, "high": 106.0, "low": 104.5, "close": 105.0},
            # Closed upper wick rejects Fib 110; range is only $5.
            {"open": 108.0, "high": 111.0, "low": 106.0, "close": 106.5},
            {"open": 106.5, "high": 107.0, "low": 106.0, "close": 106.5},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )
    watch_sell_result = runtime._overlay_blueprint_prz_memory(
        frame(watch_sell_rows),
        SellBlueprint(),
        lock_bars=4,
        df_5m=watch_sell_m5,
        df_1h=None,
    )
    assert_equal(
        watch_sell_result["V4_Sell_M5_Sniper_Mode"].iloc[-1],
        "WICK_RECLAIM",
        "closed upper wick promotes WATCH_SELL",
    )
    watch_sell_candidate = SellSignalEngine().evaluate(
        watch_sell_result, 2, NY_SESSION, ALLOWED
    )
    assert_true(
        watch_sell_candidate is not None,
        "WATCH_SELL plus closed M5 wick reaches SELL engine",
    )
    assert_equal(
        watch_sell_candidate["entry_mode"],
        "V4_SELL_M5_WICK_RECLAIM",
        "EA receives explicit SELL wick source",
    )
    assert_true(
        watch_sell_candidate["tp2_price"]
        <= watch_sell_candidate["tp1_price"]
        < watch_sell_candidate["entry_price"],
        "SELL wick signal always exposes directionally valid TP levels",
    )

def test_m5_two_point_reclaim_confirms_multibar_kivanc_reaction() -> None:
    class Blueprint:
        htf_prz_support_low = 88.0
        htf_prz_support_high = 92.0
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 88.0
        prz_a_support_high = 92.0
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0
        kivanc_boundary_low = 0.0
        kivanc_boundary_high = 0.0
        kivanc_fibo_0618 = 0.0
        kivanc_fibo_0786 = 0.0
        kivanc_fibo_0886 = 0.0

    rows = []
    for _ in range(3):
        row = base_row()
        row.update(
            {
                "open": 99.0,
                "high": 101.0,
                "low": 89.0,
                "close": 96.0,
                "BB_Lower": 91.0,
                "BB_Upper": 110.0,
                "Fib_0786": 90.0,
                "Fib_0618": 93.0,
                "Deep_Buy_PRZ_Context": False,
                "Deep_Sell_PRZ_Context": False,
                "Bull_Sweep": False,
                "Bear_Sweep": False,
                "Bullish_Pinbar": False,
                "Bearish_Pinbar": False,
                "Deep_Buy_Reclaim_Trigger": False,
                "Deep_Sell_Reclaim_Trigger": False,
                "Zone_Buy_Pinbar_Trigger": False,
                "Zone_Sell_Pinbar_Trigger": False,
                "In_Session_Kivanc_Buy_Zone": False,
                "In_Session_Kivanc_Sell_Zone": False,
                "Near_BB_Lower": False,
                "Near_BB_Upper": False,
                "Micro_BOS_Down": False,
                "V4_Buy_Setup": False,
                "V4_Sell_Setup": False,
                "HA_Bull_Reversal": False,
                "HA_Bear_Reversal": False,
            }
        )
        rows.append(row)
    m15 = frame(rows)

    m5 = pd.DataFrame(
        [
            {"open": 100.0, "high": 101.0, "low": 98.0, "close": 99.0},
            {"open": 97.0, "high": 99.0, "low": 95.0, "close": 96.0},
            {"open": 92.0, "high": 96.0, "low": 91.5, "close": 93.0},
            # First closed green reaction at Kivanc/BB. Range is below $10.
            {"open": 91.0, "high": 95.0, "low": 89.8, "close": 93.5},
            # Second closed green reaction confirms the same level. Its own
            # range is below $10, but the bounded M5 excursion is $11.6.
            {"open": 91.5, "high": 96.0, "low": 89.4, "close": 94.0},
            # Provider-forming row is ignored.
            {"open": 94.0, "high": 95.0, "low": 93.0, "close": 94.5},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )

    result = runtime._overlay_blueprint_prz_memory(
        m15,
        Blueprint(),
        lock_bars=4,
        df_5m=m5,
        df_1h=None,
    )
    assert_true(
        bool(result["V4_Buy_M5_Sniper_Evidence"].iloc[-1]),
        "two closed M5 reactions may confirm a distributed decline",
    )
    assert_equal(
        result["V4_Buy_M5_Sniper_Mode"].iloc[-1],
        "TWO_POINT_RECLAIM",
        "owner trace identifies the two-point path",
    )
    assert_equal(
        int(result["V4_Buy_M5_Sniper_Point_Count"].iloc[-1]),
        2,
        "second green reaction is the executable confirmation",
    )
    assert_equal(
        result["V4_Buy_Trigger_Source"].iloc[-1],
        "M5_TWO_POINT_RECLAIM",
        "entry source must not be confused with a one-candle spike",
    )
    candidate = BuySignalEngine().evaluate(result, 2, NY_SESSION, ALLOWED)
    assert_true(candidate is not None, "two-point BUY reaches the engine")
    assert_equal(
        candidate["entry_mode"],
        "V4_BUY_M5_TWO_POINT_RECLAIM",
        "EA payload keeps the two-point M5 source",
    )

    forming_result = runtime._overlay_blueprint_prz_memory(
        m15,
        Blueprint(),
        lock_bars=4,
        df_5m=m5.iloc[:5].copy(),
        df_1h=None,
    )
    assert_true(
        not bool(forming_result["V4_Buy_M5_Sniper_Evidence"].any()),
        "a forming second point cannot release the BUY",
    )

def test_m5_sniper_ignores_forming_provider_candle() -> None:
    class Blueprint:
        htf_prz_support_low = 88.0
        htf_prz_support_high = 92.0
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 0.0
        prz_a_support_high = 0.0
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0

    rows = []
    for position in range(3):
        row = base_row()
        row.update(
            {
                "BB_Lower": 91.0,
                "Fib_0786": 90.0,
                "Deep_Buy_PRZ_Context": False,
                "Deep_Sell_PRZ_Context": False,
                "HA_Bull_Reversal": position == 2,
                "V4_Buy_Setup": False,
                "V4_Sell_Setup": False,
            }
        )
        rows.append(row)
    rows[-1].update({"low": 89.0, "high": 101.0, "close": 96.0})
    m5 = pd.DataFrame(
        [
            {"open": 98.0, "high": 99.0, "low": 96.0, "close": 97.0},
            {"open": 97.0, "high": 98.0, "low": 95.0, "close": 96.0},
            {"open": 96.0, "high": 97.0, "low": 94.0, "close": 95.0},
            {"open": 96.0, "high": 97.0, "low": 95.0, "close": 96.0},
            {"open": 96.0, "high": 97.0, "low": 95.0, "close": 96.0},
            # The only apparent sniper is still forming and must be discarded.
            {"open": 100.0, "high": 101.0, "low": 89.0, "close": 96.0},
        ],
        index=pd.date_range("2026-07-10 15:15", periods=6, freq="5min", tz="UTC"),
    )
    result = runtime._overlay_blueprint_prz_memory(
        frame(rows),
        Blueprint(),
        lock_bars=4,
        df_5m=m5,
        df_1h=None,
    )
    assert_true(
        not bool(result["V4_Buy_M5_Sniper_Evidence"].any()),
        "forming M5 wick cannot arm or open a trade",
    )

def test_scanner_prz_ignores_forming_candle_extremes() -> None:
    rows = [
        {"open": 95.0, "high": 100.0, "low": 90.0, "close": 96.0},
        {"open": 96.0, "high": 99.0, "low": 91.0, "close": 95.0},
        # Deliberately impossible forming wick; it must not repaint the zone.
        {"open": 95.0, "high": 1000.0, "low": 1.0, "close": 95.0},
    ]
    support_low, _, _, resistance_high = ScenarioScanner()._prz_zone(
        frame(rows)
    )
    assert_equal(support_low, 90.0, "forming low cannot move confirmed PRZ")
    assert_equal(resistance_high, 100.0, "forming high cannot move confirmed PRZ")
    forecast = ScenarioScanner()._prz_forecast_grid(frame(rows), 92.0)
    assert_equal(round(forecast["prz_a_support_low"], 2), 92.95, "PRZ-A demand 0.705")
    assert_equal(round(forecast["prz_a_support_high"], 2), 93.82, "PRZ-A demand 0.618")
    assert_equal(round(forecast["prz_b_support_low"], 2), 91.20, "PRZ-B demand 0.88")
    assert_equal(round(forecast["prz_b_support_high"], 2), 92.80, "PRZ-B demand 0.72")
    assert_equal(forecast["active_name"], "PRZ-B DEMAND", "deep H1 box is observable")

def test_full_scenario_scan_populates_h1_prz_a_and_b() -> None:
    rows = []
    for index in range(120):
        base = 100.0 + ((index % 20) - 10) * 0.5 + index * 0.02
        rows.append(
            {
                "open": base - 0.2,
                "high": base + 1.0,
                "low": base - 1.0,
                "close": base + 0.2,
            }
        )
    data = pd.DataFrame(rows)
    original_log = os.environ.get("ALPHA_SCANNER_STATE_LOG")
    try:
        os.environ["ALPHA_SCANNER_STATE_LOG"] = "off"
        blueprint = ScenarioScanner().scan(
            data.copy(),
            data.copy(),
            data.copy(),
            symbol="XAUUSD",
        )
    finally:
        if original_log is None:
            os.environ.pop("ALPHA_SCANNER_STATE_LOG", None)
        else:
            os.environ["ALPHA_SCANNER_STATE_LOG"] = original_log

    assert_equal(blueprint.prz_forecast_status in {"READY", "ACTIVE"}, True, "H1 forecast status")
    assert_true(blueprint.prz_a_support_low > 0, "PRZ-A demand is populated")
    assert_true(blueprint.prz_b_support_low > 0, "PRZ-B demand is populated")
    assert_true(
        blueprint.prz_a_support_low < blueprint.prz_a_support_high,
        "PRZ-A demand bounds are ordered",
    )
    assert_true(
        blueprint.prz_b_resistance_low < blueprint.prz_b_resistance_high,
        "PRZ-B supply bounds are ordered",
    )

def test_opposite_bos_cancels_prz_memory_before_ha_flip() -> None:
    class Blueprint:
        htf_prz_support_low = 97.5
        htf_prz_support_high = 98.5
        htf_prz_resistance_low = 0.0
        htf_prz_resistance_high = 0.0
        prz_a_support_low = 0.0
        prz_a_support_high = 0.0
        prz_a_resistance_low = 0.0
        prz_a_resistance_high = 0.0
        prz_b_support_low = 0.0
        prz_b_support_high = 0.0
        prz_b_resistance_low = 0.0
        prz_b_resistance_high = 0.0

    touch = base_row()
    touch.update(
        {
            "high": 102.0,
            "low": 98.0,
            "Bullish_Pinbar": True,
            "Bearish_Pinbar": False,
            "HA_Bull_Reversal": False,
            "HA_Bear_Reversal": False,
            "Deep_Buy_PRZ_Context": False,
            "Deep_Sell_PRZ_Context": False,
            "Bull_Sweep": False,
            "Bear_Sweep": False,
            "Deep_Buy_Reclaim_Trigger": False,
            "Deep_Sell_Reclaim_Trigger": False,
            "Zone_Buy_Pinbar_Trigger": False,
            "Zone_Sell_Pinbar_Trigger": False,
            "In_Session_Kivanc_Buy_Zone": False,
            "In_Session_Kivanc_Sell_Zone": False,
            "Micro_BOS_Up": False,
            "V4_Buy_Setup": False,
            "V4_Sell_Setup": False,
        }
    )
    invalidated = dict(touch)
    invalidated.update(
        {
            "high": 105.0,
            "low": 101.0,
            "Bullish_Pinbar": False,
            "CHoCH_Bear": True,
        }
    )
    late_flip = dict(invalidated)
    late_flip.update(
        {
            "CHoCH_Bear": False,
            "HA_Bull_Reversal": True,
        }
    )

    result = runtime._overlay_blueprint_prz_memory(
        frame([touch, invalidated, late_flip]),
        Blueprint(),
        lock_bars=4,
    )

    assert_true(not bool(result["V4_Buy_Location_Memory"].iloc[2]), "opposite BOS clears location")
    assert_true(not bool(result["V4_Buy_Memory_Trigger"].iloc[2]), "late HA cannot revive invalid setup")
    assert_true(not bool(result["V4_Buy_Setup"].iloc[2]), "no BUY after invalidation")
    diagnostic = runtime._engine_v4_wait_diagnostics(result)
    assert_equal(diagnostic["status"], "WAIT_REARM", "owner sees cancelled setup")
    assert_true(
        "CANCELLED_BY_BEAR_BOS_CHOCH" in diagnostic["missing_buy"],
        "owner receives the exact structural cancellation",
    )

def test_v4_pattern_comparison_routes_only_to_owner() -> None:
    original_owner = runtime.TELEGRAM_OWNER_CHAT_IDS
    original_group = runtime.TELEGRAM_CHAT_IDS
    try:
        runtime.TELEGRAM_OWNER_CHAT_IDS = ["owner-only"]
        runtime.TELEGRAM_CHAT_IDS = ["group-only"]
        assert_equal(runtime._telegram_targets("OWNER"), ["owner-only"], "owner destination")
        assert_equal(runtime._telegram_targets("GROUP"), ["group-only"], "group destination")

        payload = {
            "symbol": "XAUUSD",
            "signal": {
                "engine_v4_diagnostics": {
                    "status": "BUY_ARMED",
                    "v4_selected": False,
                    "current_price": 4060.51,
                    "context_direction": "BUY",
                    "buy_prz_layer_count": 4,
                    "sell_prz_layer_count": 0,
                    "buy_evidence_score": 7,
                    "sell_evidence_score": 0,
                    "evidence_min": 3,
                    "buy_armed": True,
                    "sell_armed": False,
                    "buy_trigger_source": "NONE",
                    "recent_prz_touch": True,
                    "recent_buy_prz_touch": True,
                    "recent_sell_prz_touch": False,
                    "buy_touch_time": "2026-07-23T14:30:00+00:00",
                    "recent_kivanc_state": "BUY_ZONE_ARMED",
                    "missing_buy": ["WAIT_HA_OR_PINBAR_OR_M5_SNIPER"],
                    "latest": {
                        "Pine_PRZ_Support_Low": 4025.0,
                        "Pine_PRZ_Support_High": 4045.0,
                        "Fib_0618": 4048.0,
                        "Fib_072": 4042.0,
                        "Fib_0786": 4038.0,
                        "Fib_0886": 4029.0,
                        "Fib_100": 4018.0,
                    },
                },
                "blueprint": {
                    "current_price": 4060.51,
                    "harmonic": {
                        "is_real_harmonic": True,
                        "pattern": "Bullish Bat",
                        "state": "FORMING",
                        "source_tf": "1H",
                        "direction": "BUY",
                        "candidate_patterns": [
                            {"pattern": "Bullish Bat"},
                            {"pattern": "Bullish Gartley"},
                        ],
                    },
                    "prz_layers": {
                        "htf": {},
                        "tunnel_state": {
                            "state": "DOWNTREND",
                            "valid": True,
                            "retest_valid": True,
                            "buy_sweep_armed": False,
                            "sell_sweep_armed": False,
                        },
                    },
                },
            },
            "ea": {"action": "WAIT"},
        }
        context = runtime._owner_v4_context(payload)
        message = runtime.format_telegram_owner_v4_context(payload)

        assert_true(context["eligible"], "PRZ/pattern context must be owner-observable")
        assert_true("BULLISH BAT" in message, "selected harmonic is compared")
        assert_true("BULLISH GARTLEY" in message, "candidate pattern is compared")
        assert_true("Layers B/S 4 / 0" in message, "PRZ layers are explicit")
        assert_true(
            "WAIT_HA_OR_PINBAR_OR_M5_SNIPER" in message,
            "the exact remaining trigger is explicit",
        )
        assert_true(
            "ARMED; รอ HA / Pinbar break / M5 Sniper" in message,
            "armed context cannot be mistaken for an unqualified setup",
        )
        assert_true(
            "Entry cluster: Level 4,042.00 | PRZ 4,025.00-4,045.00 | WAIT CF"
            in message,
            "owner sees the selected entry level without exposing its formula",
        )
        assert_true(
            "Level state: BUY ARMED" in message,
            "owner sees only a neutral level state",
        )
        assert_true(
            all(
                hidden not in message
                for hidden in (
                    "Kivanc",
                    "KIVANC",
                    "| K ",
                    "0.618",
                    "0.720",
                    "0.786",
                    "0.886",
                    "1.000",
                )
            ),
            "owner Telegram must not expose proprietary level names or ratios",
        )
        assert_true("EA: HOLD" in message, "private context cannot claim execution")
    finally:
        runtime.TELEGRAM_OWNER_CHAT_IDS = original_owner
        runtime.TELEGRAM_CHAT_IDS = original_group
