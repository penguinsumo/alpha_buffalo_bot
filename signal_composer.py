from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Dict, Any

from session_clock import SessionClock
from scenario_blueprint import ScenarioBlueprint


# =========================================================
# SIGNAL COMPOSER (PHASE 1 FINAL GATE LAYER)
# =========================================================
# RULES:
# - NO market analysis
# - NO interpretation
# - NO fallback logic
# - ONLY gate + pass-through
# =========================================================


class SignalComposer:

    def __init__(self):
        self._clock = SessionClock()

    # -----------------------------------------------------
    # MAIN ENTRY
    # -----------------------------------------------------
    def compose(self, bp: ScenarioBlueprint) -> Dict[str, Any]:

        session_ctx = self._clock.get()

        # === HARD GUARD: contract must be valid ===
        if not bp or not bp.current_price:
            return self._empty("INVALID_BLUEPRINT")

        # === SESSION GATE ONLY (NO INTERPRETATION) ===
        session = session_ctx.get("session", "UNKNOWN")

        # NOTE:
        # composer does NOT decide market condition
        # it only attaches runtime session metadata

        # === GATE FLAGS (PURE PASS THROUGH) ===
        gates = {
            "session": session,
            "bos_triggered": bp.bos_triggered,
            "tunnel_valid": bp.tunnel_valid,
            "has_golden_zone": bp.golden_zone_high > 0,
        }

        # === RAW BLUEPRINT PASS-THROUGH ===
        # no transformation, no inference
        payload = bp.to_dict()

        return {
            "type": "COMPOSED_SIGNAL",
            "timestamp": session_ctx.get("timestamp"),
            "symbol": bp.symbol,
            "gates": gates,
            "blueprint": payload
        }

    # -----------------------------------------------------
    # SAFE EMPTY OUTPUT
    # -----------------------------------------------------
    def _empty(self, reason: str) -> Dict[str, Any]:
        return {
            "type": "COMPOSED_SIGNAL",
            "valid": False,
            "reason": reason
        }


# =========================================================
# SINGLETON
# =========================================================
composer = SignalComposer()
