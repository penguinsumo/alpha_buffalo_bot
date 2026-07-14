#!/usr/bin/env python3
"""
Final Production Gate — HA-Filtered Buy (ASIA/LONDON Profit Hours) + Baseline (NY >=15)
"""
from session_clock import SessionState
from engine_v4.harmonic_bias_gate import evaluate_harmonic_bias
from engine_v4.session_gate import GateResult

class FinalGate:
    ASIA_BUY_HOURS_BKK = {5,6,7,8,12,13}
    LONDON_BUY_HOURS_BKK = {15,17}

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

        if direction == 'BUY':
            sess = session_state.session
            bkk_hour = session_state.bkk_hour
            utc_hour = session_state.utc_hour

            # Baseline: NY >=15 UTC
            if sess == 'NY' and utc_hour >= 15:
                return GateResult(True, "NY buy" + harmonic_reason)

            # ASIA profit hours (HA filter if data available)
            if sess == 'ASIA' and bkk_hour in self.ASIA_BUY_HOURS_BKK:
                if df is not None and idx is not None:
                    row = df.iloc[idx]
                    if not row.get('HA_Bullish', True):
                        return GateResult(False, "HA not bullish in ASIA")
                return GateResult(True, f"ASIA buy BKK {bkk_hour}" + harmonic_reason)

            # LONDON profit hours (HA filter)
            if sess == 'LONDON' and bkk_hour in self.LONDON_BUY_HOURS_BKK:
                if df is not None and idx is not None:
                    row = df.iloc[idx]
                    if not row.get('HA_Bullish', True):
                        return GateResult(False, "HA not bullish in LONDON")
                return GateResult(True, f"LONDON buy BKK {bkk_hour}" + harmonic_reason)

            return GateResult(False, "Buy not allowed")
        if direction == 'SELL':
            return GateResult(True, "Sell allowed" + harmonic_reason)
        return GateResult(False, "Unknown direction")
