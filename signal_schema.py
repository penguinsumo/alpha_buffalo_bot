from dataclasses import dataclass
from typing import Optional

@dataclass
class SignalDecision:
    direction: str
    confidence: float
    score: float
    reason: Optional[str] = None
