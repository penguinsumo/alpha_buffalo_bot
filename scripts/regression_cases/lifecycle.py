"""Extracted characterization cases; behavior intentionally unchanged."""
from scripts.regression_cases.common import *

def test_ha5_uses_two_closed_bars() -> None:
    bearish = closed_ha5_evidence(_m5_trend("DOWN"))
    bullish = closed_ha5_evidence(_m5_trend("UP"))
    assert_true(bearish["two_bearish"], "two completed HA5 red bars required")
    assert_true(bullish["two_bullish"], "two completed HA5 green bars required")
    assert_equal(len(bearish["timestamps"]), 2, "HA evidence exposes exactly two closed bars")

def test_live_m5_extreme_detects_tp1_between_polls() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="m5-hit", ticket="100", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=110, filled_at="2026-07-10T14:59:00+00:00",
    )
    command = manager.evaluate("XAUUSD", 103, _m5_trend("UP"))
    assert_equal(command["action"], "PARTIAL_CLOSE_MOVE_BE", "M5 high detects missed TP1 touch")

def test_lifecycle_buy_tp1_be_then_ha5_exit_is_idempotent() -> None:
    manager = ExecutionLifecycleManager()
    first = manager.register_fill(
        symbol="XAUUSD", signal_id="buy-1", ticket="101", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=120, filled_at="2026-07-10T14:50:00+00:00",
    )
    repeated = manager.register_fill(
        symbol="XAUUSD", signal_id="buy-1", ticket="101", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=120, filled_at="2026-07-10T14:50:00+00:00",
    )
    assert_equal(repeated, first, "same fill retry must not reset position")

    tp1 = manager.evaluate("XAUUSD", 105)
    retry = manager.evaluate("XAUUSD", 105)
    assert_equal(tp1["action"], "PARTIAL_CLOSE_MOVE_BE", "TP1 command")
    assert_equal(retry["command_id"], tp1["command_id"], "poll retry keeps command id")
    state = manager.acknowledge(
        symbol="XAUUSD", command_id=tp1["command_id"], success=True, remaining_pct=50,
        acknowledged_at="2026-07-10T15:00:00+00:00",
    )
    assert_true(state["tp1_done"] and state["be_armed"], "TP1 ACK arms BE")
    assert_equal(state["sl"], 100.0, "BUY SL moves to entry")

    post_be = _m5_trend("DOWN", offset=10, start="2026-07-10 15:05")
    pre_be = pd.DataFrame(
        {"open": [101], "high": [102], "low": [95], "close": [101]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-10T14:55:00Z")]),
    )
    close = manager.evaluate(
        "XAUUSD", 104, pd.concat([pre_be, post_be]), now="2026-07-10T15:40:00+00:00"
    )
    assert_equal(close["action"], "CLOSE_ALL", "opposite HA5 closes BUY after BE")
    assert_equal(close["reason"], "HA5_OPPOSITE_2_AFTER_BE", "HA5 close reason")
    manager.acknowledge(
        symbol="XAUUSD", command_id=close["command_id"], success=True, r_multiple=1.5,
    )
    assert_true(not manager.has_active("XAUUSD"), "close ACK removes position")

def test_lifecycle_sell_mirror_and_hard_risk_gate() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="sell-1", ticket="201", direction="SELL",
        entry=100, sl=102, tp1=95, tp2=80, filled_at="2026-07-10T14:50:00+00:00",
    )
    tp1 = manager.evaluate("XAUUSD", 95)
    manager.acknowledge(
        symbol="XAUUSD", command_id=tp1["command_id"], success=True, remaining_pct=50,
        acknowledged_at="2026-07-10T15:00:00+00:00",
    )
    post_be = _m5_trend("UP", offset=-10, start="2026-07-10 15:05")
    pre_be = pd.DataFrame(
        {"open": [99], "high": [105], "low": [98], "close": [99]},
        index=pd.DatetimeIndex([pd.Timestamp("2026-07-10T14:55:00Z")]),
    )
    close = manager.evaluate(
        "XAUUSD", 96, pd.concat([pre_be, post_be]), now="2026-07-10T15:40:00+00:00"
    )
    assert_equal(close["reason"], "HA5_OPPOSITE_2_AFTER_BE", "SELL uses two green HA5")
    manager.acknowledge(
        symbol="XAUUSD", command_id=close["command_id"], success=True, r_multiple=1.0,
    )

    for count in range(3):
        signal_id = f"loss-{count}"
        manager.register_fill(
            symbol="XAUUSD", signal_id=signal_id, ticket=signal_id, direction="BUY",
            entry=100, sl=98, tp1=105, tp2=110,
        )
        stop = manager.evaluate("XAUUSD", 98)
        manager.acknowledge(
            symbol="XAUUSD", command_id=stop["command_id"], success=True, r_multiple=-1.0,
        )
    assert_true(not manager.risk_permissions("XAUUSD")["daily_dd_ok"], "3R daily loss blocks entries")

def test_active_position_forces_ea_to_management_only() -> None:
    manager = ExecutionLifecycleManager()
    manager.register_fill(
        symbol="XAUUSD", signal_id="active-1", ticket="301", direction="BUY",
        entry=100, sl=98, tp1=105, tp2=110,
    )
    original_manager = runtime.execution_lifecycle
    original_fetch_m5 = runtime.fetch_management_m5
    try:
        runtime.execution_lifecycle = manager
        runtime.fetch_management_m5 = lambda symbol: _m5_trend("UP")
        managed = runtime._attach_execution_lifecycle(
            data_symbol="XAU/USD",
            public_symbol="XAUUSD",
            df_15m=pd.DataFrame({"close": [101.0]}),
            ea={"action": "OPEN", "execution_state": "READY", "plan_lifecycle": {}},
        )
        assert_equal(managed["action"], "WAIT", "active position blocks another open")
        assert_equal(managed["execution_state"], "MANAGING", "EA becomes management-only")
    finally:
        runtime.execution_lifecycle = original_manager
        runtime.fetch_management_m5 = original_fetch_m5

def test_execution_api_fill_and_ack_round_trip() -> None:
    class JsonRequest:
        def __init__(self, payload):
            self.payload = payload

        async def json(self):
            return self.payload

    manager = ExecutionLifecycleManager()
    original_manager = runtime.execution_lifecycle
    original_cache = runtime._get_latest_signal()
    plan = {
        "signal_id": "api-buy-1",
        "action": "OPEN",
        "execution_state": "READY",
        "direction": "BUY",
        "entry": 100.0,
        "sl": 98.0,
        "tp1": 105.0,
        "tp_final": 110.0,
        "max_bars": 40,
    }
    try:
        runtime.execution_lifecycle = manager
        runtime._set_latest_signal({"symbol": "XAUUSD", "ea": plan})
        fill = asyncio.run(runtime.execution_fill(JsonRequest({
            "key": API_LICENSE_KEY,
            "symbol": "XAUUSD",
            "signal_id": "api-buy-1",
            "ticket": "9001",
            "fill_price": 100.0,
        })))
        assert_equal(fill["status"], "accepted", "fill endpoint accepts matching ready plan")
        command = manager.evaluate("XAUUSD", 105.0)
        ack = asyncio.run(runtime.execution_ack(JsonRequest({
            "key": API_LICENSE_KEY,
            "symbol": "XAUUSD",
            "command_id": command["command_id"],
            "success": True,
            "remaining_pct": 50,
        })))
        assert_true(ack["result"]["be_armed"], "ACK endpoint persists BE state")
    finally:
        runtime.execution_lifecycle = original_manager
        runtime._set_latest_signal(original_cache)
