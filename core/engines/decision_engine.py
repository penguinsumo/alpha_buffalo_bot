"""
Decision Engine — Final Brain Output (ConfluenceToDecisionContract)
"""
from core.contracts.pipeline_contract import ConfluenceToDecisionContract
from core.models.confluence import ConfluenceScore
from core.models.decision import Decision
from core.models.market_context import MarketContext

class DecisionEngine(ConfluenceToDecisionContract):
    def run(self, confluence: ConfluenceScore) -> Decision:
        if confluence.confluence_score < 40:
            return Decision(
                action="HOLD", entry_price=None, stop_loss=None,
                take_profit=None, position_size=0, confidence=0.0,
                reasoning="Low confluence score"
            )

        direction = confluence.direction_bias
        if direction == "BUY":
            action = "BUY"
            sl = 2290.0  # ตัวอย่าง — ในระบบจริงจะมาจาก MarketContext
            tp = 2320.0
        elif direction == "SELL":
            action = "SELL"
            sl = 2320.0
            tp = 2290.0
        else:
            return Decision(
                action="HOLD", entry_price=None, stop_loss=None,
                take_profit=None, position_size=0, confidence=0.0,
                reasoning="Neutral bias"
            )

        return Decision(
            action=action, entry_price=2315.0,
            stop_loss=sl, take_profit=tp,
            position_size=1.0,
            confidence=confluence.confluence_score / 100,
            reasoning="Confluence-based decision"
        )
