#!/usr/bin/env python3
"""Durable, idempotent relay for final Pine commands consumed by an EA.

Pine owns analysis, PRZ construction, entry, and exit decisions.  This module
does not calculate market logic.  It validates and relays only final OPEN or
CLOSE commands to the execution-only EA.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Any, Mapping

from signal_schema import SIGNAL, create_signal, validate_directional_prices


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) and number > 0 else 0.0


def _symbol(value: Any) -> str:
    symbol = str(value or "XAUUSD").strip().upper().split(":")[-1]
    return symbol.replace("/", "")


class PineSignalError(ValueError):
    """A rejected Pine payload with a stable API error code."""


class PineSignalBridge:
    def __init__(self, state_file: str | Path | None = None) -> None:
        self._lock = threading.RLock()
        self._state_file = Path(state_file) if state_file else None
        self._pending: dict[str, dict[str, Any]] = {}
        self._acked: dict[str, dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        if not self._state_file or not self._state_file.is_file():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._pending = dict(payload.get("pending") or {})
            self._acked = dict(payload.get("acked") or {})
        except Exception:
            self._pending = {}
            self._acked = {}

    def _save(self) -> None:
        if not self._state_file:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {
                    "pending": self._pending,
                    "acked": self._acked,
                    "updated_at": _iso_now(),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        os.replace(temporary, self._state_file)

    @staticmethod
    def _normalize_action(value: Any) -> str:
        action = str(value or "").strip().upper()
        aliases = {
            "ENTRY": "OPEN",
            "EXIT": "CLOSE",
            "CLOSE_ALL": "CLOSE",
        }
        return aliases.get(action, action)

    @staticmethod
    def _reverse_open_command(
        payload: Mapping[str, Any],
        *,
        symbol: str,
        closing_direction: str,
        received_at: str,
        now: float,
    ) -> dict[str, Any] | None:
        """Build the OPEN that may be promoted only after a CLOSE ACK."""
        reverse_direction = str(payload.get("reverse_direction") or "").upper()
        if not reverse_direction:
            return None
        if reverse_direction not in {"BUY", "SELL"}:
            raise PineSignalError("INVALID_REVERSE_DIRECTION")
        if reverse_direction == closing_direction:
            raise PineSignalError("REVERSE_DIRECTION_MUST_BE_OPPOSITE")

        reverse_signal_id = str(payload.get("reverse_signal_id") or "").strip()
        if not reverse_signal_id or len(reverse_signal_id) > 160:
            raise PineSignalError("INVALID_REVERSE_SIGNAL_ID")

        entry = _number(payload.get("reverse_entry_price"))
        sl = _number(payload.get("reverse_sl_price"))
        tp1 = _number(payload.get("reverse_tp1_price"))
        tp2 = _number(payload.get("reverse_tp2_price"))
        levels_ok, levels_reason = validate_directional_prices(
            reverse_direction, entry, sl, tp1, tp2
        )
        if not levels_ok:
            raise PineSignalError(f"INVALID_REVERSE_{levels_reason}")

        try:
            score = int(float(payload.get("reverse_score") or 0))
        except (TypeError, ValueError):
            score = 0
        open_ttl = max(30, int(os.getenv("ALPHA_PINE_OPEN_TTL_SECONDS", "300")))
        command_id = f"PINE:{reverse_signal_id}:OPEN"
        return {
            "command_id": command_id,
            "action": "OPEN",
            "source": "PINE",
            "command_owner": "PINE_TRADINGVIEW",
            "symbol": symbol,
            "signal_id": reverse_signal_id,
            "direction": reverse_direction,
            "entry": entry,
            "exit_price": 0.0,
            "sl": sl,
            "tp1": tp1,
            "tp_final": tp2,
            "score": score,
            "target_source": str(
                payload.get("reverse_target_source") or "UNKNOWN"
            ),
            "reason": str(
                payload.get("reverse_reason") or "HA15_ACK_REVERSE"
            ),
            "ticker_id": str(payload.get("ticker_id") or ""),
            "timeframe": str(payload.get("timeframe") or ""),
            "received_at": received_at,
            "expires_at_epoch": now + open_ttl,
            "promote_after_ack": True,
        }

    def ingest(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """Validate one final Pine command and queue it exactly once."""
        if str(payload.get("status") or "").upper() != SIGNAL:
            raise PineSignalError("ONLY_SIGNAL_STATUS_ACCEPTED")
        if str(payload.get("source") or "").upper() != "PINE":
            raise PineSignalError("ONLY_PINE_SOURCE_ACCEPTED")

        action = self._normalize_action(payload.get("action"))
        if action not in {"OPEN", "CLOSE"}:
            raise PineSignalError("INVALID_PINE_ACTION")
        direction = str(payload.get("direction") or "").upper()
        if direction not in {"BUY", "SELL"}:
            raise PineSignalError("INVALID_PINE_DIRECTION")

        signal_id = str(payload.get("signal_id") or "").strip()
        if not signal_id or len(signal_id) > 160:
            raise PineSignalError("INVALID_SIGNAL_ID")

        symbol = _symbol(payload.get("symbol"))
        entry = _number(payload.get("entry_price"))
        sl = _number(payload.get("sl_price"))
        tp1 = _number(payload.get("tp1_price"))
        tp2 = _number(payload.get("tp2_price"))
        if action == "OPEN":
            levels_ok, levels_reason = validate_directional_prices(
                direction, entry, sl, tp1, tp2
            )
            if not levels_ok:
                raise PineSignalError(levels_reason)
        elif not entry:
            raise PineSignalError("INVALID_CLOSE_ENTRY")

        command_action = "OPEN" if action == "OPEN" else "CLOSE_ALL"
        command_id = f"PINE:{signal_id}:{command_action}"
        try:
            score = int(float(payload.get("score") or 0))
        except (TypeError, ValueError):
            score = 0

        now = time.time()
        open_ttl = max(30, int(os.getenv("ALPHA_PINE_OPEN_TTL_SECONDS", "300")))
        close_ttl = max(300, int(os.getenv("ALPHA_PINE_CLOSE_TTL_SECONDS", "3600")))
        received_at = _iso_now()
        command = {
            "command_id": command_id,
            "action": command_action,
            "source": "PINE",
            "command_owner": "PINE_TRADINGVIEW",
            "symbol": symbol,
            "signal_id": signal_id,
            "direction": direction,
            "entry": entry,
            "exit_price": _number(payload.get("exit_price")),
            "sl": sl,
            "tp1": tp1,
            "tp_final": tp2,
            "score": score,
            "target_source": str(payload.get("target_source") or "UNKNOWN"),
            "reason": str(payload.get("reason") or "PINE_FINAL_SIGNAL"),
            "ticker_id": str(payload.get("ticker_id") or ""),
            "timeframe": str(payload.get("timeframe") or ""),
            "received_at": received_at,
            "expires_at_epoch": now + (open_ttl if action == "OPEN" else close_ttl),
        }
        if action == "CLOSE":
            after_ack = self._reverse_open_command(
                payload,
                symbol=symbol,
                closing_direction=direction,
                received_at=received_at,
                now=now,
            )
            if after_ack:
                command["after_ack"] = after_ack

        with self._lock:
            if command_id in self._acked:
                acknowledged = dict(self._acked[command_id])
                return {
                    "action": "HOLD",
                    "reason": "DUPLICATE_ACKED_SIGNAL",
                    "symbol": symbol,
                    "signal_id": signal_id,
                    "command_id": command_id,
                    "acknowledged": acknowledged,
                }

            existing = self._pending.get(symbol)
            if existing and existing.get("command_id") == command_id:
                return dict(existing)
            if existing and action == "OPEN":
                raise PineSignalError("PENDING_COMMAND_EXISTS")

            # A Pine CLOSE supersedes an unfilled OPEN for the same symbol.  The
            # EA treats CLOSE_ALL as close-if-present and no-ops when flat.
            self._pending[symbol] = command
            self._save()
            return dict(command)

    def pending_command(self, symbol: str) -> dict[str, Any]:
        public_symbol = _symbol(symbol)
        with self._lock:
            command = self._pending.get(public_symbol)
            if not command:
                return {
                    "action": "HOLD",
                    "reason": "NO_PENDING_PINE_COMMAND",
                    "symbol": public_symbol,
                }
            if time.time() > float(command.get("expires_at_epoch") or 0):
                del self._pending[public_symbol]
                self._save()
                return {
                    "action": "HOLD",
                    "reason": "PINE_COMMAND_EXPIRED",
                    "symbol": public_symbol,
                }
            return dict(command)

    def owns(self, command_id: str) -> bool:
        return str(command_id or "").startswith("PINE:")

    def acknowledge(
        self,
        *,
        symbol: str,
        command_id: str,
        success: bool,
    ) -> dict[str, Any]:
        public_symbol = _symbol(symbol)
        with self._lock:
            if command_id in self._acked:
                return dict(self._acked[command_id])
            command = self._pending.get(public_symbol)
            if not command:
                raise PineSignalError("NO_PENDING_PINE_COMMAND")
            if command_id != command.get("command_id"):
                raise PineSignalError("PINE_COMMAND_ID_MISMATCH")
            if not success:
                result = dict(command)
                result["retry"] = True
                return result

            result = dict(command)
            result["acknowledged_at"] = _iso_now()
            result["acknowledged"] = True
            self._acked[command_id] = result
            del self._pending[public_symbol]
            after_ack = command.get("after_ack")
            if isinstance(after_ack, Mapping):
                promoted = dict(after_ack)
                promoted_id = str(promoted.get("command_id") or "")
                if promoted_id and promoted_id not in self._acked:
                    self._pending[public_symbol] = promoted
                    result["promoted_command"] = promoted
                    result["promoted_command_id"] = promoted_id
                    self._acked[command_id] = result
            while len(self._acked) > 100:
                self._acked.pop(next(iter(self._acked)))
            self._save()
            return result


def build_pine_api_payload(command: Mapping[str, Any]) -> dict[str, Any]:
    """Expose the existing canonical schema while keeping EA execution-only."""
    action = str(command.get("action") or "HOLD").upper()
    direction = str(command.get("direction") or "NONE").upper()
    entry = _number(command.get("entry"))
    sl = _number(command.get("sl"))
    tp1 = _number(command.get("tp1"))
    tp_final = _number(command.get("tp_final"))

    levels_ready = action == "OPEN" and all((entry, sl, tp1, tp_final))
    if direction == "BUY":
        directional_levels_ok = levels_ready and sl < entry < tp1 <= tp_final
        risk_points = entry - sl
        reward_points = tp_final - entry
    elif direction == "SELL":
        directional_levels_ok = levels_ready and tp_final <= tp1 < entry < sl
        risk_points = sl - entry
        reward_points = entry - tp_final
    else:
        directional_levels_ok = False
        risk_points = 0.0
        reward_points = 0.0

    rr = reward_points / risk_points if directional_levels_ok and risk_points > 0 else 0.0
    try:
        min_rr = max(0.0, float(os.getenv("TRADE_MIN_RR", "1.5")))
    except (TypeError, ValueError):
        min_rr = 1.5
    rr_ok = directional_levels_ok and rr >= min_rr

    contract = create_signal(
        status=SIGNAL,
        direction=direction,
        entry_price=entry,
        sl_price=sl,
        tp1_price=tp1,
        tp2_price=tp_final,
        score=command.get("score", 0),
        reason=command.get("reason", "PINE_FINAL_SIGNAL"),
    )
    ea = {
        "command_id": command.get("command_id"),
        "signal_id": command.get("signal_id"),
        "symbol": command.get("symbol"),
        "action": action,
        "execution_state": "READY",
        "direction": direction,
        "entry": entry,
        "exit_price": command.get("exit_price"),
        "sl": sl,
        "tp1": tp1,
        "tp_final": tp_final,
        "score": command.get("score", 0),
        "target_source": command.get("target_source"),
        "reason": command.get("reason"),
        # Pine owns the completed decision stack.  The bridge already validates
        # all four directional prices before an OPEN can reach this adapter.
        "levels_ready": bool(levels_ready),
        "directional_levels_ok": bool(directional_levels_ok),
        "risk_points": risk_points,
        "reward_points": reward_points,
        "rr": rr,
        "min_rr": min_rr,
        "rr_ok": bool(rr_ok),
        "setup_ok": action == "OPEN",
        "zone_ok": action == "OPEN",
        "vsa_gate_ok": action == "OPEN",
        "command_owner": "PINE_TRADINGVIEW",
        "ea_role": "EXECUTION_ONLY",
        "ea_execute_only": True,
    }
    return {
        **contract,
        "symbol": command.get("symbol"),
        "source": "PINE",
        "signal": {
            **contract,
            "symbol": command.get("symbol"),
            "source": "PINE",
            "target_source": command.get("target_source"),
        },
        "ea": ea,
    }


pine_signal_bridge = PineSignalBridge(
    os.getenv("ALPHA_PINE_BRIDGE_STATE_FILE", "/tmp/alpha_buffalo_pine_bridge.json")
)
