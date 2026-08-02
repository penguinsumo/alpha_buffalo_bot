#!/usr/bin/env python3
"""End-to-end check that Harmonic has no entry authority at /webhook/tv."""
from __future__ import annotations

import os
from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def command(action: str, direction: str, signal_id: str) -> dict:
    if direction == "BUY":
        entry, sl, tp1, tp2 = 4000.0, 3990.0, 4010.0, 4025.0
    else:
        entry, sl, tp1, tp2 = 4000.0, 4010.0, 3990.0, 3975.0
    return {
        "key": "HARMONIC_TEST_KEY",
        "status": "SIGNAL",
        "source": "PINE",
        "strategy": "ALPHABUFF_V2_4",
        "action": action,
        "direction": direction,
        "symbol": "XAUUSD",
        "ticker_id": "OANDA:XAUUSD",
        "timeframe": "5",
        "signal_id": signal_id,
        "entry_price": entry,
        "exit_price": entry if action == "CLOSE" else None,
        "sl_price": sl,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "score": 5,
        "target_source": "SUPPLY_PRZ" if direction == "BUY" else "DEMAND_PRZ",
        "reason": "HARMONIC_GATE_TEST",
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        os.environ["ALPHA_SIGNAL_SOURCE"] = "PINE"
        os.environ["ALPHA_API_KEY"] = "HARMONIC_TEST_KEY"
        os.environ["TELEGRAM_PINE_MONITOR_ENABLED"] = "false"
        os.environ["ALPHA_PINE_BRIDGE_STATE_FILE"] = str(Path(temporary) / "bridge.json")
        os.environ["ALPHA_EXECUTION_STATE_FILE"] = str(Path(temporary) / "execution.json")

        from fastapi.testclient import TestClient
        import alpha_buffalo_signal as service
        from engine_v4.session_gate import GateResult

        service._market_open_gate = lambda: {
            "market_open": True,
            "block_reason": "",
            "session_state": None,
        }
        service._pine_entry_permission = lambda direction, symbol: GateResult(
            True,
            f"TEST_{str(direction).upper()}_MARKET_RISK_ALLOWED",
        )

        with TestClient(service.app) as client:
            opened_sell = client.post(
                "/webhook/tv",
                json=command("OPEN", "SELL", "XAUUSD-HARMONIC-SELL-OPEN"),
            )
            assert opened_sell.status_code == 200, opened_sell.text
            assert opened_sell.json()["command"]["direction"] == "SELL"

            close = command("CLOSE", "SELL", "XAUUSD-HARMONIC-SELL-OPEN")
            close.update(
                {
                    "reverse_direction": "BUY",
                    "reverse_entry_price": 4005.0,
                    "reverse_sl_price": 3995.0,
                    "reverse_tp1_price": 4015.0,
                    "reverse_tp2_price": 4030.0,
                    "reverse_score": 5,
                    "reverse_signal_id": "XAUUSD-HARMONIC-REV-BUY",
                    "reverse_target_source": "SUPPLY_PRZ",
                }
            )
            closed = client.post("/webhook/tv", json=close)
            assert closed.status_code == 200, closed.text
            close_command = closed.json()["command"]
            assert close_command["action"] == "CLOSE_ALL"
            assert close_command["after_ack"]["direction"] == "BUY"
            assert "reverse_blocked_reason" not in close_command

    print("PASS Harmonic cannot block a fresh SELL webhook")
    print("PASS CLOSE may retain a market/risk-approved reverse BUY")
    print("Summary: 2/2 harmonic target-only webhook checks passed")


if __name__ == "__main__":
    main()
