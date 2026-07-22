#!/usr/bin/env python3
"""End-to-end Pine webhook -> EA command -> fill/ACK regression check."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BROKER_SYMBOL = "XAUUSD-VIP"


def payload(action: str) -> dict:
    command = {
        "key": "PINE_TEST_KEY",
        "status": "SIGNAL",
        "source": "PINE",
        "strategy": "ALPHABUFF_V2_4",
        "action": action,
        "direction": "BUY",
        "symbol": "XAUUSD",
        "ticker_id": "OANDA:XAUUSD",
        "timeframe": "5",
        "signal_id": "XAUUSD-E2E-BUY-001",
        "entry_price": 4120.0,
        "exit_price": 4140.0 if action == "CLOSE" else None,
        "sl_price": 4110.0,
        "tp1_price": 4130.0,
        "tp2_price": 4140.0,
        "score": 5,
        "target_source": "SUPPLY_PRZ",
        "reason": "E2E_TEST",
    }
    if action == "CLOSE":
        command.update(
            {
                "reverse_direction": "SELL",
                "reverse_entry_price": 4140.0,
                "reverse_sl_price": 4150.0,
                "reverse_tp1_price": 4130.0,
                "reverse_tp2_price": 4110.0,
                "reverse_score": 5,
                "reverse_signal_id": "XAUUSD-E2E-REV-SELL-002",
                "reverse_target_source": "DEMAND_PRZ",
                "reverse_reason": "KIVANC_PRZ_ARMED_HA15_ACK_REVERSE",
            }
        )
    return command


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["ALPHA_SIGNAL_SOURCE"] = "PINE"
        os.environ["ALPHA_API_KEY"] = "PINE_TEST_KEY"
        os.environ["TELEGRAM_PINE_MONITOR_ENABLED"] = "false"
        bridge_state = Path(temporary) / "bridge.json"
        os.environ["ALPHA_PINE_BRIDGE_STATE_FILE"] = str(bridge_state)
        os.environ["ALPHA_EXECUTION_STATE_FILE"] = str(Path(temporary) / "execution.json")

        from fastapi.testclient import TestClient
        import alpha_buffalo_signal as service

        service._market_open_gate = lambda: {
            "market_open": True,
            "block_reason": "",
            "session_state": None,
        }
        from engine_v4.session_gate import GateResult

        service._pine_entry_permission = lambda direction, symbol: GateResult(
            True, f"TEST_{direction}_ALLOWED"
        )
        telegram_messages = []
        service._telegram_market_is_open = lambda payload=None, now=None: True
        service.TELEGRAM_TOKEN = "test-token"
        service.TELEGRAM_CHAT_IDS = ["test-chat"]
        service.send_telegram_message = lambda message: telegram_messages.append(message) or True

        with TestClient(service.app) as client:
            opened = client.post("/webhook/tv", json=payload("OPEN"))
            assert opened.status_code == 200, opened.text
            assert opened.json()["telegram_notified"] is True
            assert len(telegram_messages) == 1
            assert "BUY" in telegram_messages[-1]
            assert "ALPHA BUFFALO" in telegram_messages[-1]
            assert "TP1" in telegram_messages[-1]
            assert "TP2" in telegram_messages[-1]
            assert "EA Executing" in telegram_messages[-1]
            assert "PINE_V2_4" not in telegram_messages[-1]
            assert "Signal accepted and queued" not in telegram_messages[-1]
            open_command = opened.json()["command"]
            assert open_command["action"] == "OPEN"

            telegram_status = client.get(
                "/telegram/status",
                params={"key": "PINE_TEST_KEY", "symbol": "XAUUSD"},
            )
            assert telegram_status.status_code == 200, telegram_status.text
            assert telegram_status.json()["telegram_enabled"] is True
            assert telegram_status.json()["pending_action"] == "OPEN"

            # Simulate loss of process memory and reload the still-pending
            # command from disk before the EA confirms its fill.
            from pine_signal_bridge import PineSignalBridge

            service._set_latest_signal({})
            service.pine_signal_bridge = PineSignalBridge(bridge_state)

            polled = client.get(
                "/execution/command",
                params={"key": "PINE_TEST_KEY", "symbol": BROKER_SYMBOL},
            )
            assert polled.status_code == 200, polled.text
            assert polled.json()["command"]["command_id"] == open_command["command_id"]

            filled = client.post(
                "/execution/fill",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": BROKER_SYMBOL,
                    "signal_id": "XAUUSD-E2E-BUY-001",
                    "ticket": "MT5-TEST-1",
                    "fill_price": 4120.0,
                },
            )
            assert filled.status_code == 200, filled.text
            assert filled.json()["telegram_notified"] is True
            assert len(telegram_messages) == 2
            assert "MT5 FILLED" in telegram_messages[-1]

            open_ack = client.post(
                "/execution/ack",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": BROKER_SYMBOL,
                    "command_id": open_command["command_id"],
                    "success": True,
                },
            )
            assert open_ack.status_code == 200, open_ack.text

            # Pine owns entries, but after the OPEN ACK the durable Python
            # lifecycle must still be allowed to issue TP1/BE/HA-exit commands.
            # Broker suffixes remain the position key while market-data calls
            # use the canonical Twelve Data symbol.
            originals = {
                "has_active": service.execution_lifecycle.has_active,
                "pending_command": service.execution_lifecycle.pending_command,
                "evaluate": service.execution_lifecycle.evaluate,
                "fetch": service._fetch_cached_tf,
                "latest": service._latest_market_price,
                "m5": service.fetch_management_m5,
            }
            observed = {}
            try:
                service.execution_lifecycle.has_active = lambda symbol: True
                service.execution_lifecycle.pending_command = lambda symbol: {
                    "action": "HOLD",
                    "reason": "NO_PENDING_COMMAND",
                    "symbol": symbol,
                }
                service._fetch_cached_tf = lambda symbol, interval, outputsize=200: (
                    observed.setdefault("data_15m", (symbol, interval)) or None
                )
                service._latest_market_price = lambda frame: 4130.0
                service.fetch_management_m5 = lambda symbol: observed.setdefault(
                    "data_m5", symbol
                )
                service.execution_lifecycle.evaluate = (
                    lambda symbol, price, frame: {
                        "command_id": "XAUUSD-E2E-BUY-001:PARTIAL_CLOSE_MOVE_BE:1",
                        "action": "PARTIAL_CLOSE_MOVE_BE",
                        "reason": "TP1_HIT",
                        "symbol": symbol,
                        "signal_id": "XAUUSD-E2E-BUY-001",
                        "ticket": "MT5-TEST-1",
                        "direction": "BUY",
                        "close_pct": 50.0,
                        "new_sl": 4120.0,
                        "remaining_pct": 100.0,
                    }
                )
                managed = client.get(
                    "/execution/command",
                    params={"key": "PINE_TEST_KEY", "symbol": BROKER_SYMBOL},
                )
                assert managed.status_code == 200, managed.text
                assert managed.json()["source"] == "LIFECYCLE"
                assert managed.json()["command"]["action"] == "PARTIAL_CLOSE_MOVE_BE"
                assert managed.json()["command"]["symbol"] == BROKER_SYMBOL
                assert observed["data_15m"] == ("XAU/USD", "15min")
                assert observed["data_m5"] == "XAU/USD"
            finally:
                service.execution_lifecycle.has_active = originals["has_active"]
                service.execution_lifecycle.pending_command = originals["pending_command"]
                service.execution_lifecycle.evaluate = originals["evaluate"]
                service._fetch_cached_tf = originals["fetch"]
                service._latest_market_price = originals["latest"]
                service.fetch_management_m5 = originals["m5"]

            closed = client.post("/webhook/tv", json=payload("CLOSE"))
            assert closed.status_code == 200, closed.text
            close_command = closed.json()["command"]
            assert close_command["action"] == "CLOSE_ALL"
            assert closed.json()["telegram_notified"] is True
            assert len(telegram_messages) == 3
            assert "Alpha Buffalo CLOSE" in telegram_messages[-1]

            close_ack = client.post(
                "/execution/ack",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": BROKER_SYMBOL,
                    "command_id": close_command["command_id"],
                    "success": True,
                    "r_multiple": 2.0,
                },
            )
            assert close_ack.status_code == 200, close_ack.text
            assert close_ack.json()["position"]["status"] == "CLOSED"
            assert close_ack.json()["next_command"]["action"] == "OPEN"
            assert close_ack.json()["next_command"]["direction"] == "SELL"
            assert close_ack.json()["telegram_notified"] is True
            assert len(telegram_messages) == 4
            assert "SELL" in telegram_messages[-1]

            reverse_poll = client.get(
                "/execution/command",
                params={"key": "PINE_TEST_KEY", "symbol": BROKER_SYMBOL},
            )
            assert reverse_poll.status_code == 200, reverse_poll.text
            reverse_command = reverse_poll.json()["command"]
            assert reverse_command["command_id"] == "PINE:XAUUSD-E2E-REV-SELL-002:OPEN"

            state = client.get(
                "/execution/state",
                params={"key": "PINE_TEST_KEY", "symbol": BROKER_SYMBOL},
            )
            assert state.status_code == 200, state.text
            assert state.json()["position"] is None

            # A server intentionally running the Python engine must not accept
            # a TradingView command and accidentally mix signal ownership.
            service.SIGNAL_SOURCE = "PYTHON"
            disabled = client.post("/webhook/tv", json=payload("OPEN"))
            assert disabled.status_code == 409, disabled.text
            assert disabled.json()["detail"] == "PINE_SIGNAL_MODE_DISABLED"

    print("PASS Pine OPEN webhook becomes one EA OPEN command")
    print("PASS broker symbol suffix resolves to the TradingView XAUUSD command")
    print("PASS EA fill survives relay restart and command ACK is accepted")
    print("PASS Pine mode delegates active-position TP/BE/HA exits to lifecycle")
    print("PASS broker symbol state uses canonical XAU/USD management data")
    print("PASS Pine CLOSE becomes EA CLOSE_ALL")
    print("PASS CLOSE ACK clears durable execution state")
    print("PASS CLOSE ACK promotes the queued reverse SELL OPEN")
    print("PASS Pine OPEN and promoted reverse OPEN notify Telegram exactly once")
    print("PASS Pine CLOSE and confirmed MT5 fill are visible in Telegram")
    print("PASS Telegram health exposes relay and pending-command state safely")
    print("PASS Python mode rejects Pine webhook ownership")
    print("Summary: 12/12 webhook round-trip checks passed")


if __name__ == "__main__":
    main()
