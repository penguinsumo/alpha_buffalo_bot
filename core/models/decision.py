from pydantic import BaseModel, Field
from typing import Optional, Literal

class Decision(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size: float = Field(default=0.0, ge=0.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reasoning: str = ""
