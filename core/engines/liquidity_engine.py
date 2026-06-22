from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class LiquidityEngine(DetectorContract):
    def validate(self, context: MarketContext) -> bool:
        return context.high > context.low
    def run(self, context: MarketContext) -> DetectorOutput:
        rng = context.high - context.low
        close = context.close
        detected = close > context.high - rng*0.25 or close < context.low + rng*0.25
        return DetectorOutput(
            detector_type="LIQUIDITY",
            signal_type="sweep" if detected else "no_sweep",
            direction="NEUTRAL",
            strength=80 if detected else 40,
            confidence=0.75 if detected else 0.5,
            is_valid=True,
            metadata={"liquidity_detected": detected}
        )
