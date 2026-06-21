from pydantic import BaseModel
from typing import List, Optional

class LiquidityZone(BaseModel):
    price: float
    zone_type: str
    strength: float

class NewdayMarketMap(BaseModel):
    symbol: str
    daily_bias: str

    asian_high: float
    asian_low: float

    previous_day_high: float
    previous_day_low: float

    projected_high: float
    projected_low: float

    liquidity_zones: List[LiquidityZone] = []

    narrative: str = ""
