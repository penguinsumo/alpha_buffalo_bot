from pydantic import BaseModel, Field
from typing import Optional, Literal
from .newday_market_map import NewdayMarketMap

class MarketContext(BaseModel):
    symbol: str
    timeframe: str

    bid: float
    ask: float
    open: float
    high: float
    low: float
    close: float
    volume: float

    spread: Optional[float] = None
    timestamp: int

    session_state: Optional[str] = None
    regime: Optional[Literal["TREND", "RANGE", "VOLATILE"]] = None
    volatility_score: Optional[float] = Field(default=None, ge=0, le=100)

    bias: Optional[str] = None
    current_regime: Optional[str] = None

    market_map: Optional[NewdayMarketMap] = None
