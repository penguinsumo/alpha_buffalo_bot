#!/usr/bin/env python3
"""End-to-end Pine webhook -> EA command -> fill/ACK regression check."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def payload(action: str) -> dict:
    return {
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


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["ALPHA_SIGNAL_SOURCE"] = "PINE"
        os.environ["ALPHA_API_KEY"] = "PINE_TEST_KEY"
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

        with TestClient(service.app) as client:
            opened = client.post("/webhook/tv", json=payload("OPEN"))
            assert opened.status_code == 200, opened.text
            open_command = opened.json()["command"]
            assert open_command["action"] == "OPEN"

            # Simulate loss of process memory and reload the still-pending
            # command from disk before the EA confirms its fill.
            from pine_signal_bridge import PineSignalBridge

            service._set_latest_signal({})
            service.pine_signal_bridge = PineSignalBridge(bridge_state)

            polled = client.get(
                "/execution/command",
                params={"key": "PINE_TEST_KEY", "symbol": "XAUUSD"},
            )
            assert polled.status_code == 200, polled.text
            assert polled.json()["command"]["command_id"] == open_command["command_id"]

            filled = client.post(
                "/execution/fill",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": "XAUUSD",
                    "signal_id": "XAUUSD-E2E-BUY-001",
                    "ticket": "MT5-TEST-1",
                    "fill_price": 4120.0,
                },
            )
            assert filled.status_code == 200, filled.text

            open_ack = client.post(
                "/execution/ack",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": "XAUUSD",
                    "command_id": open_command["command_id"],
                    "success": True,
                },
            )
            assert open_ack.status_code == 200, open_ack.text

            closed = client.post("/webhook/tv", json=payload("CLOSE"))
            assert closed.status_code == 200, closed.text
            close_command = closed.json()["command"]
            assert close_command["action"] == "CLOSE_ALL"

            close_ack = client.post(
                "/execution/ack",
                json={
                    "key": "PINE_TEST_KEY",
                    "symbol": "XAUUSD",
                    "command_id": close_command["command_id"],
                    "success": True,
                    "r_multiple": 2.0,
                },
            )
            assert close_ack.status_code == 200, close_ack.text
            assert close_ack.json()["position"]["status"] == "CLOSED"

            state = client.get(
                "/execution/state",
                params={"key": "PINE_TEST_KEY", "symbol": "XAUUSD"},
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
    print("PASS EA fill survives relay restart and command ACK is accepted")
    print("PASS Pine CLOSE becomes EA CLOSE_ALL")
    print("PASS CLOSE ACK clears durable execution state")
    print("PASS Python mode rejects Pine webhook ownership")
    print("Summary: 5/5 webhook round-trip checks passed")


if __name__ == "__main__":
    main()
