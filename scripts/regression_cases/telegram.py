"""Extracted characterization cases; behavior intentionally unchanged."""
from scripts.regression_cases.common import *

def test_telegram_public_output_hides_engine_internals() -> None:
    engine = {
        "direction": "SELL",
        "entry_mode": "V4_SELL_PINE_PRZ_VSA",
        "setup_state": "SELL_CF_READY",
        "journey_state": "V5_SELL_JOURNEY",
        "entry": 100.0,
        "sl": 102.0,
        "tp": 90.0,
        "signal_tp": 95.0,
        "bb_lower_tp": 90.0,
        "prz_resistance_low": 99.0,
        "prz_resistance_high": 103.0,
    }
    payload = {
        "symbol": "XAUUSD",
        "signal": {
            "timestamp": "2026-07-10T15:00:00+00:00",
            "entry_mode": "V4_SELL_PINE_PRZ_VSA",
            "setup_state": "SELL_CF_READY",
            "scenario_state": "SELL_CF_READY",
            "journey_state": "V5_SELL_JOURNEY",
            "engine_v4": engine,
            "blueprint": {
                "current_price": 100.0,
                "trend_h1": "PULLBACK_UP",
                "trend_h4": "PULLBACK_DOWN",
                "price_action": {"m15_phase": "PULLBACK_DOWN"},
            },
        },
        "ea": {
            "action": "WAIT",
            "execution_state": "WATCH",
            "direction": "SELL",
            "entry_mode": "V4_SELL_PINE_PRZ_VSA",
            "entry": 100.0,
            "sl": 102.0,
            "tp_final": 90.0,
            "session": "ASIA",
        },
    }

    trend_text = format_telegram_trend_update(payload)
    signal_text = format_telegram_signal(payload)
    combined = trend_text + "\n" + signal_text

    forbidden = [
        "engine_v4",
        "V4_SELL_PINE_PRZ_VSA",
        "SELL_CF_READY",
        "V5_SELL_JOURNEY",
        "PINE_PRZ",
        "VSA",
        "BOS",
    ]
    for token in forbidden:
        assert_true(token not in combined, f"public Telegram output leaked {token}")

    assert_true(
        "Watch for ⚪ WAIT Setup..." in trend_text,
        "mixed trend evidence must remain WAIT and must not promote an engine candidate",
    )
    assert_true(
        "ALPHA BUFFALO" in signal_text,
        "V5 journey should use the public TP1/TP2 runner template",
    )

def test_pine_monitor_does_not_promote_blocked_buy_inside_sell_ha_context() -> None:
    """Regression for the 15 Jul chart: Demand location is not BUY permission."""
    payload = {
        "symbol": "XAUUSD",
        "signal": {
            "timestamp": "2026-07-14T23:03:00+00:00",
            "engine_v4": {
                "V4_Buy_Setup": True,
                "In_Pine_PRZ_Support": True,
                "V4_Block_Sell_At_Lower": True,
            },
            "blueprint": {
                "current_price": 4029.50,
                "trend_h1": "DOWN",
                "trend_h4": "DOWN",
                "price_action": {
                    "m15_phase": "PULLBACK_DOWN",
                    "h1_phase": "PULLBACK_DOWN",
                    "h4_phase": "IMPULSE_DOWN",
                    "m15_delta": "DOWN",
                    "h1_delta": "DOWN",
                    "h4_delta": "DOWN",
                    "ha_m15_bearish": True,
                    "ha_h1_bearish": True,
                    "watch_bias": "SELL",
                },
                "prz_layers": {
                    "htf": {
                        "support_low": 4018.82,
                        "support_high": 4042.06,
                        "resistance_low": 4103.54,
                        "resistance_high": 4126.20,
                    },
                },
            },
        },
        "ea": {
            "action": "WAIT",
            "execution_state": "WATCH",
            "direction": "BUY",
            "session": "ASIA",
        },
    }

    text = format_telegram_trend_update(payload)
    assert_true(
        "Watch for 🔴 S Setup..." in text,
        "confirmed H1 HA and MTF down own the public trend watch",
    )
    assert_true("Watch for 🟢 B Setup..." not in text, "monitor must never publish the blocked BUY as setup")
    assert_true("WAIT SETUP 🟢 BUY" not in text, "legacy false BUY watch label is removed")

    original_pipeline = runtime.run_pipeline
    try:
        runtime.run_pipeline = lambda: payload
        monitor = runtime._pine_monitor_payload()
    finally:
        runtime.run_pipeline = original_pipeline
    assert_equal(monitor["ea"]["direction"], "NONE", "watch-only monitor has no trade direction")
    assert_equal(
        monitor["ea"]["monitor_candidate_direction"],
        "BUY",
        "candidate is retained for private diagnostics only",
    )

