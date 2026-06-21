"""
Liquidity Engine — Liquidity Sweep Detector with DailyMarketMap awareness
"""
from core.contracts.detector_contract import DetectorContract
from core.models.market_context import MarketContext
from core.models.detector_output import DetectorOutput

class LiquidityEngine(DetectorContract):
    def validate(self, context: MarketContext) -> bool:
        return all([
            context.high is not None,
            context.low is not None,
            context.close is not None
        ])

    def run(self, context: MarketContext) -> DetectorOutput:
        # 1. Base Liquidity Sweep Logic
        range_size = context.high - context.low
        sweep_detected = False
        direction = "NEUTRAL"
        strength = 40

        if context.close > context.high - (range_size * 0.25):
            direction = "BUY"
            strength = 80
            sweep_detected = True
        elif context.close < context.low + (range_size * 0.25):
            direction = "SELL"
            strength = 80
            sweep_detected = True

        # 2. NewdayMarketMap Enhancement
        market_map = context.market_map
        proximity_bonus = 0
        if market_map and sweep_detected:
            for zone in market_map.liquidity_zones:
                if abs(context.close - zone.price) < (range_size * 0.5):
                    # Price is near a pre-identified liquidity zone
                    if zone.zone_type == "BUY_SIDE" and direction == "BUY":
                        proximity_bonus += zone.strength * 10
                    elif zone.zone_type == "SELL_SIDE" and direction == "SELL":
                        proximity_bonus += zone.strength * 10
            strength = min(100, strength + proximity_bonus)

        return DetectorOutput(
            detector_type="LIQUIDITY",
            signal_type="buy_sweep" if direction == "BUY" else "sell_sweep" if direction == "SELL" else "no_sweep",
            direction=direction,
            strength=strength,
            confidence=0.75 if sweep_detected else 0.5,
            is_valid=True,
            metadata={"proximity_bonus": proximity_bonus}
        )
