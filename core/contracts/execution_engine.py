from abc import ABC, abstractmethod
from core.models.risk_adjusted_decision import RiskAdjustedDecision
from core.models.execution import ExecutionPacket

class ExecutionEngine(ABC):
    @abstractmethod
    def run(self, risk_adjusted: RiskAdjustedDecision) -> ExecutionPacket:
        pass
