from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput


class FibonacciEngine(DetectorContract):

    def validate(self, context: MarketContext) -> bool:
        return context.high > context.low

    def run(self, context: MarketContext) -> DetectorOutput:

        # fib zone approximation (simple + deterministic)
        move = context.high - context.low
        fib_618 = context.high - (move * 0.618)
        fib_382 = context.high - (move * 0.382)

        if abs(context.close - fib_618) < move * 0.02:
            return DetectorOutput(
                detector_type="FIBONACCI",
                signal_type="fib_618_reaction",
                direction="BUY",
                strength=70,
                confidence=0.65,
                is_valid=True
            )

        if abs(context.close - fib_382) < move * 0.02:
            return DetectorOutput(
                detector_type="FIBONACCI",
                signal_type="fib_382_reaction",
                direction="SELL",
                strength=70,
                confidence=0.65,
                is_valid=True
            )

        return DetectorOutput(
            detector_type="FIBONACCI",
            signal_type="no_reaction",
            direction="NEUTRAL",
            strength=35,
            confidence=0.5,
            is_valid=True
        )
