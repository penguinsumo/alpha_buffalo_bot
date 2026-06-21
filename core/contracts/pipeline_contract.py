"""
Pipeline Contract — บังคับ Data Flow ระหว่าง Engine
DetectorOutput[] → ConfluenceScore → Decision → RiskAdjustedDecision → ExecutionPacket
"""
from abc import ABC, abstractmethod
from typing import List
from core.models.detector_output import DetectorOutput
from core.models.confluence import ConfluenceScore
from core.models.decision import Decision
from core.models.risk_adjusted_decision import RiskAdjustedDecision
from core.models.execution import ExecutionPacket


class DetectorToConfluenceContract(ABC):
    """Layer 1 → 2: Detectors → Confluence"""
    @abstractmethod
    def run(self, detectors: List[DetectorOutput]) -> ConfluenceScore:
        pass


class ConfluenceToDecisionContract(ABC):
    """Layer 2 → 3: Confluence → Decision"""
    @abstractmethod
    def run(self, confluence: ConfluenceScore) -> Decision:
        pass


class DecisionToRiskContract(ABC):
    """Layer 3 → 4: Decision → Risk"""
    @abstractmethod
    def run(self, decision: Decision) -> RiskAdjustedDecision:
        pass


class RiskToExecutionContract(ABC):
    """Layer 4 → 5: Risk → Execution"""
    @abstractmethod
    def run(self, risk_adjusted: RiskAdjustedDecision) -> ExecutionPacket:
        pass
