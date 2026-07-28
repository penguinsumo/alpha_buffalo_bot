#!/usr/bin/env python3
"""Final production permission gate.

Entry confirmation belongs to the V4 setup:

    PRZ layers >= 2 + evidence >= 3
        -> closed M15 HA flip OR pinbar break OR closed M5 sniper reclaim

This gate therefore owns only market/risk permission and an optional research
harmonic restriction.  It must not repeat an HA check or restrict BUY to a
small list of historical "profit hours" after a valid closed-bar trigger has
already fired.
"""
from session_clock import SessionState
from engine_v4.harmonic_bias_gate import evaluate_harmonic_bias
from engine_v4.session_gate import GateResult

class FinalGate:
    def __init__(self, clock):
        self.clock = clock

    def evaluate(self, session_state: SessionState, direction: str,
                 df=None, idx=None,
                 daily_dd_ok=True, consec_loss_ok=True,
                 harmonic_context=None,
                 require_harmonic=False) -> GateResult:
        direction = str(direction or "").upper()
        if session_state.session == 'CLOSED':
            return GateResult(False, "Market closed")
        if not daily_dd_ok:
            return GateResult(False, "Daily DD limit reached")
        if not consec_loss_ok:
            return GateResult(False, "Max consecutive losses reached")

        harmonic_gate = evaluate_harmonic_bias(
            direction,
            harmonic_context,
            require_harmonic=require_harmonic,
        )
        if not harmonic_gate.allowed:
            return GateResult(False, harmonic_gate.reason)

        harmonic_reason = (
            f"|{harmonic_gate.reason}"
            if harmonic_context is not None or require_harmonic
            else ""
        )

        if direction in {"BUY", "SELL"}:
            return GateResult(
                True,
                f"{session_state.session} {direction.lower()} allowed"
                + harmonic_reason,
            )
        return GateResult(False, "Unknown direction")
