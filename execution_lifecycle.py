#!/usr/bin/env python3
"""Python-owned position lifecycle and idempotent EA management commands."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import threading
from time import time_ns
from typing import Any

import pandas as pd

from runtime_layers.hourly_stats import HourlyStats


BKK = timezone(timedelta(hours=7))


def _number(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: Any) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        parsed = datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def closed_ha5_evidence(
    df_5m: pd.DataFrame | None,
    since: str | None = None,
) -> dict[str, Any]:
    """Return the last two completed M5 Heikin-Ashi colours.

    Twelve Data can include an updating last candle, so the last row is always
    excluded.  An exit needs two completed opposite colours, never an open bar.
    """
    empty = {
        "available": False,
        "two_bullish": False,
        "two_bearish": False,
        "colors": [],
        "timestamps": [],
    }
    if df_5m is None or getattr(df_5m, "empty", True) or len(df_5m) < 4:
        return empty

    frame = df_5m.copy()
    if "datetime" in frame.columns:
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["datetime"]).set_index("datetime")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        return empty
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = frame.sort_index().iloc[:-1]
    if since:
        frame = frame[frame.index >= pd.Timestamp(_parse_time(since))]
    if len(frame) < 3:
        return empty

    for column in ("open", "high", "low", "close"):
        if column not in frame:
            return empty
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    if len(frame) < 3:
        return empty

    ha_close = (frame["open"] + frame["high"] + frame["low"] + frame["close"]) / 4.0
    ha_open = pd.Series(index=frame.index, dtype="float64")
    ha_open.iloc[0] = (frame["open"].iloc[0] + frame["close"].iloc[0]) / 2.0
    for index in range(1, len(frame)):
        ha_open.iloc[index] = (ha_open.iloc[index - 1] + ha_close.iloc[index - 1]) / 2.0
    bullish = ha_close > ha_open
    colors = ["GREEN" if bool(value) else "RED" for value in bullish.iloc[-2:]]
    timestamps = [timestamp.isoformat() for timestamp in frame.index[-2:]]
    return {
        "available": True,
        "two_bullish": colors == ["GREEN", "GREEN"],
        "two_bearish": colors == ["RED", "RED"],
        "colors": colors,
        "timestamps": timestamps,
    }


def _m5_extremes_since(
    df_5m: pd.DataFrame | None,
    opened_at: str,
    current_price: float,
) -> tuple[float, float]:
    """Include the live M5 bar for price-level hits, but never for HA colour."""
    low_seen = high_seen = current_price
    if df_5m is None or getattr(df_5m, "empty", True):
        return low_seen, high_seen
    frame = df_5m.copy()
    if "datetime" in frame.columns:
        frame["datetime"] = pd.to_datetime(frame["datetime"], utc=True, errors="coerce")
        frame = frame.dropna(subset=["datetime"]).set_index("datetime")
    elif not isinstance(frame.index, pd.DatetimeIndex):
        return low_seen, high_seen
    if frame.index.tz is None:
        frame.index = frame.index.tz_localize("UTC")
    else:
        frame.index = frame.index.tz_convert("UTC")
    frame = frame[frame.index >= pd.Timestamp(_parse_time(opened_at))]
    if frame.empty or "low" not in frame or "high" not in frame:
        return low_seen, high_seen
    lows = pd.to_numeric(frame["low"], errors="coerce").dropna()
    highs = pd.to_numeric(frame["high"], errors="coerce").dropna()
    if not lows.empty:
        low_seen = min(low_seen, float(lows.min()))
    if not highs.empty:
        high_seen = max(high_seen, float(highs.max()))
    return low_seen, high_seen


@dataclass
class PositionState:
    symbol: str
    signal_id: str
    ticket: str
    direction: str
    entry: float
    sl: float
    tp1: float
    tp2: float
    opened_at: str
    max_bars: int = 40
    remaining_pct: float = 100.0
    tp1_done: bool = False
    be_armed: bool = False
    be_armed_at: str = ""
    status: str = "OPEN"
    pending_command_id: str = ""
    pending_command: str = ""
    pending_reason: str = ""


class ExecutionLifecycleManager:
    def __init__(self, state_file: str | Path | None = None):
        self._lock = threading.RLock()
        self._positions: dict[str, PositionState] = {}
        self._risk: dict[str, dict[str, Any]] = {}
        self._hourly_stats: dict[str, HourlyStats] = {}
        self._state_file = Path(state_file) if state_file else None
        self._load()

    def _load(self) -> None:
        if not self._state_file or not self._state_file.is_file():
            return
        try:
            payload = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._positions = {
                symbol: PositionState(**state)
                for symbol, state in (payload.get("positions") or {}).items()
            }
            self._risk = dict(payload.get("risk") or {})
            self._hourly_stats = {
                symbol: HourlyStats.from_json(buckets)
                for symbol, buckets in (payload.get("hourly_stats") or {}).items()
            }
        except Exception:
            self._positions = {}
            self._risk = {}
            self._hourly_stats = {}

    def _save(self) -> None:
        if not self._state_file:
            return
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        temporary = self._state_file.with_suffix(self._state_file.suffix + ".tmp")
        payload = {
            "positions": {symbol: asdict(state) for symbol, state in self._positions.items()},
            "risk": self._risk,
            "hourly_stats": {
                symbol: stats.to_json() for symbol, stats in self._hourly_stats.items()
            },
            "updated_at": _iso_now(),
        }
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, self._state_file)

    def hourly_stats_summary(self, symbol: str, min_samples: int = 5) -> dict:
        """Diagnostic-only win-rate-by-UTC-hour snapshot. Never gates entry
        -- nothing in engine_v4 reads this. See runtime_layers/hourly_stats.py.
        """
        stats = self._hourly_stats.get(str(symbol))
        if not stats:
            return {}
        return stats.summary(min_samples=min_samples)

    def has_active(self, symbol: str) -> bool:
        with self._lock:
            return str(symbol) in self._positions

    def position(self, symbol: str) -> dict[str, Any] | None:
        with self._lock:
            state = self._positions.get(str(symbol))
            return asdict(state) if state else None

    def close_external(self, symbol: str, r_multiple: float = 0.0) -> dict[str, Any]:
        """Finalize state after an execution-only EA ACKs a Pine CLOSE_ALL."""
        with self._lock:
            state = self._positions.get(str(symbol))
            if not state:
                return {"status": "NO_ACTIVE_POSITION", "symbol": str(symbol)}
            self._record_close(str(symbol), _number(r_multiple))
            closed = asdict(state)
            closed["status"] = "CLOSED"
            closed["closed_at"] = _iso_now()
            closed["close_owner"] = "PINE_TRADINGVIEW"
            del self._positions[str(symbol)]
            self._save()
            return closed

    def register_fill(
        self,
        *,
        symbol: str,
        signal_id: str,
        ticket: str,
        direction: str,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        max_bars: int = 40,
        filled_at: str | None = None,
    ) -> dict[str, Any]:
        side = str(direction).upper()
        entry, sl, tp1, tp2 = map(_number, (entry, sl, tp1, tp2))
        valid = (
            side == "BUY" and sl < entry < tp1 <= tp2
        ) or (
            side == "SELL" and tp2 <= tp1 < entry < sl
        )
        if not valid:
            raise ValueError("INVALID_POSITION_LEVELS")
        with self._lock:
            existing = self._positions.get(symbol)
            if existing and existing.signal_id != signal_id:
                raise ValueError("POSITION_ALREADY_ACTIVE")
            if existing:
                # A fill acknowledgement can be retried by the EA.  Never reset
                # TP1/BE/pending-command state for the same live position.
                if str(existing.ticket) != str(ticket):
                    raise ValueError("POSITION_TICKET_MISMATCH")
                return asdict(existing)
            state = PositionState(
                symbol=symbol,
                signal_id=signal_id,
                ticket=str(ticket),
                direction=side,
                entry=entry,
                sl=sl,
                tp1=tp1,
                tp2=tp2,
                opened_at=(filled_at or _iso_now()),
                max_bars=max(1, int(max_bars)),
            )
            self._positions[symbol] = state
            self._save()
            return asdict(state)

    def risk_permissions(self, symbol: str) -> dict[str, bool]:
        with self._lock:
            today = datetime.now(BKK).date().isoformat()
            state = self._risk.get(symbol) or {}
            if state.get("day") != today:
                state = {"day": today, "loss_r": 0.0, "consecutive_losses": 0}
                self._risk[symbol] = state
            max_daily_loss_r = float(os.getenv("ALPHA_MAX_DAILY_LOSS_R", "3.0"))
            max_consecutive = int(os.getenv("ALPHA_MAX_CONSECUTIVE_LOSSES", "5"))
            return {
                "daily_dd_ok": float(state.get("loss_r", 0.0)) < max_daily_loss_r,
                "consec_loss_ok": int(state.get("consecutive_losses", 0)) < max_consecutive,
            }

    def _record_close(self, symbol: str, r_multiple: float) -> None:
        today = datetime.now(BKK).date().isoformat()
        risk = self._risk.get(symbol) or {"day": today, "loss_r": 0.0, "consecutive_losses": 0}
        if risk.get("day") != today:
            risk = {"day": today, "loss_r": 0.0, "consecutive_losses": 0}
        if r_multiple < 0:
            risk["loss_r"] = float(risk.get("loss_r", 0.0)) + abs(r_multiple)
            risk["consecutive_losses"] = int(risk.get("consecutive_losses", 0)) + 1
        elif r_multiple > 0:
            risk["consecutive_losses"] = 0
        self._risk[symbol] = risk

        # Adaptive hourly stats (ported from clean v5's trade_manager.py
        # HourlyStats): diagnostic-only, keyed by UTC hour of close.
        stats = self._hourly_stats.get(symbol) or HourlyStats()
        stats.record(datetime.now(timezone.utc).hour, r_multiple)
        self._hourly_stats[symbol] = stats

    def _command(self, state: PositionState, action: str, reason: str, **values: Any) -> dict[str, Any]:
        if state.pending_command_id:
            return self.pending_command(state.symbol)
        command_id = f"{state.signal_id}:{action}:{time_ns()}"
        state.pending_command_id = command_id
        state.pending_command = action
        state.pending_reason = reason
        self._save()
        return {
            "command_id": command_id,
            "action": action,
            "reason": reason,
            "symbol": state.symbol,
            "signal_id": state.signal_id,
            "ticket": state.ticket,
            "direction": state.direction,
            "remaining_pct": state.remaining_pct,
            **values,
        }

    def pending_command(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            state = self._positions.get(symbol)
            if not state or not state.pending_command_id:
                return {"action": "HOLD", "reason": "NO_PENDING_COMMAND", "symbol": symbol}
            values: dict[str, Any] = {}
            if state.pending_command == "PARTIAL_CLOSE_MOVE_BE":
                values = {
                    "close_pct": float(os.getenv("ALPHA_TP1_CLOSE_PCT", "50")),
                    "new_sl": state.entry,
                }
            return {
                "command_id": state.pending_command_id,
                "action": state.pending_command,
                "reason": state.pending_reason,
                "symbol": state.symbol,
                "signal_id": state.signal_id,
                "ticket": state.ticket,
                "direction": state.direction,
                "remaining_pct": state.remaining_pct,
                **values,
            }

    def evaluate(
        self,
        symbol: str,
        current_price: float,
        df_5m: pd.DataFrame | None = None,
        now: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._positions.get(symbol)
            if not state:
                return {"action": "HOLD", "reason": "NO_ACTIVE_POSITION", "symbol": symbol}
            if state.pending_command_id:
                return self.pending_command(symbol)

            price = _number(current_price)
            if price <= 0:
                return {"action": "HOLD", "reason": "INVALID_CURRENT_PRICE", "symbol": symbol}
            is_buy = state.direction == "BUY"
            low_seen, high_seen = _m5_extremes_since(df_5m, state.opened_at, price)
            sl_since = state.be_armed_at if state.be_armed and state.be_armed_at else state.opened_at
            sl_low_seen, sl_high_seen = _m5_extremes_since(df_5m, sl_since, price)
            if (is_buy and sl_low_seen <= state.sl) or (not is_buy and sl_high_seen >= state.sl):
                return self._command(state, "CLOSE_ALL", "HARD_SL", close_pct=state.remaining_pct)
            if (is_buy and high_seen >= state.tp2) or (not is_buy and low_seen <= state.tp2):
                return self._command(state, "CLOSE_ALL", "TP2_FINAL", close_pct=state.remaining_pct)
            tp1_hit = (is_buy and high_seen >= state.tp1) or (not is_buy and low_seen <= state.tp1)
            if not state.tp1_done and tp1_hit:
                return self._command(
                    state,
                    "PARTIAL_CLOSE_MOVE_BE",
                    "TP1_REACHED",
                    close_pct=float(os.getenv("ALPHA_TP1_CLOSE_PCT", "50")),
                    new_sl=state.entry,
                )

            opened = _parse_time(state.opened_at)
            current_time = _parse_time(now or _iso_now())
            if current_time >= opened + timedelta(minutes=15 * state.max_bars):
                return self._command(state, "CLOSE_ALL", "MAX_BARS_TIMEOUT", close_pct=state.remaining_pct)

            ha5 = closed_ha5_evidence(df_5m, state.be_armed_at if state.be_armed else None)
            opposite = (is_buy and ha5["two_bearish"]) or ((not is_buy) and ha5["two_bullish"])
            if state.be_armed and opposite:
                return self._command(
                    state,
                    "CLOSE_ALL",
                    "HA5_OPPOSITE_2_AFTER_BE",
                    close_pct=state.remaining_pct,
                    ha5=ha5,
                )
            return {
                "action": "HOLD",
                "reason": "MANAGING",
                "symbol": symbol,
                "signal_id": state.signal_id,
                "ticket": state.ticket,
                "direction": state.direction,
                "remaining_pct": state.remaining_pct,
                "tp1_done": state.tp1_done,
                "be_armed": state.be_armed,
                "ha5": ha5,
            }

    def acknowledge(
        self,
        *,
        symbol: str,
        command_id: str,
        success: bool,
        remaining_pct: float | None = None,
        r_multiple: float = 0.0,
        acknowledged_at: str | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            state = self._positions.get(symbol)
            if not state:
                raise ValueError("NO_ACTIVE_POSITION")
            if command_id != state.pending_command_id:
                raise ValueError("COMMAND_ID_MISMATCH")
            action = state.pending_command
            if not success:
                # Keep the same pending id so retries remain idempotent.
                return self.pending_command(symbol)
            if action == "PARTIAL_CLOSE_MOVE_BE":
                state.tp1_done = True
                state.be_armed = True
                state.be_armed_at = _parse_time(acknowledged_at or _iso_now()).isoformat()
                state.sl = state.entry
                remaining = _number(
                    remaining_pct,
                    100.0 - float(os.getenv("ALPHA_TP1_CLOSE_PCT", "50")),
                )
                state.remaining_pct = min(100.0, max(0.0, remaining))
                state.pending_command_id = ""
                state.pending_command = ""
                state.pending_reason = ""
                self._save()
                return asdict(state)
            if action == "CLOSE_ALL":
                self._record_close(symbol, _number(r_multiple))
                closed = asdict(state)
                closed["status"] = "CLOSED"
                closed["closed_at"] = _iso_now()
                del self._positions[symbol]
                self._save()
                return closed
            state.pending_command_id = ""
            state.pending_command = ""
            state.pending_reason = ""
            self._save()
            return asdict(state)


execution_lifecycle = ExecutionLifecycleManager(
    os.getenv("ALPHA_EXECUTION_STATE_FILE", "/tmp/alpha_buffalo_execution_state.json")
)