def test_confirmed_open_is_the_only_public_directional_setup() -> None:
    payload = {
        "signal": {
            "blueprint": {
                "current_price": 4100.0,
                "price_action": {"h1_phase": "IMPULSE_UP", "ha_h1_bullish": True},
                "trend_h1": "UP",
                "trend_h4": "UP",
            },
        },
        "ea": {
            "action": "OPEN",
            "execution_state": "READY",
            "direction": "BUY",
            "session": "NY",
        },
    }
    text = format_telegram_trend_update(payload)
    assert_true("Watch for 🟢 B Setup..." in text, "confirmed OPEN may publish BUY setup")

def test_closed_market_suppresses_all_telegram() -> None:
    payload = {
        "symbol": "XAUUSD",
        "signal": {
            "timestamp": "2026-07-10T20:00:00+00:00",
            "gates": {"session": "CLOSED"},
            "blueprint": {"current_price": 100.0},
        },
        "ea": {
            "action": "OPEN",
            "execution_state": "READY",
            "direction": "BUY",
            "entry": 100.0,
            "sl": 98.0,
            "tp_final": 110.0,
            "rr": 5.0,
            "rr_ok": True,
            "levels_ready": True,
            "directional_levels_ok": True,
            "setup_ok": True,
            "zone_ok": True,
            "vsa_gate_ok": True,
            "session": "CLOSED",
        },
    }
    closed = SessionState(
        session="CLOSED",
        liquidity="NONE",
        bkk_hour=3,
        utc_hour=20,
        timestamp="2026-07-11T03:00:00+07:00",
    )
    sent = []
    original_clock_get = runtime.SessionClock.get
    original_enabled = runtime._telegram_enabled
    original_send = runtime.send_telegram_message
    original_notify = runtime.TELEGRAM_NOTIFY_TREND_UPDATE
    original_post = runtime.requests.post

    try:
        runtime.SessionClock.get = lambda self, dt=None: closed
        runtime._telegram_enabled = lambda: True
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = True
        runtime.send_telegram_message = lambda text: sent.append(text) or True

        runtime.maybe_broadcast_signal(payload)
        runtime.maybe_broadcast_trend_update(payload)
        assert_equal(sent, [], "closed session must block signal and trend broadcasts")

        runtime.send_telegram_message = original_send
        runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram network call must not occur while CLOSED")
        )
        assert_true(
            not runtime.send_telegram_message("closed market"),
            "direct Telegram sender must fail closed",
        )
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime._telegram_enabled = original_enabled
        runtime.send_telegram_message = original_send
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = original_notify
        runtime.requests.post = original_post

