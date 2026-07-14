#!/usr/bin/env python3
"""Hard directional bias for PRZ V4/V5 entries.

The harmonic detector's direction is the reversal direction at D.  It is not
the direction of the final C->D approach leg.  Once price is armed at, or is
inside, the harmonic PRZ, fresh entries are permitted only in that reversal
direction.  Entry confirmation (sweep/reclaim, BB/VSA and HA) remains the job
of the BUY/SELL engines; this gate never creates a trade by itself.
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
    """Return the one direction that may arm a fresh V4/V5 entry.

    CLOSE/EXIT commands must never call this function.  A confirmed XABC in
    FORMING state may license only its tunnel-aligned C->D approach.  ARMED or
    ACTIVE at the projected D/PRZ switches authority to the reversal direction.
    """
    requested = str(direction or "").upper()
    if requested not in {"BUY", "SELL"}:
        return HarmonicBiasResult(
            False, "NONE", "INVALID_DIRECTION", "INVALID_ENTRY_DIRECTION"
        )

    if context is None:
        if require_harmonic:
            return HarmonicBiasResult(
                False, "NONE", "MISSING", "HARMONIC_BIAS_UNAVAILABLE"
            )
        return HarmonicBiasResult(
            True, "BOTH", "OPTIONAL", "HARMONIC_BIAS_OPTIONAL"
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
    execution_authority = bool(_value(context, "execution_authority", True))
    tunnel_broken = bool(_value(context, "tunnel_broken", False))

    if not found or harmonic_direction not in {"BUY", "SELL"}:
        if require_harmonic:
            return HarmonicBiasResult(
                False,
                "NONE",
                state,
                "HARMONIC_BIAS_UNAVAILABLE",
                pattern,
                source,
                alignment,
                harmonic_direction,
                approach_direction,
                "MISSING",
            )
        return HarmonicBiasResult(
            True,
            "BOTH",
            state,
            "HARMONIC_BIAS_OPTIONAL",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "OPTIONAL",
        )

    if not execution_authority:
        return HarmonicBiasResult(
            False,
            "NONE",
            state,
            "HARMONIC_CANDIDATE_CONTEXT_ONLY",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "CONTEXT_ONLY",
        )

    if tunnel_broken:
        return HarmonicBiasResult(
            False,
            "NONE",
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
            False,
            "NONE",
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
        if approach_direction not in {"BUY", "SELL"}:
            return HarmonicBiasResult(
                False,
                "NONE",
                state,
                "HARMONIC_APPROACH_DIRECTION_UNAVAILABLE",
                pattern,
                source,
                alignment,
                harmonic_direction,
                approach_direction,
                "C_TO_D",
            )
        if alignment != "C_TO_D_APPROACH_ALIGNED":
            return HarmonicBiasResult(
                False,
                approach_direction,
                state,
                "WAIT_PARALLEL_TUNNEL_ALIGNMENT",
                pattern,
                source,
                alignment,
                harmonic_direction,
                approach_direction,
                "C_TO_D",
            )
        if requested != approach_direction:
            return HarmonicBiasResult(
                False,
                approach_direction,
                state,
                f"HARMONIC_FORMING_{approach_direction}_ONLY",
                pattern,
                source,
                alignment,
                harmonic_direction,
                approach_direction,
                "C_TO_D",
            )
        return HarmonicBiasResult(
            True,
            approach_direction,
            state,
            f"HARMONIC_C_TO_D_{approach_direction}_ALLOWED",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "C_TO_D",
        )

    if state not in ACTIVE_HARMONIC_STATES:
        return HarmonicBiasResult(
            False,
            harmonic_direction,
            state,
            "WAIT_HARMONIC_D_PRZ",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "WAIT_D",
        )

    if requested != harmonic_direction:
        return HarmonicBiasResult(
            False,
            harmonic_direction,
            state,
            f"HARMONIC_BIAS_{harmonic_direction}_ONLY",
            pattern,
            source,
            alignment,
            harmonic_direction,
            approach_direction,
            "D_REVERSAL",
        )

    return HarmonicBiasResult(
        True,
        harmonic_direction,
        state,
        f"HARMONIC_{harmonic_direction}_D_PRZ_ALLOWED",
        pattern,
        source,
        alignment,
        harmonic_direction,
        approach_direction,
        "D_REVERSAL",
    )
