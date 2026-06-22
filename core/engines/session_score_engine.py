from core.models.market_context import MarketContext

class SessionScoreEngine:
    THRESHOLDS = {"ASIA": 3.0, "LONDON": 5.0, "NY": 5.0}
    def evaluate(self, base_score: float, context: MarketContext) -> dict:
        session = (context.session_state or "LONDON").upper()
        threshold = self.THRESHOLDS.get(session, 5.0)
        effective_score = base_score * (5.0/threshold)
        entry_allowed = effective_score >= threshold
        return {
            "session": session,
            "base_score": base_score,
            "effective_score": effective_score,
            "threshold": threshold,
            "entry_allowed": entry_allowed
        }
