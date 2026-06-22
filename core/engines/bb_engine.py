from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class BbEngine(DetectorContract):
    def validate(self, context: MarketContext) -> bool: return True
    def run(self, context: MarketContext) -> DetectorOutput:
        return DetectorOutput(
            detector_type="STRUCTURE",
            signal_type="bb_touch",
            direction="NEUTRAL",
            strength=40, confidence=0.5, is_valid=True
        )
