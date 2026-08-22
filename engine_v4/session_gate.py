#!/usr/bin/env python3
"""
SessionGate — ใช้ SessionClock จริง + Time Gate (BUY >= 15 UTC)
+ Daily DD / Consecutive Loss Check

BUY off-hours policy
---------------------
Historically this gate hard-blocked every BUY outside the NY session before
15:00 UTC. That is a time-of-day risk policy, not a trend/EMA/BOS entry
gate, so it is safe to soften under PROJECT_CONTRACT.md's Integration
Contract (see ALPHA_FUSION_CONTRACT.md Red Lines -- this file is not one of
the listed forbidden blockers).

Softening is opt-in via ALPHA_BUY_SOFT_SESSION_GATE so existing production
behavior (hard block) is unchanged until someone deliberately turns it on
and re-runs the regression suite / observes live results, per the
Integration Contract's "prove current v12 behavior still passes" step.
When enabled, an off-hours BUY is allowed through with a reduced
risk_adjustment (graduated sizing) instead of being vetoed outright -- the
same pattern clean v5's vsa_gate.py already used for Asia-session VSA
(0.5x position_multiplier instead of a hard block).

NOTE: risk_adjustment is not yet consumed by the EA payload contract
(EA_EXECUTION_CONTRACT.md's command schema has no lot/risk field today).
Until that schema is extended and the MT5 EA is updated to read it, this
value is diagnostic-only -- visible in the API response, not yet capable
of actually scaling a live position's size.
"""
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from session_clock import SessionClock, SessionState

BUY_SOFT_SESSION_GATE = os.getenv("ALPHA_BUY_SOFT_SESSION_GATE", "false").strip().lower() == "true"
BUY_OFFHOURS_RISK_MULTIPLIER = float(os.getenv("ALPHA_BUY_OFFHOURS_RISK_MULTIPLIER", "0.5"))


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
            is_offhours = session_state.session != 'NY' or utc_hour < 15
            if is_offhours:
                if not BUY_SOFT_SESSION_GATE:
                    if session_state.session != 'NY':
                        return GateResult(False, "BUY allowed only in NY session")
                    return GateResult(False, f"BUY before 15 UTC (now {utc_hour})")
                return GateResult(
                    True,
                    f"BUY off-hours ({session_state.session} {utc_hour}h UTC) - reduced risk",
                    risk_adjustment=BUY_OFFHOURS_RISK_MULTIPLIER,
                )

        return GateResult(True, "Gate passed")
