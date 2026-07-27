"""Harmonic/Newday context normalization; guidance only, never an entry gate."""
from __future__ import annotations

from typing import Dict

from runtime_layers.common import _safe_float

def _harmonic_gate_context(blueprint) -> Dict:
    """Normalize ScenarioBlueprint into the one V4/V5 bias contract."""
    if blueprint is None:
        return {
            "found": False,
            "direction": "NONE",
            "state": "MISSING",
            "pattern": "",
            "source": "NONE",
            "tunnel_state": "NONE",
        }
    return {
        "found": bool(getattr(blueprint, "harmonic_is_real", False)),
        "direction": str(getattr(blueprint, "harmonic_direction", "NONE") or "NONE").upper(),
        "approach_direction": str(getattr(blueprint, "harmonic_approach_direction", "NONE") or "NONE").upper(),
        "state": str(getattr(blueprint, "harmonic_state", "NONE") or "NONE").upper(),
        "pattern": str(getattr(blueprint, "harmonic_pattern", "") or ""),
        "source": str(getattr(blueprint, "harmonic_source", "NONE") or "NONE"),
        "source_tf": str(getattr(blueprint, "harmonic_source_tf", "NONE") or "NONE"),
        "pattern_state": str(getattr(blueprint, "harmonic_pattern_state", "NONE") or "NONE"),
        "projection_mode": str(getattr(blueprint, "harmonic_projection_mode", "NONE") or "NONE"),
        "execution_authority": bool(getattr(blueprint, "harmonic_execution_authority", True)),
        "tunnel_broken": bool(getattr(blueprint, "harmonic_tunnel_broken", False)),
        "candidate_patterns": list(getattr(blueprint, "harmonic_candidate_patterns", []) or []),
        "current_xad": _safe_float(getattr(blueprint, "harmonic_current_xad", 0.0)),
        "current_bcd": _safe_float(getattr(blueprint, "harmonic_current_bcd", 0.0)),
        "next_xad": _safe_float(getattr(blueprint, "harmonic_next_xad", 0.0)),
        "d_point": _safe_float(getattr(blueprint, "harmonic_d_point", 0.0)),
        "prz_low": _safe_float(getattr(blueprint, "harmonic_prz_low", 0.0)),
        "prz_high": _safe_float(getattr(blueprint, "harmonic_prz_high", 0.0)),
        "current_price": _safe_float(getattr(blueprint, "current_price", 0.0)),
        "tunnel_state": str(getattr(blueprint, "tunnel_state", "NONE") or "NONE").upper(),
        "tunnel_valid": bool(getattr(blueprint, "tunnel_valid", False)),
        "bos_eligible": bool(getattr(blueprint, "harmonic_bos_eligible", False)),
        "bos_state": str(getattr(blueprint, "harmonic_bos_state", "WAIT_BOS") or "WAIT_BOS"),
        "bos_sources": list(getattr(blueprint, "harmonic_bos_sources", []) or []),
        "bos_direction": str(getattr(blueprint, "harmonic_bos_direction", "NONE") or "NONE"),
        "bos_primary_timeframe": str(
            getattr(blueprint, "harmonic_bos_primary_timeframe", "NONE") or "NONE"
        ),
    }