def test_h1_prz_wait_confirmation_does_not_require_harmonic() -> None:
    payload = {
        "source": "PYTHON",
        "symbol": "XAUUSD",
        "signal": {
            "decision": {"score": 3},
            "blueprint": {
                "current_price": 4005.0,
                "harmonic": {
                    "state": "NONE",
                    "direction": "NONE",
                    "bos_eligible": False,
                },
                "prz_layers": {
                    "htf": {
                        "timeframe": "1H",
                        "support_low": 3998.0,
                        "support_high": 4010.0,
                        "resistance_low": 4050.0,
                        "resistance_high": 4060.0,
                    }
                },
            },
        },
        "ea": {"action": "WAIT", "score": 3},
    }

    context = runtime._confirmation_event_context(payload)
    assert_true(context["eligible"], "H1 PRZ location must create WAIT-CF event")
    assert_equal(context["event"], "H1_PRZ", "H1 PRZ event type")
    assert_equal(context["direction"], "BUY", "H1 demand PRZ direction")
    text = runtime.format_telegram_confirmation(payload)
    assert_true("H1 PRZ" in text, "H1 PRZ must be visible in Telegram")
    assert_true("WAIT CONFIRM" in text, "H1 PRZ is confirmation-only")
    assert_true("Harmonic" not in text, "Harmonic must not gate H1 PRZ output")

def test_trend_update_is_hourly_except_h1_ema_or_rsi_regime_cross() -> None:
    payload = {
        "source": "PYTHON",
        "symbol": "XAUUSD",
        "signal": {
            "gates": {"session": "NY"},
            "blueprint": {
                "current_price": 4000.0,
                "trend_h1": "DOWN",
                "trend_h4": "DOWN",
                "price_action": {
                    "m15_phase": "IMPULSE_DOWN",
                    "h1_phase": "IMPULSE_DOWN",
                    "h4_phase": "IMPULSE_DOWN",
                },
                "h1_indicators": {
                    "ema_regime": "BELOW_EMA200",
                    "rsi_regime": "BELOW_RSI50",
                },
            },
        },
        "ea": {"action": "WAIT", "session": "NY"},
    }
    sent = []
    now = [datetime(2026, 7, 23, 1, 0, tzinfo=timezone.utc)]
    original_market_open = runtime._telegram_market_is_open
    original_enabled = runtime._telegram_enabled
    original_send = runtime.send_telegram_message
    original_now = runtime._trend_now_utc
    original_state_file = runtime.TELEGRAM_TREND_STATE_FILE
    original_notify = runtime.TELEGRAM_NOTIFY_TREND_UPDATE
    original_interval = runtime.TELEGRAM_TREND_MIN_INTERVAL_SECONDS
    original_key = runtime.LAST_TELEGRAM_TREND_UPDATE_KEY
    original_at = runtime.LAST_TELEGRAM_TREND_UPDATE_AT
    original_h1 = runtime.LAST_TELEGRAM_H1_CROSS_KEY
    original_loaded = runtime.LAST_TELEGRAM_TREND_STATE_LOADED
    try:
        runtime._telegram_market_is_open = lambda payload=None, now=None: True
        runtime._telegram_enabled = lambda audience="GROUP": True
        runtime.send_telegram_message = (
            lambda text, **kwargs: sent.append(text) or True
        )
        runtime._trend_now_utc = lambda: now[0]
        runtime.TELEGRAM_TREND_STATE_FILE = ""
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = True
        runtime.TELEGRAM_TREND_MIN_INTERVAL_SECONDS = 3600
        runtime.LAST_TELEGRAM_TREND_UPDATE_KEY = ""
        runtime.LAST_TELEGRAM_TREND_UPDATE_AT = None
        runtime.LAST_TELEGRAM_H1_CROSS_KEY = ""
        runtime.LAST_TELEGRAM_TREND_STATE_LOADED = True

        assert_true(runtime.maybe_broadcast_trend_update(payload), "initial trend")
        now[0] += timedelta(minutes=10)
        payload["signal"]["blueprint"]["price_action"]["m15_phase"] = "PULLBACK_UP"
        assert_true(
            not runtime.maybe_broadcast_trend_update(payload),
            "M15 noise inside one hour must stay silent",
        )

        now[0] += timedelta(minutes=10)
        payload["signal"]["blueprint"]["h1_indicators"]["rsi_regime"] = "ABOVE_RSI50"
        assert_true(
            runtime.maybe_broadcast_trend_update(payload),
            "confirmed H1 RSI regime cross must bypass hourly throttle",
        )

        now[0] += timedelta(minutes=30)
        assert_true(
            not runtime.maybe_broadcast_trend_update(payload),
            "unchanged H1 state remains throttled",
        )
        now[0] += timedelta(minutes=31)
        assert_true(runtime.maybe_broadcast_trend_update(payload), "hourly refresh")
        assert_equal(len(sent), 3, "initial + H1 cross + hourly")
    finally:
        runtime._telegram_market_is_open = original_market_open
        runtime._telegram_enabled = original_enabled
        runtime.send_telegram_message = original_send
        runtime._trend_now_utc = original_now
        runtime.TELEGRAM_TREND_STATE_FILE = original_state_file
        runtime.TELEGRAM_NOTIFY_TREND_UPDATE = original_notify
        runtime.TELEGRAM_TREND_MIN_INTERVAL_SECONDS = original_interval
        runtime.LAST_TELEGRAM_TREND_UPDATE_KEY = original_key
        runtime.LAST_TELEGRAM_TREND_UPDATE_AT = original_at
        runtime.LAST_TELEGRAM_H1_CROSS_KEY = original_h1
        runtime.LAST_TELEGRAM_TREND_STATE_LOADED = original_loaded

