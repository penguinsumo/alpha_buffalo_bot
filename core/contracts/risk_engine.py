from abc import ABC, abstractmethod
from core.models.decision import Decision
from core.models.market_context import MarketContext
from core.models.risk_adjusted_decision import RiskAdjustedDecision

class RiskEngine(ABC):
    @abstractmethod
    def run(self, decision: Decision, context: MarketContext) -> RiskAdjustedDecision:
        pass
