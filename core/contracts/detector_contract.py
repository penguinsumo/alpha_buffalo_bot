from abc import abstractmethod
from core.contracts.base_engine import BaseEngine
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class DetectorContract(BaseEngine):
    """
    Typed Inheritance Layer สำหรับ Detector ทุกตัว
    Python จะบังคับว่า run() ต้องรับ MarketContext และคืน DetectorOutput เท่านั้น
    """
    @abstractmethod
    def validate(self, context: MarketContext) -> bool:
        pass

    @abstractmethod
    def run(self, context: MarketContext) -> DetectorOutput:
        pass
