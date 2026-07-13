#!/usr/bin/env python3
"""Regression checks for Pine -> relay -> execution-only EA commands."""

from pathlib import Path
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pine_signal_bridge import (  # noqa: E402
    PineSignalBridge,
    PineSignalError,
    build_pine_api_payload,
)


def sample_payload(action: str = "OPEN", direction: str = "BUY") -> dict:
    if direction == "BUY":
        entry, sl, tp1, tp2 = 4120.0, 4110.0, 4130.0, 4145.0
    else:
        entry, sl, tp1, tp2 = 4120.0, 4130.0, 4110.0, 4095.0
    return {
        "status": "SIGNAL",
        "source": "PINE",
        "strategy": "ALPHABUFF_V2_4",
        "action": action,
        "direction": direction,
        "symbol": "OANDA:XAUUSD",
        "ticker_id": "OANDA:XAUUSD",
        "timeframe": "5",
        "signal_id": f"XAUUSD-TEST-{direction}",
        "entry_price": entry,
        "exit_price": entry + (5 if direction == "BUY" else -5),
        "sl_price": sl,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "score": 5,
        "target_source": "SUPPLY_PRZ" if direction == "BUY" else "DEMAND_PRZ",
        "reason": "TEST_FINAL_SIGNAL",
    }


def assert_rejected(bridge: PineSignalBridge, payload: dict, reason: str) -> None:
    try:
        bridge.ingest(payload)
    except PineSignalError as exc:
        assert str(exc) == reason, (str(exc), reason)
    else:
        raise AssertionError(f"Expected {reason}")


def main() -> None:
    with tempfile.TemporaryDirectory() as temporary:
        state_file = Path(temporary) / "bridge.json"
        bridge = PineSignalBridge(state_file)

        buy = sample_payload()
        opened = bridge.ingest(buy)
        assert opened["action"] == "OPEN"
        assert opened["symbol"] == "XAUUSD"
        assert opened["direction"] == "BUY"
        assert opened["command_owner"] == "PINE_TRADINGVIEW"

        duplicate = bridge.ingest(buy)
        assert duplicate["command_id"] == opened["command_id"]
        assert bridge.pending_command("XAU/USD")["action"] == "OPEN"

        public = build_pine_api_payload(opened)
        assert public["status"] == "SIGNAL"
        assert public["direction"] == "BUY"
        assert public["ea"]["action"] == "OPEN"
        assert public["ea"]["ea_execute_only"] is True

        # Pending state survives a process restart.
        reloaded = PineSignalBridge(state_file)
        assert reloaded.pending_command("XAUUSD")["command_id"] == opened["command_id"]

        acknowledged = reloaded.acknowledge(
            symbol="XAUUSD",
            command_id=opened["command_id"],
            success=True,
        )
        assert acknowledged["acknowledged"] is True
        assert reloaded.pending_command("XAUUSD")["action"] == "HOLD"

        # TradingView retry after ACK must never requeue the same command.
        retry = reloaded.ingest(buy)
        assert retry["action"] == "HOLD"
        assert retry["reason"] == "DUPLICATE_ACKED_SIGNAL"

        close_payload = sample_payload(action="CLOSE")
        closed = reloaded.ingest(close_payload)
        assert closed["action"] == "CLOSE_ALL"
        assert reloaded.pending_command("XAUUSD")["action"] == "CLOSE_ALL"

        no_signal = sample_payload()
        no_signal["status"] = "NO_SIGNAL"
        assert_rejected(reloaded, no_signal, "ONLY_SIGNAL_STATUS_ACCEPTED")

        bad_source = sample_payload()
        bad_source["source"] = "PYTHON"
        assert_rejected(reloaded, bad_source, "ONLY_PINE_SOURCE_ACCEPTED")

        invalid_levels = sample_payload(direction="SELL")
        invalid_levels["sl_price"] = 4100.0
        assert_rejected(reloaded, invalid_levels, "INVALID_SELL_LEVELS")

    print("PASS Pine OPEN is canonical and EA execution-only")
    print("PASS duplicate webhook delivery is idempotent")
    print("PASS ACK clears durable pending command")
    print("PASS duplicate ACKed signal never requeues")
    print("PASS Pine CLOSE becomes CLOSE_ALL")
    print("PASS NO_SIGNAL/non-Pine/invalid levels are rejected")
    print("Summary: 6/6 Pine-EA bridge checks passed")


if __name__ == "__main__":
    main()
