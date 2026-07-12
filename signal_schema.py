"""Canonical BUY/SELL signal contract shared by engines, router, API, and EA.

``status`` describes whether a signal may be acted on. ``direction`` describes
the market side only.  The two fields must never be used interchangeably.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


SIGNAL = "SIGNAL"
NO_SIGNAL = "NO_SIGNAL"
BLOCKED = "BLOCKED"
ERROR = "ERROR"

VALID_STATUSES = {SIGNAL, NO_SIGNAL, BLOCKED, ERROR}
VALID_DIRECTIONS = {"BUY", "SELL"}


def _number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def validate_directional_prices(
    direction: str | None,
    entry_price: Any,
    sl_price: Any,
    tp1_price: Any,
    tp2_price: Any,
) -> tuple[bool, str]:
    """Validate all execution levels using the selected market direction."""
    side = str(direction or "").upper()
    if side not in VALID_DIRECTIONS:
        return False, "INVALID_DIRECTION"

    entry = _number(entry_price)
    sl = _number(sl_price)
    tp1 = _number(tp1_price)
    tp2 = _number(tp2_price)
    if None in (entry, sl, tp1, tp2):
        return False, "MISSING_PRICE_LEVELS"

    if side == "BUY" and not (sl < entry < tp1 <= tp2):
        return False, "INVALID_BUY_LEVELS"
    if side == "SELL" and not (tp2 <= tp1 < entry < sl):
        return False, "INVALID_SELL_LEVELS"
    return True, "OK"


def create_signal(
    *,
    status: str,
    direction: str | None = None,
    entry_price: Any = None,
    sl_price: Any = None,
    tp1_price: Any = None,
    tp2_price: Any = None,
    score: Any = 0,
    reason: str = "",
) -> dict[str, Any]:
    """Build a canonical response and downgrade invalid SIGNAL levels to BLOCKED."""
    normalized_status = str(status or ERROR).upper()
    if normalized_status not in VALID_STATUSES:
        normalized_status = ERROR
        reason = "INVALID_STATUS" if not reason else f"{reason}|INVALID_STATUS"

    normalized_direction = str(direction or "").upper()
    if normalized_direction not in VALID_DIRECTIONS:
        normalized_direction = None

    entry = _number(entry_price)
    sl = _number(sl_price)
    tp1 = _number(tp1_price)
    tp2 = _number(tp2_price)

    if normalized_status == SIGNAL:
        prices_ok, validation_reason = validate_directional_prices(
            normalized_direction, entry, sl, tp1, tp2
        )
        if not prices_ok:
            normalized_status = BLOCKED
            reason = "|".join(part for part in (reason, validation_reason) if part)

    if normalized_status in {NO_SIGNAL, ERROR}:
        normalized_direction = None
        entry = sl = tp1 = tp2 = None

    try:
        normalized_score = int(float(score or 0))
    except (TypeError, ValueError):
        normalized_score = 0

    return {
        "status": normalized_status,
        "direction": normalized_direction,
        "entry_price": entry,
        "sl_price": sl,
        "tp1_price": tp1,
        "tp2_price": tp2,
        "score": normalized_score,
        "reason": str(reason or ""),
    }


def normalize_engine_candidate(candidate: Mapping[str, Any] | None) -> dict[str, Any]:
    """Normalize one BUY/SELL engine candidate without dropping diagnostics."""
    if not candidate:
        return create_signal(status=NO_SIGNAL, reason="No engine conditions met")

    direction = str(candidate.get("direction") or "").upper()
    tp2 = candidate.get("tp2_price", candidate.get("tp", candidate.get("tp_final")))
    tp1 = candidate.get("tp1_price", candidate.get("tp1"))
    if tp1 is None:
        tp1 = candidate.get("signal_tp")
    if tp1 is None:
        tp1 = tp2

    canonical = create_signal(
        status=candidate.get("status", SIGNAL),
        direction=direction,
        entry_price=candidate.get("entry_price", candidate.get("entry")),
        sl_price=candidate.get("sl_price", candidate.get("sl")),
        tp1_price=tp1,
        tp2_price=tp2,
        score=candidate.get("score", candidate.get("v5_quality_score", 0)),
        reason=candidate.get("reason", candidate.get("entry_mode", "Engine candidate")),
    )
    merged = dict(candidate)
    merged.update(canonical)
    return merged
