"""
Structure Engine — Market Structure Detector (DetectorContract)
"""
from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput
from core.enums.direction import Direction
from core.enums.strength import Strength

class StructureEngine(DetectorContract):
    def __init__(self):
        self.name = "STRUCTURE_ENGINE"

    def validate(self, context: MarketContext) -> bool:
        return context.bias is not None

    def run(self, context: MarketContext) -> DetectorOutput:
        bias = (context.bias or "").upper()
        
        if bias == "UP":
            direction = Direction.BUY
            strength = Strength.HIGH
            confidence = 0.7
            signal_type = "bullish_structure"
        elif bias == "DOWN":
            direction = Direction.SELL
            strength = Strength.HIGH
            confidence = 0.7
            signal_type = "bearish_structure"
        else:
            direction = Direction.NEUTRAL
            strength = Strength.LOW
            confidence = 0.5
            signal_type = "range"

        return DetectorOutput(
            detector_type="STRUCTURE",
            signal_type=signal_type,
            direction=direction.value,
            strength=float(strength.value),
            confidence=confidence,
            metadata={"bias": bias}
        )
