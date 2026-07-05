from __future__ import annotations

from dataclasses import dataclass

from scenario_blueprint import ScenarioBlueprint


@dataclass(frozen=True)
class Decision:
    action: str
    confidence: float
    score: int
    reason: str
    grade: str = "NONE"

    def to_dict(self):
        return {
            "action": self.action,
            "confidence": self.confidence,
            "score": self.score,
            "reason": self.reason,
            "grade": self.grade,
        }


class DecisionEngine:
    def evaluate(self, bp: ScenarioBlueprint) -> Decision:
        if not bp or not bp.is_valid:
            return Decision(
                action="NONE",
                confidence=0.0,
                score=0,
                reason="INVALID_BLUEPRINT",
                grade="INVALID",
            )

        score = int(bp.base_score)

        if bp.decision_bias == "STRONG":
            score += 2
        elif bp.decision_bias == "MODERATE":
            score += 1

        if score >= 8:
            grade = "STRONG_TRADE"
            action = self._trend_to_action(bp.trend_h4)
            confidence = 0.85
        elif score >= 5:
            grade = "VALID_TRADE"
            action = self._trend_to_action(bp.trend_h4)
            confidence = 0.65
        elif score >= 3:
            grade = "WAIT"
            action = "NONE"
            confidence = 0.40
        else:
            grade = "NONE"
            action = "NONE"
            confidence = 0.20

        if action not in ("BUY", "SELL"):
            action = "NONE"

        reason = (
            f"score={score}|base={bp.base_score}|bias={bp.decision_bias}"
            f"|trend_h4={bp.trend_h4}|trend_h1={bp.trend_h1}"
            f"|bos={bp.bos_triggered}|smc={bp.smc_confirmed}|vsa={bp.vsa_confirmed}"
        )

        return Decision(
            action=action,
            confidence=round(confidence, 2),
            score=score,
            reason=reason,
            grade=grade,
        )

    def _trend_to_action(self, trend: str) -> str:
        if trend == "UP":
            return "BUY"
        if trend == "DOWN":
            return "SELL"
        return "NONE"


engine = DecisionEngine()
