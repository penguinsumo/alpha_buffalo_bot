from pydantic import BaseModel, Field
from typing import Optional, Literal

class ExecutionPacket(BaseModel):
    symbol: str
    order_type: Literal["MARKET", "LIMIT", "STOP"]
    side: Literal["BUY", "SELL"]
    volume: float = Field(gt=0)
    price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    status: Optional[Literal["PENDING", "FILLED", "REJECTED"]] = None
    broker_response: Optional[str] = None
