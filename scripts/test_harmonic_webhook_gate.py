#!/usr/bin/env python3
"""End-to-end checks for harmonic one-way entry bias at /webhook/tv."""
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
            str(direction).upper() == "BUY",
            "HARMONIC_BUY_D_PRZ_ALLOWED"
            if str(direction).upper() == "BUY"
            else "HARMONIC_BIAS_BUY_ONLY",
        )

        with TestClient(service.app) as client:
            blocked_sell = client.post(
                "/webhook/tv",
                json=command("OPEN", "SELL", "XAUUSD-HARMONIC-SELL-BLOCK"),
            )
            assert blocked_sell.status_code == 409, blocked_sell.text
            assert blocked_sell.json()["detail"] == "HARMONIC_BIAS_BUY_ONLY"

            opened_buy = client.post(
                "/webhook/tv",
                json=command("OPEN", "BUY", "XAUUSD-HARMONIC-BUY-OPEN"),
            )
            assert opened_buy.status_code == 200, opened_buy.text
            assert opened_buy.json()["command"]["direction"] == "BUY"

            close = command("CLOSE", "BUY", "XAUUSD-HARMONIC-BUY-OPEN")
            close.update(
                {
                    "reverse_direction": "SELL",
                    "reverse_entry_price": 3995.0,
                    "reverse_sl_price": 4005.0,
                    "reverse_tp1_price": 3985.0,
                    "reverse_tp2_price": 3970.0,
                    "reverse_score": 5,
                    "reverse_signal_id": "XAUUSD-HARMONIC-REV-SELL",
                    "reverse_target_source": "DEMAND_PRZ",
                }
            )
            closed = client.post("/webhook/tv", json=close)
            assert closed.status_code == 200, closed.text
            close_command = closed.json()["command"]
            assert close_command["action"] == "CLOSE_ALL"
            assert "after_ack" not in close_command
            assert close_command["reverse_blocked_reason"] == "HARMONIC_BIAS_BUY_ONLY"

    print("PASS bullish harmonic D blocks fresh SELL webhook")
    print("PASS aligned BUY webhook remains executable")
    print("PASS CLOSE passes while counter-bias reverse leg is removed")
    print("Summary: 3/3 harmonic webhook checks passed")


if __name__ == "__main__":
    main()
