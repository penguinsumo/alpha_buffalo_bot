from pydantic import BaseModel
from typing import Literal
from .decision import Decision

class RiskAdjustedDecision(BaseModel):
    original_decision: Decision
    adjusted_position_size: float
    max_risk_allowed: float
    risk_status: Literal["APPROVED", "REDUCED", "REJECTED"]
    reason: str
