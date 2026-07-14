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
        harmonic_bias = self._active_harmonic_direction(bp)

        if bp.decision_bias == "STRONG":
            score += 2
        elif bp.decision_bias == "MODERATE":
            score += 1

        if bp.watch_bias in ("BUY", "SELL"):
            score += 1

        if bp.delta_alignment in ("M15_H1_BUY", "M15_H1_SELL", "BULLISH", "BEARISH"):
            score += 1

        if bp.impulse_direction in ("BUY", "SELL"):
            score += 1

        if bp.m15_impulse:
            score += 1

        if bp.h1_impulse:
            score += 1

        if bp.trade_plan == "PRZ_REVERSAL_WATCH" and bp.reversal_allowed:
            score += 1

        if bp.trade_plan == "TUNNEL_WATCH" and bp.tunnel_retest_valid:
            score += 1

        if bp.trade_plan == "NO_TRADE":
            score -= 3

        if harmonic_bias in ("BUY", "SELL"):
            score += 1
            if bp.watch_bias in ("BUY", "SELL") and bp.watch_bias != harmonic_bias:
                # Trend/context may disagree at a reversal D. It must not
                # outvote the harmonic PRZ direction.
                score -= 1

        if bp.zone_invalidated:
            score -= 2

        if bp.micro_prz_broken and not bp.micro_prz_reclaimed:
            score -= 2

        score = max(0, min(10, score))

        preferred_action = self._resolve_watch_direction(bp)
        executable = self._is_executable(bp)

        if score >= 9 and preferred_action in ("BUY", "SELL") and executable:
            grade = "STRONG_TRADE"
            action = preferred_action
            confidence = 0.85
        elif score >= 7 and preferred_action in ("BUY", "SELL") and executable:
            grade = "VALID_TRADE"
            action = preferred_action
            confidence = 0.68
        elif score >= 5 and preferred_action in ("BUY", "SELL"):
            grade = f"WATCH_{preferred_action}"
            action = "NONE"
            confidence = 0.52
        elif score >= 4:
            grade = "WAIT"
            action = "NONE"
            confidence = 0.42
        else:
            grade = "NONE"
            action = "NONE"
            confidence = 0.20

        reason = (
            f"score={score}|base={bp.base_score}|bias={bp.decision_bias}"
            f"|trend_h4={bp.trend_h4}|trend_h1={bp.trend_h1}"
            f"|watch_bias={bp.watch_bias}|delta_align={bp.delta_alignment}"
            f"|impulse={bp.impulse_direction}|m15_imp={bp.m15_impulse}|h1_imp={bp.h1_impulse}"
            f"|plan={bp.trade_plan}|executable={executable}|prz={bp.prz_state}"
            f"|broken={bp.micro_prz_broken}|bos={bp.bos_triggered}"
            f"|smc={bp.smc_confirmed}|vsa={bp.vsa_confirmed}"
            f"|harmonic_bias={harmonic_bias}|harmonic_state={bp.harmonic_state}"
            f"|tunnel={bp.tunnel_state}"
        )

        return Decision(
            action=action,
            confidence=round(confidence, 2),
            score=score,
            reason=reason,
            grade=grade,
        )

    def _resolve_watch_direction(self, bp: ScenarioBlueprint) -> str:
        harmonic_bias = self._active_harmonic_direction(bp)
        if harmonic_bias in ("BUY", "SELL"):
            return harmonic_bias

        if bp.watch_bias in ("BUY", "SELL"):
            return bp.watch_bias

        if bp.impulse_direction in ("BUY", "SELL"):
            return bp.impulse_direction

        if bp.delta_alignment in ("M15_H1_BUY", "BULLISH"):
            return "BUY"

        if bp.delta_alignment in ("M15_H1_SELL", "BEARISH"):
            return "SELL"

        return "NONE"

    def _active_harmonic_direction(self, bp: ScenarioBlueprint) -> str:
        if not bp.harmonic_execution_authority or bp.harmonic_tunnel_broken:
            return "NONE"
        direction = str(bp.harmonic_direction or "NONE").upper()
        approach = str(bp.harmonic_approach_direction or "NONE").upper()
        state = str(bp.harmonic_state or "NONE").upper()
        if bp.harmonic_is_real and state in {"ARMED", "ACTIVE"} and direction in {"BUY", "SELL"}:
            return direction
        if bp.harmonic_is_real and state == "FORMING" and approach in {"BUY", "SELL"}:
            tunnel = str(bp.tunnel_state or "NONE").upper()
            aligned = (
                (direction == "BUY" and tunnel == "DOWNTREND")
                or (direction == "SELL" and tunnel == "UPTREND")
            )
            return approach if aligned else "NONE"
        return "NONE"

    def _is_executable(self, bp: ScenarioBlueprint) -> bool:
        trade_plan = str(bp.trade_plan or "").upper()

        if "WATCH" in trade_plan:
            return False

        if trade_plan in ("NO_TRADE", "NONE", ""):
            return False

        if bp.zone_invalidated:
            return False

        if bp.micro_prz_broken and not bp.micro_prz_reclaimed:
            return False

        has_trigger = (
            bp.bos_triggered
            or bp.vsa_confirmed
            or bp.watch_bias in ("BUY", "SELL")
            or bp.impulse_direction in ("BUY", "SELL")
        )

        has_ready_plan = (
            "READY" in trade_plan
            or "EXECUTE" in trade_plan
            or "BREAKOUT" in trade_plan
        )

        return bool(has_ready_plan and has_trigger)


engine = DecisionEngine()
