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
    approach_direction: str = "NONE"
    timeframe: str = "NONE"
    source: str = "NONE"
    state: str = "NONE"
    x_point: float = 0.0
    a_point: float = 0.0
    b_point: float = 0.0
    c_point: float = 0.0
    d_point: float = 0.0
    x_idx: int = -1
    a_idx: int = -1
    b_idx: int = -1
    c_idx: int = -1
    d_idx: int = -1
    ratios: Dict[str, float] = Field(default_factory=dict)
    prz_low: float = 0.0
    prz_high: float = 0.0
    tp1: float = 0.0
    tp2: float = 0.0
    tp3: float = 0.0
    invalidation: float = 0.0
    priority: int = 5
    reliability: str = "UNKNOWN"
    projection_mode: str = "COMPLETED_XABCD"
    execution_authority: bool = True
    selected_pattern: str = ""
    candidate_patterns: List[Dict] = Field(default_factory=list)
    current_xad: float = 0.0
    current_bcd: float = 0.0
    next_xad: float = 0.0
    ratio_model: str = "NONE"
    confirmation_required: List[str] = Field(default_factory=list)
    stop_reference: str = "NONE"
    morph_state: str = "BASE_PROJECTION"
    morph_from: List[str] = Field(default_factory=list)
    morph_to: str = ""
    morph_reason: str = "NONE"
    statistics_status: str = "INSUFFICIENT_SAMPLE"
    statistics_sample_size: int = 0
    statistics_source: str = "NONE"


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