def test_weekend_is_hard_closed_before_session_resolution() -> None:
    saturday = pd.Timestamp("2026-07-11T10:00:00+07:00").to_pydatetime()
    sunday = pd.Timestamp("2026-07-12T20:00:00+07:00").to_pydatetime()
    for value in (saturday, sunday):
        assert_equal(market_closed_reason(value), "WEEKEND", "weekend close reason")
        assert_equal(SessionClock().get(value).session, "CLOSED", "weekend session")
        assert_true(
            not runtime._telegram_market_is_open(now=value),
            "weekend must block Telegram even during an intraday session hour",
        )

    summer_before_close = pd.Timestamp("2026-07-11T03:30:00+07:00").to_pydatetime()
    summer_after_close = pd.Timestamp("2026-07-11T04:30:00+07:00").to_pydatetime()
    winter_before_open = pd.Timestamp("2026-12-07T05:30:00+07:00").to_pydatetime()
    winter_after_open = pd.Timestamp("2026-12-07T06:30:00+07:00").to_pydatetime()
    assert_equal(market_closed_reason(summer_before_close), "", "Friday NY pre-close remains openable")
    assert_equal(market_closed_reason(summer_after_close), "WEEKEND", "Friday NY post-close")
    assert_equal(market_closed_reason(winter_before_open), "WEEKEND", "winter Sunday NY pre-open")
    assert_equal(market_closed_reason(winter_after_open), "", "winter Sunday NY post-open")

def test_seasonal_bangkok_sessions_survive_conflict_resolution() -> None:
    summer = pd.Timestamp("2026-07-14T04:30:00+07:00").to_pydatetime()
    winter_before = pd.Timestamp("2026-12-08T04:30:00+07:00").to_pydatetime()
    winter_after = pd.Timestamp("2026-12-08T05:30:00+07:00").to_pydatetime()
    assert_equal(SessionClock().get(summer).session, "ASIA", "summer ASIA opens at 04:00 BKK")
    assert_equal(SessionClock().get(winter_before).session, "CLOSED", "winter pre-ASIA gap")
    assert_equal(SessionClock().get(winter_after).session, "ASIA", "winter ASIA opens at 05:00 BKK")

def test_closed_market_pipeline_is_canonical_and_skips_data_fetch() -> None:
    closed = SessionState(
        session="CLOSED",
        liquidity="NONE",
        bkk_hour=10,
        utc_hour=3,
        timestamp="2026-07-12T10:00:00+07:00",
    )
    original_clock_get = runtime.SessionClock.get
    original_fetch = runtime.fetch_multi_tf
    try:
        runtime.SessionClock.get = lambda self, dt=None: closed
        runtime.fetch_multi_tf = lambda symbol: (_ for _ in ()).throw(
            AssertionError("closed-market pipeline must not fetch candles")
        )
        payload = runtime.run_pipeline()
        assert_equal(payload["status"], NO_SIGNAL, "closed market canonical status")
        assert_equal(payload["direction"], None, "closed market has no direction")
        assert_equal(payload["ea"]["action"], "WAIT", "EA waits while closed")
        assert_equal(payload["ea"]["execution_state"], "BLOCKED", "EA closed state")
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime.fetch_multi_tf = original_fetch

