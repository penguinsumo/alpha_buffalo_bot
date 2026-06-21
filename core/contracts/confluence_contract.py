from abc import abstractmethod
from typing import List
from core.contracts.base_engine import BaseEngine
from core.models.detector_output import DetectorOutput
from core.models.confluence import ConfluenceScore

class ConfluenceContract(BaseEngine):
    @abstractmethod
    def run(self, detectors: List[DetectorOutput]) -> ConfluenceScore:
        pass
