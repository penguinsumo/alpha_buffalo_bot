#!/usr/bin/env python3
"""
SessionGate — ใช้ SessionClock จริง + Time Gate (BUY >= 15 UTC)
+ Daily DD / Consecutive Loss Check
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from session_clock import SessionClock, SessionState

@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    risk_adjustment: float = 1.0

class SessionGate:
    def __init__(self, session_clock: SessionClock):
        self.clock = session_clock

    def evaluate(self, session_state: SessionState, direction: str,
                 daily_dd_ok: bool = True,
                 consec_loss_ok: bool = True) -> GateResult:
        """
        session_state: จาก SessionClock.get()
        """
        if session_state.session == 'CLOSED':
            return GateResult(False, "Market closed")
        if not daily_dd_ok:
            return GateResult(False, "Daily DD limit reached")
        if not consec_loss_ok:
            return GateResult(False, "Max consecutive losses reached")

        utc_hour = session_state.utc_hour

        if direction == 'BUY':
            if session_state.session != 'NY':
                return GateResult(False, "BUY allowed only in NY session")
            if utc_hour < 15:
                return GateResult(False, f"BUY before 15 UTC (now {utc_hour})")

        return GateResult(True, "Gate passed")
