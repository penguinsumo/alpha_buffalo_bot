from abc import abstractmethod
from core.contracts.base_engine import BaseEngine
from core.models.confluence import ConfluenceScore
from core.models.market_context import MarketContext
from core.models.decision import Decision

class DecisionContract(BaseEngine):
    """
    Typed Inheritance Layer สำหรับ Decision Engine
    Python จะบังคับว่า run() ต้องรับ ConfluenceScore และ MarketContext และคืน Decision เท่านั้น
    """
    @abstractmethod
    def run(self, confluence: ConfluenceScore, context: MarketContext) -> Decision:
        pass
