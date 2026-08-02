#!/usr/bin/env python3
"""Final production permission gate.

Entry confirmation belongs to the V4 setup:

    PRZ layers >= 2 + evidence >= 3
        -> closed M15 HA flip OR pinbar break OR closed M5 sniper reclaim

    A confirmed H1 regular-candle green dot projected onto a closed M15 bar may
    use the two-layer demand-PRZ fast path without waiting for evidence >= 3.

This gate therefore owns only market/risk permission. It must not repeat an HA
check, restrict BUY to historical "profit hours", or use Harmonic as an entry
restriction after a valid closed-bar trigger has already fired.
"""
from session_clock import SessionState
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

        if direction in {"BUY", "SELL"}:
            return GateResult(
                True,
                f"{session_state.session} {direction.lower()} allowed",
            )
        return GateResult(False, "Unknown direction")
