from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List, Literal

class DetectorOutput(BaseModel):
    detector_type: Literal["VSA", "LIQUIDITY", "STRUCTURE", "FIBONACCI"]
    signal_type: str
    direction: Literal["BUY", "SELL", "NEUTRAL"]
    strength: float = Field(ge=0, le=100)
    confidence: float = Field(ge=0, le=1)
    is_valid: bool = True
    related_levels: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
