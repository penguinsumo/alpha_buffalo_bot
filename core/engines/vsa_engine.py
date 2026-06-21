from core.contracts.detector_engine import DetectorEngine
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput


class VSAEngine(DetectorEngine):

    def validate(self, context: MarketContext) -> bool:
        return context.volume is not None and context.volume > 0

    def run(self, context: MarketContext) -> DetectorOutput:

        if context.close > context.open and context.volume > 1000:
            return DetectorOutput(
                detector_type="VSA",
                signal_type="buy_strength",
                direction="BUY",
                strength=75,
                confidence=0.7,
                is_valid=True
            )

        if context.close < context.open and context.volume > 1000:
            return DetectorOutput(
                detector_type="VSA",
                signal_type="sell_pressure",
                direction="SELL",
                strength=75,
                confidence=0.7,
                is_valid=True
            )

        return DetectorOutput(
            detector_type="VSA",
            signal_type="neutral",
            direction="NEUTRAL",
            strength=30,
            confidence=0.4,
            is_valid=True
        )