def test_configured_holiday_blocks_session_and_telegram() -> None:
    holiday = pd.Timestamp("2026-07-13T10:00:00+07:00").to_pydatetime()
    original = os.environ.get("ALPHA_MARKET_CLOSED_DATES")
    try:
        os.environ["ALPHA_MARKET_CLOSED_DATES"] = "2026-07-13"
        assert_equal(market_closed_reason(holiday), "CONFIGURED_HOLIDAY", "holiday reason")
        assert_equal(SessionClock().get(holiday).session, "CLOSED", "holiday session")
        assert_true(not runtime._telegram_market_is_open(now=holiday), "holiday Telegram gate")
    finally:
        if original is None:
            os.environ.pop("ALPHA_MARKET_CLOSED_DATES", None)
        else:
            os.environ["ALPHA_MARKET_CLOSED_DATES"] = original

def test_weekend_direct_sender_never_calls_telegram_network() -> None:
    weekend = pd.Timestamp("2026-07-12T20:00:00+07:00").to_pydatetime()
    original_clock_get = runtime.SessionClock.get
    original_enabled = runtime._telegram_enabled
    original_post = runtime.requests.post
    try:
        runtime.SessionClock.get = lambda self, dt=None: original_clock_get(self, weekend)
        runtime._telegram_enabled = lambda: True
        runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("Telegram network call must not occur on weekend")
        )
        assert_true(
            not runtime.send_telegram_message("weekend blocked"),
            "direct Telegram sender must fail closed on weekend",
        )
    finally:
        runtime.SessionClock.get = original_clock_get
        runtime._telegram_enabled = original_enabled
        runtime.requests.post = original_post

def test_every_repository_telegram_sender_uses_central_closed_gate() -> None:
    original_force = os.environ.get("ALPHA_FORCE_MARKET_CLOSED")
    original_runtime_enabled = runtime._telegram_enabled
    original_bot_token = telegram_bot_runtime.TOKEN
    original_post = telegram_guard_runtime.requests.post
    try:
        os.environ["ALPHA_FORCE_MARKET_CLOSED"] = "true"
        runtime._telegram_enabled = lambda: True
        telegram_bot_runtime.TOKEN = "test-token"
        telegram_guard_runtime.requests.post = lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("No repository Telegram sender may reach the network while closed")
        )

        assert_true(not runtime.send_telegram_message("blocked"), "runtime sender gate")
        assert_true(not warning_runtime.send_telegram("blocked"), "early warning sender gate")
        assert_true(not telegram_bot_runtime.send_message("1", "blocked"), "bot sender gate")
        assert_true(
            telegram_guard_runtime.guarded_telegram_post(
                "https://api.telegram.org/test",
                json={"text": "blocked"},
                timeout=1,
            ) is None,
            "network-layer sender gate",
        )

        for relative in (
            "alpha_buffalo_signal.py",
            "early_warning.py",
            "telegram_bot.py",
        ):
            source = (ROOT / relative).read_text(encoding="utf-8")
            assert_true("guarded_telegram_post" in source, f"{relative} central sender")
            assert_true("requests.post(" not in source, f"{relative} bypasses central sender")
    finally:
        if original_force is None:
            os.environ.pop("ALPHA_FORCE_MARKET_CLOSED", None)
        else:
            os.environ["ALPHA_FORCE_MARKET_CLOSED"] = original_force
        runtime._telegram_enabled = original_runtime_enabled
        telegram_bot_runtime.TOKEN = original_bot_token
        telegram_guard_runtime.requests.post = original_post
