from pydantic import BaseModel, Field
from typing import Optional, Literal

class ExecutionPacket(BaseModel):
    action: Literal["BUY", "SELL", "HOLD"]
    symbol: str
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    position_size: float = Field(default=0.0, ge=0.0)
    is_valid: bool = True
    reasoning: str = ""

class ExecutionResult(BaseModel):
    order_id: str
    status: Literal["FILLED", "REJECTED", "PENDING", "FAILED"]
    filled_price: Optional[float] = None
    execution_latency_ms: Optional[int] = None
    message: str = ""
    raw_response: Optional[dict] = None
