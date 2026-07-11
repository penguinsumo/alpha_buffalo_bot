from abc import ABC, abstractmethod
from core.models.confluence import ConfluenceScore
from core.models.market_context import MarketContext
from core.models.decision import Decision

class DecisionEngine(ABC):
    @abstractmethod
    def run(self, confluence: ConfluenceScore, context: MarketContext) -> Decision:
        pass
