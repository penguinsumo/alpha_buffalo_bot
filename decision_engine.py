from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from scenario_blueprint import ScenarioBlueprint


# =========================================================
# DECISION ENGINE v1 (SINGLE SOURCE OF TRUTH)
# =========================================================
# RULE:
# - NO market analysis
# - NO signal composition
# - ONLY decision from confluence
# =========================================================


@dataclass(frozen=True)
class Decision:
    action: str          # BUY / SELL / NONE
    confidence: float
    score: int
    reason: str


class DecisionEngine:

    def evaluate(self, bp: ScenarioBlueprint) -> Decision:

        if not bp or not bp.is_valid:
            return Decision(
                action="NONE",
                confidence=0.0,
                score=0,
                reason="INVALID_BLUEPRINT"
            )

        score = 0

        # =====================================================
        # 1. BASE SCORE (from scanner)
        # =====================================================
        score += getattr(bp, "base_score", 0)

        # =====================================================
        # 2. STRUCTURE CONFIRMATION
        # =====================================================
        if bp.bos_triggered:
            score += 2

        if bp.smc_confirmed:
            score += 2

        # =====================================================
        # 3. VSA CONFIRMATION
        # =====================================================
        if bp.vsa_confirmed:
            score += 2

        # =====================================================
        # 4. PRZ CONTEXT BONUS
        # =====================================================
        if bp.prz_support_top and bp.prz_support_bottom:
            score += 1

        # =====================================================
        # 5. DECISION BIAS WEIGHTING
        # =====================================================
        bias = getattr(bp, "decision_bias", "WEAK")

        if bias == "STRONG":
            score += 2
        elif bias == "MODERATE":
            score += 1

        # =====================================================
        # FINAL DECISION LOGIC (CLEAN THRESHOLD MODEL)
        # =====================================================
        if score >= 8:
            action = "BUY" if bp.trend_h4 == "UP" else "SELL"
            confidence = 0.85
        elif score >= 5:
            action = "BUY" if bp.trend_h4 == "UP" else "SELL"
            confidence = 0.65
        elif score >= 3:
            action = "NONE"
            confidence = 0.40
        else:
            action = "NONE"
            confidence = 0.20

        reason = f"score={score}|bias={bias}|trend={bp.trend_h4}"

        return Decision(
            action=action,
            confidence=round(confidence, 2),
            score=score,
            reason=reason
        )


# =========================================================
# SINGLETON
# =========================================================
engine = DecisionEngine()
