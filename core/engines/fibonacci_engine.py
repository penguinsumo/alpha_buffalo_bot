from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class FibonacciEngine(DetectorContract):
    def validate(self, context: MarketContext) -> bool:
        return (context.swing_high is not None and context.swing_low is not None and context.swing_high > context.swing_low)
    def run(self, context: MarketContext) -> DetectorOutput:
        move = context.swing_high - context.swing_low
        fib_382 = context.swing_high - move*0.382
        fib_500 = context.swing_high - move*0.500
        fib_618 = context.swing_high - move*0.618
        fib_786 = context.swing_high - move*0.786
        price = context.close
        distances = {
            "fib_382": abs(price-fib_382),
            "fib_500": abs(price-fib_500),
            "fib_618": abs(price-fib_618),
            "fib_786": abs(price-fib_786)
        }
        closest = min(distances, key=distances.get)
        closest_dist = distances[closest]
        normalized_dist = closest_dist / move if move>0 else 1
        strength = max(0, 100 - normalized_dist*1000)
        return DetectorOutput(
            detector_type="FIBONACCI",
            signal_type="fib_distance",
            direction="NEUTRAL",
            strength=strength, confidence=0.65 if strength>50 else 0.4,
            is_valid=True,
            metadata={"distances": distances, "closest_fib": closest, "closest_distance": closest_dist}
        )
