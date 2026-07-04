from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any
import time

from session_clock import SessionClock
from scenario_blueprint import ScenarioBlueprint
from edge_logger import EdgeLogger, TradeEvidence


class SignalComposer:
    def __init__(self):
        self._clock = SessionClock()
        self._edge_logger = EdgeLogger()

    def compose(self, bp: ScenarioBlueprint) -> Dict[str, Any]:
        session_ctx = self._clock.get()

        if not bp or not bp.current_price:
            return self._empty("INVALID_BLUEPRINT")

        session = session_ctx.get("session", "UNKNOWN")

        gates = {
            "session": session,
            "bos_triggered": bp.bos_triggered,
            "tunnel_valid": bp.tunnel_valid,
            "has_golden_zone": bp.golden_zone_high > 0,
        }

        payload = bp.to_dict()

        try:
            self._edge_logger.log_trade(TradeEvidence(
                timestamp=time.time(),
                pattern=bp.harmonic_pattern or "Unknown",
                direction="BUY" if bp.trend_h4 == "UP" else "SELL",
                bos=bp.bos_triggered,
                vsa_ok=False,
                atr_value=bp.atr_15m,
                entry_price=0.0,
                exit_price=0.0,
                sl=0.0,
                tp=0.0,
                pnl=0.0,
                r_multiple=0.0,
                confidence=0.0,
                regime=bp.market_mode
            ))
        except Exception:
            pass

        return {
            "type": "COMPOSED_SIGNAL",
            "timestamp": session_ctx.get("timestamp"),
            "symbol": bp.symbol,
            "gates": gates,
            "blueprint": payload
        }

    def _empty(self, reason: str) -> Dict[str, Any]:
        return {"type": "COMPOSED_SIGNAL", "valid": False, "reason": reason}


composer = SignalComposer()
