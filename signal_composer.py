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
    """
    Backward-compatible wrapper for legacy tests.

    Supported:
    - v12: compose_signal(blueprint=..., decision=..., symbol=...)
    - legacy: compose_signal(df_4h, df_1h, df_15m_or_row15)
    """

    from datetime import datetime, timezone
    import pandas as pd

    from decision_engine import DecisionEngine
    from scenario_blueprint import ScenarioBlueprint
    from scenario_scanner import ScenarioScanner

    # v12 direct style
    if "blueprint" in kwargs and "decision" in kwargs:
        return composer.compose(
            blueprint=kwargs["blueprint"],
            decision=kwargs["decision"],
            symbol=kwargs.get("symbol"),
        )

    # v12 positional style
    if len(args) >= 2 and isinstance(args[0], ScenarioBlueprint):
        return composer.compose(
            blueprint=args[0],
            decision=args[1],
            symbol=kwargs.get("symbol"),
        )

    # legacy style: compose_signal(df4h, df1h, df15_or_row15)
    if len(args) >= 3:
        df_4h, df_1h, df_15m = args[:3]

        if isinstance(df_15m, pd.Series):
            df_15m = pd.DataFrame([df_15m])

        symbol = kwargs.get("symbol", "XAUUSD")

        try:
            if isinstance(df_15m, pd.DataFrame) and len(df_15m) >= 50:
                blueprint = ScenarioScanner().scan(df_4h, df_1h, df_15m, symbol=symbol)
            else:
                row = df_15m.iloc[-1] if isinstance(df_15m, pd.DataFrame) else df_15m
                current_price = float(row.get("close", 0.0))

                blueprint = ScenarioBlueprint(
                    timestamp=datetime.now(timezone.utc).isoformat(),
                    symbol=symbol,
                    current_price=current_price,
                    trend_h4="NEUTRAL",
                    trend_h1="NEUTRAL",
                    market_mode="SIDEWAYS",
                    is_valid=current_price > 0,
                    validation_errors=[] if current_price > 0 else ["INVALID_PRICE"],
                )

            decision = DecisionEngine().evaluate(blueprint)
            return composer.compose(
                blueprint=blueprint,
                decision=decision,
                symbol=symbol,
            )

        except Exception as exc:
            blueprint = ScenarioBlueprint(
                timestamp=datetime.now(timezone.utc).isoformat(),
                symbol=symbol,
                is_valid=False,
                validation_errors=[f"COMPOSE_SIGNAL_COMPAT_ERROR:{type(exc).__name__}"],
            )
            decision = DecisionEngine().evaluate(blueprint)
            return composer.compose(
                blueprint=blueprint,
                decision=decision,
                symbol=symbol,
            )

    raise TypeError("compose_signal requires either v12 arguments or legacy df4h, df1h, df15 arguments")

