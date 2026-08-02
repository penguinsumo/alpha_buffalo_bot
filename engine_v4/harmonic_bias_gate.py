#!/usr/bin/env python3
"""Harmonic/Newday context for post-BOS targets.

Harmonic never owns or blocks a fresh V4 entry. PRZ location plus a confirmed
price-action trigger owns entry. After aligned BOS/CHoCH promotes the existing
position to V5, the harmonic D projection may select TP2 when it overlaps the
next opposite PRZ.

The historic function name is retained for API compatibility. ``allowed`` is
therefore always true for a valid BUY/SELL request; the remaining fields are
diagnostic target context only.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping


ACTIVE_HARMONIC_STATES = frozenset({"ARMED", "ACTIVE"})
INVALID_HARMONIC_STATES = frozenset({"INVALID", "INVALIDATED", "EXPIRED"})
FORMING_HARMONIC_STATES = frozenset({"FORMING"})


def _value(context: Any, name: str, default: Any = None) -> Any:
    if isinstance(context, Mapping):
        return context.get(name, default)
    return getattr(context, name, default)


@dataclass(frozen=True)
class HarmonicBiasResult:
    allowed: bool
    allowed_direction: str
    state: str
    reason: str
    pattern: str = ""
    source: str = "NONE"
    tunnel_alignment: str = "UNKNOWN"
    reversal_direction: str = "NONE"
    approach_direction: str = "NONE"
    phase: str = "NONE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def tunnel_harmonic_alignment(direction: str, tunnel_state: str) -> str:
    """Describe the parallel-tunnel phase without changing harmonic bias.

    At D, a BUY harmonic normally arrives through a falling tunnel and a SELL
    harmonic through a rising tunnel.  After confirmation the tunnel may flip
    into the reversal direction.  Both are valid phases; FLAT is transition.
    """
    direction = str(direction or "").upper()
    tunnel_state = str(tunnel_state or "NONE").upper()
    if tunnel_state in {"NONE", "UNKNOWN", ""}:
        return "UNKNOWN"
    if tunnel_state == "FLAT":
        return "D_TRANSITION"
    if (direction == "BUY" and tunnel_state == "DOWNTREND") or (
        direction == "SELL" and tunnel_state == "UPTREND"
    ):
        return "C_TO_D_APPROACH_ALIGNED"
    if (direction == "BUY" and tunnel_state == "UPTREND") or (
        direction == "SELL" and tunnel_state == "DOWNTREND"
    ):
        return "D_REVERSAL_CONFIRMED"
    return "UNKNOWN"


def evaluate_harmonic_bias(
    direction: str,
    context: Any = None,
    *,
    require_harmonic: bool = True,
) -> HarmonicBiasResult:
    """Return non-blocking harmonic context for compatibility.

    ``require_harmonic`` is intentionally ignored. A stale production or
    research flag must never turn a missing/opposite pattern into an entry
    veto again.
    """
    requested = str(direction or "").upper()
    if requested not in {"BUY", "SELL"}:
        return HarmonicBiasResult(
            False, "NONE", "INVALID_DIRECTION", "INVALID_ENTRY_DIRECTION"
        )

    if context is None:
        return HarmonicBiasResult(
            True, "BOTH", "MISSING", "HARMONIC_TARGET_CONTEXT_UNAVAILABLE"
        )

    found = bool(
        _value(context, "found", _value(context, "harmonic_is_real", False))
    )
    harmonic_direction = str(
        _value(
            context,
            "direction",
            _value(context, "harmonic_direction", "NONE"),
        )
        or "NONE"
    ).upper()
    state = str(
        _value(context, "state", _value(context, "harmonic_state", "NONE"))
        or "NONE"
    ).upper()
    pattern = str(
        _value(context, "pattern", _value(context, "harmonic_pattern", ""))
        or ""
    )
    source = str(
        _value(context, "source", _value(context, "harmonic_source", "NONE"))
        or "NONE"
    )
    tunnel_state = str(_value(context, "tunnel_state", "NONE") or "NONE")
    alignment = tunnel_harmonic_alignment(harmonic_direction, tunnel_state)
    approach_direction = str(
        _value(
            context,
            "approach_direction",
            "SELL" if harmonic_direction == "BUY" else "BUY" if harmonic_direction == "SELL" else "NONE",
        )
        or "NONE"
    ).upper()
    tunnel_broken = bool(_value(context, "tunnel_broken", False))

    if not found or harmonic_direction not in {"BUY", "SELL"}:
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "HARMONIC_TARGET_CONTEXT_OPTIONAL",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "OPTIONAL",
        )

    if tunnel_broken:
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "HARMONIC_TUNNEL_BROKEN",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "INVALIDATED",
        )

    if state in INVALID_HARMONIC_STATES:
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "HARMONIC_PATTERN_INVALIDATED",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "INVALIDATED",
        )

    if state in FORMING_HARMONIC_STATES:
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "HARMONIC_FORMING_TARGET_CONTEXT",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "C_TO_D",
        )

    if state not in ACTIVE_HARMONIC_STATES:
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "WAIT_HARMONIC_TARGET_PRZ",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "WAIT_D",
        )

    return HarmonicBiasResult(
        True,
        "BOTH",
        state,
        "HARMONIC_D_AVAILABLE_FOR_POST_BOS_TARGET",
        pattern,
        source,
        alignment,
        harmonic_direction,
        approach_direction,
        "D_REVERSAL",
    )
