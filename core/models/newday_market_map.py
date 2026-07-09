from pydantic import BaseModel, Field
from typing import Dict, List


class LiquidityZone(BaseModel):
    price: float
    zone_type: str
    strength: float


class HarmonicContext(BaseModel):
    found: bool = False
    pattern: str = ""
    direction: str = "NONE"
    timeframe: str = "NONE"
    source: str = "NONE"
    state: str = "NONE"
    d_point: float = 0.0
    prz_low: float = 0.0
    prz_high: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    invalidation: float = 0.0
    priority: int = 5
    reliability: str = "UNKNOWN"


class NewdayMarketMap(BaseModel):
    symbol: str
    map_date: str = ""
    generated_at: str = ""
    source: str = "market_close_map"
    daily_bias: str

    asian_high: float
    asian_low: float

    previous_day_high: float
    previous_day_low: float
    previous_day_close: float = 0.0

    projected_high: float
    projected_low: float

    h4_swing_high: float = 0.0
    h4_swing_low: float = 0.0
    daily_swing_high: float = 0.0
    daily_swing_low: float = 0.0

    lot0: Dict = Field(default_factory=dict)
    kivanc: Dict = Field(default_factory=dict)
    harmonic_context: HarmonicContext = Field(default_factory=HarmonicContext)

    liquidity_zones: List[LiquidityZone] = Field(default_factory=list)

    narrative: str = ""
