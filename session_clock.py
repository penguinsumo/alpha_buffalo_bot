from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any


# =========================================================
# TIMEBASE
# =========================================================

BKK = timezone(timedelta(hours=7))


# =========================================================
# SESSION PROVIDER (READ ONLY LAYER)
# =========================================================

class SessionProvider:

    def get(self, dt: Optional[datetime] = None) -> Dict[str, Any]:

        if dt is None:
            dt = datetime.now(BKK)
        elif dt.tzinfo is None:
            dt = dt.replace(tzinfo=BKK)
        else:
            dt = dt.astimezone(BKK)

        hour = dt.hour
        t = dt.time()

        # session boundaries (fixed truth)
        if 2 <= hour < 5:
            return {"session": "CLOSED", "hour": hour}

        if 5 <= hour < 14:
            return {"session": "ASIA", "hour": hour}

        if 14 <= hour < 19:
            return {"session": "LONDON", "hour": hour}

        if hour >= 19 or hour < 2:
            liquidity = "OVERLAP" if 19 <= hour <= 23 else "NORMAL"
            return {
                "session": "NY",
                "hour": hour,
                "liquidity": liquidity
            }

        return {"session": "CLOSED", "hour": hour}


# =========================================================
# ADAPTIVE THRESHOLD ENGINE (EXTERNAL LOGIC PLUG)
# =========================================================

class AdaptiveThresholdEngine:

    def calculate(self, session: str, liquidity: str, volatility: str, hour_state: str) -> int:

        base_map = {
            "ASIA": 60,
            "LONDON": 70,
            "NY": 70,
            "CLOSED": 999
        }

        base = base_map.get(session, 70)

        liquidity_adj = 5 if liquidity == "OVERLAP" else 0

        volatility_adj = {
            "HIGH": 5,
            "MID": 0,
            "LOW": -3
        }.get(volatility, 0)

        hour_adj = {
            "HIGH": -2,
            "NORMAL": 0,
            "LOW": 4
        }.get(hour_state, 0)

        raw = base + liquidity_adj + volatility_adj + hour_adj

        return max(55, min(80, raw))


# =========================================================
# SUPPORT ENGINES
# =========================================================

class VolatilityEngine:
    def classify(self, v: float) -> str:
        if v >= 1.5:
            return "HIGH"
        if v >= 0.8:
            return "MID"
        return "LOW"


class HourEngine:
    def classify(self, h: int) -> str:
        if h in {7, 8, 9, 13, 14, 15, 18}:
            return "HIGH"
        if h in {0, 1, 2, 3, 22, 23}:
            return "LOW"
        return "NORMAL"


class SweepEngine:
    def detect(self, fibo: float, session: str) -> str:
        if session == "NY" and fibo >= 0.786:
            return "LIQUIDITY_SWEEP"
        if session == "ASIA" and fibo <= 0.618:
            return "EARLY_SWEEP"
        return "NONE"


class RiskEngine:
    def calc(self, volatility: str, sweep: str) -> float:
        r = 1.0

        if volatility == "HIGH":
            r *= 0.6
        elif volatility == "MID":
            r *= 0.8

        if sweep != "NONE":
            r *= 0.7

        return round(r, 3)


# =========================================================
# CONTEXT MODEL
# =========================================================

@dataclass(frozen=True)
class MarketContext:
    session: str
    liquidity: str
    volatility: str
    hour_state: str
    sweep: str
    hour: int


@dataclass(frozen=True)
class Decision:
    allowed: bool
    confidence: float
    threshold: int
    reason: str


# =========================================================
# DECISION ENGINE (CORE BRAIN)
# =========================================================

class DecisionEngine:

    def evaluate(
        self,
        ctx: MarketContext,
        fibo: float,
        score: float,
        risk_multiplier: float,
        threshold: int
    ) -> Decision:

        confidence = score * 100

        # session pressure
        if ctx.session == "NY" and ctx.liquidity == "OVERLAP":
            confidence += 5

        # volatility penalty
        if ctx.volatility == "HIGH":
            confidence -= 10

        # sweep boost
        if ctx.sweep != "NONE":
            confidence += 7

        # risk scaling
        confidence *= risk_multiplier

        confidence = max(0, min(100, confidence))

        allowed = confidence >= threshold

        reason = f"{ctx.session}|{ctx.liquidity}|{ctx.volatility}|{ctx.sweep}"

        return Decision(
            allowed=allowed,
            confidence=round(confidence, 2),
            threshold=threshold,
            reason=reason
        )


# =========================================================
# UNIFIED ENGINE V12 (FINAL ORCHESTRATOR)
# =========================================================

class UnifiedEngineV12:

    def __init__(self):
        self.session_provider = SessionProvider()
        self.vol = VolatilityEngine()
        self.hour = HourEngine()
        self.sweep = SweepEngine()
        self.risk = RiskEngine()
        self.threshold = AdaptiveThresholdEngine()
        self.decision = DecisionEngine()

    def run(
        self,
        fibo: float,
        score: float,
        volatility_score: float,
        dt: Optional[datetime] = None
    ) -> Decision:

        s = self.session_provider.get(dt)

        session = s["session"]
        hour = s["hour"]
        liquidity = s.get("liquidity", "NORMAL")

        volatility = self.vol.classify(volatility_score)
        hour_state = self.hour.classify(hour)
        sweep = self.sweep.detect(fibo, session)

        risk = self.risk.calc(volatility, sweep)

        threshold = self.threshold.calculate(
            session=session,
            liquidity=liquidity,
            volatility=volatility,
            hour_state=hour_state
        )

        ctx = MarketContext(
            session=session,
            liquidity=liquidity,
            volatility=volatility,
            hour_state=hour_state,
            sweep=sweep,
            hour=hour
        )

        return self.decision.evaluate(
            ctx=ctx,
            fibo=fibo,
            score=score,
            risk_multiplier=risk,
            threshold=threshold
        )


# =========================================================
# SINGLETON EXPORT
# =========================================================

engine = UnifiedEngineV12()
