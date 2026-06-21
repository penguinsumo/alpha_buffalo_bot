from abc import ABC, abstractmethod
from typing import List
from core.models.detector_output import DetectorOutput
from core.models.confluence import ConfluenceScore

class ConfluenceEngine(ABC):
    @abstractmethod
    def run(self, detectors: List[DetectorOutput]) -> ConfluenceScore:
        pass
