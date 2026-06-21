from abc import ABC, abstractmethod
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class DetectorEngine(ABC):
    @abstractmethod
    def validate(self, context: MarketContext) -> bool:
        pass

    @abstractmethod
    def run(self, context: MarketContext) -> DetectorOutput:
        pass
