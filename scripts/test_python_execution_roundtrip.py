#!/usr/bin/env python3
"""Python engine -> durable EA OPEN -> fill/ACK regression check."""

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BROKER_SYMBOL = "XAUUSD-STDc"


def ready_payload() -> dict:
    return {
        "status": "SIGNAL",
        "symbol": "XAUUSD",
        "signal": {"status": "SIGNAL", "direction": "SELL"},
        "ea": {
            "signal_id": "XAUUSD-PYTHON-E2E-SELL-001",
            "symbol": "XAUUSD",
            "action": "OPEN",
            "execution_state": "READY",
            "direction": "SELL",
            "entry": 4100.0,
            "sl": 4110.0,
            "tp1": 4090.0,
            "tp_final": 4070.0,
            "score": 5,
            "target_source": "DEMAND_PRZ",
            "reason": "BASELINE_SELL_CONFIRMED",
        },
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["ALPHA_SIGNAL_SOURCE"] = "PYTHON"
        os.environ["ALPHA_API_KEY"] = "PYTHON_TEST_KEY"
        os.environ["TELEGRAM_NOTIFY_STARTUP"] = "false"
        os.environ["ALPHA_PYTHON_BRIDGE_STATE_FILE"] = str(
            Path(temporary) / "python-bridge.json"
        )
        os.environ["ALPHA_EXECUTION_STATE_FILE"] = str(
            Path(temporary) / "execution.json"
        )

        from fastapi.testclient import TestClient
        import alpha_buffalo_signal as service
        from pine_signal_bridge import PineSignalBridge

        service.send_telegram_message = lambda message: True
        payload = ready_payload()
        service._set_latest_signal(payload)
        queued = service._publish_python_entry_command(payload)
        assert queued["action"] == "OPEN"
        assert queued["source"] == "PYTHON"
        assert queued["command_owner"] == "PYTHON_CLOUD"
        assert queued["command_id"].startswith("PYTHON:")

        # Prove pending entry survives a Railway process restart.
        service.python_signal_bridge = PineSignalBridge(
            os.environ["ALPHA_PYTHON_BRIDGE_STATE_FILE"],
            accepted_source="PYTHON",
            command_prefix="PYTHON",
            command_owner="PYTHON_CLOUD",
            open_ttl_env="ALPHA_PYTHON_OPEN_TTL_SECONDS",
            close_ttl_env="ALPHA_PYTHON_CLOSE_TTL_SECONDS",
        )

        client = TestClient(service.app)
        legacy_poll = client.get(
            "/execution/command",
            params={"key": "PYTHON_TEST_KEY", "symbol": BROKER_SYMBOL},
        )
        assert legacy_poll.status_code == 200, legacy_poll.text
        assert legacy_poll.json()["command"]["action"] == "HOLD"
        assert (
            legacy_poll.json()["command"]["reason"]
            == "USE_DEDICATED_PYTHON_ENDPOINT"
        )

        polled = client.get(
            "/execution/python/command",
            params={
                "key": "PYTHON_TEST_KEY",
                "symbol": BROKER_SYMBOL,
                "client_id": "RAILWAY_PYTHON_V1",
                "account_id": "33740165",
                "balance": 10000.0,
                "equity": 10000.0,
                "day_start_equity": 10000.0,
            },
        )
        assert polled.status_code == 200, polled.text
        assert polled.json()["source"] == "PYTHON"
        assert polled.json()["consumer"] == "RAILWAY_PYTHON_V1"
        command = polled.json()["command"]
        assert command["action"] == "OPEN"
        assert command["direction"] == "SELL"
        assert command["symbol"] == "XAUUSD"

        filled = client.post(
            "/execution/fill",
            json={
                "key": "PYTHON_TEST_KEY",
                "symbol": BROKER_SYMBOL,
                "signal_id": command["signal_id"],
                "ticket": "MT5-PYTHON-1",
                "fill_price": 4100.0,
            },
        )
        assert filled.status_code == 200, filled.text
        assert filled.json()["position"]["direction"] == "SELL"
        assert filled.json()["position"]["symbol"] == BROKER_SYMBOL

        acknowledged = client.post(
            "/execution/ack",
            json={
                "key": "PYTHON_TEST_KEY",
                "symbol": BROKER_SYMBOL,
                "command_id": command["command_id"],
                "success": True,
            },
        )
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["source"] == "PYTHON"
        assert acknowledged.json()["result"]["acknowledged"] is True
        idle = service.python_signal_bridge.pending_command(BROKER_SYMBOL)
        assert idle["action"] == "HOLD"
        assert idle["reason"] == "NO_PENDING_PYTHON_COMMAND"

        duplicate = service._publish_python_entry_command(payload)
        assert duplicate["action"] == "HOLD"
        assert duplicate["reason"] == "DUPLICATE_ACKED_SIGNAL"

    print("PASS confirmed Python signal becomes a durable EA OPEN")
    print("PASS legacy command lane cannot race the dedicated Python EA")
    print("PASS XAUUSD broker suffix receives the canonical Python command")
    print("PASS pending Python OPEN survives a relay restart")
    print("PASS EA fill registers the broker position lifecycle")
    print("PASS Python OPEN ACK clears the entry command exactly once")
    print("PASS idle Python mode never reports a Pine-owned wait reason")
    print("Summary: 7/7 Python execution round-trip checks passed")


if __name__ == "__main__":
    main()
