from __future__ import annotations

from typing import Any, Dict

from decision_engine import Decision
from scenario_blueprint import ScenarioBlueprint
from session_clock import SessionClock


class EdgeLogger:
    def log(self, payload: Dict[str, Any]) -> None:
        return None


class SignalComposer:
    def __init__(self, session_clock: SessionClock | None = None, edge_logger: EdgeLogger | None = None):
        self.session_clock = session_clock or SessionClock()
        self.edge_logger = edge_logger or EdgeLogger()

    def compose(self, blueprint: ScenarioBlueprint, decision: Decision, symbol: str | None = None) -> Dict[str, Any]:
        session_state = self.session_clock.get()

        blueprint.session = session_state.session

        payload = {
            "type": "COMPOSED_SIGNAL",
            "timestamp": blueprint.timestamp,
            "symbol": symbol or blueprint.symbol,
            "decision": decision.to_dict(),
            "gates": {
                "session": session_state.session,
                "liquidity": session_state.liquidity,
                "bkk_hour": session_state.bkk_hour,
                "utc_hour": session_state.utc_hour,
                "session_timestamp": session_state.timestamp,
                "blueprint_valid": blueprint.is_valid,
            },
            "blueprint": blueprint.to_dict(),
        }

        try:
            self.edge_logger.log(payload)
        except Exception:
            pass

        return payload


composer = SignalComposer()

def compose_signal(*args, **kwargs):
    return composer.compose(*args, **kwargs)
