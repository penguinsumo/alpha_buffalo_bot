from pydantic import BaseModel, Field
from typing import List, Optional
from .detector_output import DetectorOutput

class ConfluenceScore(BaseModel):
    direction_bias: str
    confluence_score: float = Field(ge=0, le=100)
    aligned_detectors: List[DetectorOutput] = Field(default_factory=list)
    conflicting_detectors: List[DetectorOutput] = Field(default_factory=list)
    entry_zone: Optional[float] = None
    invalidation_zone: Optional[float] = None
