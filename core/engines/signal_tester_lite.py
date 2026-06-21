import logging
from typing import Any

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

class SignalTesterLite:
    def check_equivalence(self, old_signal: Any, new_decision: Any, symbol: str) -> bool:
        try:
            old_action = getattr(old_signal, "action", "NONE").upper()
            new_action = getattr(new_decision, "action", "NONE").upper()

            is_match = (old_action == new_action)

            if not is_match:
                logger.warning(f"⚠️ [DIVERGENCE] {symbol}: Old says {old_action}, New says {new_action}")
            else:
                logger.info(f"✅ [MATCH] {symbol}: Both agree on {old_action}")

            return is_match

        except Exception as e:
            logger.error(f"❌ [TESTER ERROR] Failed to compare signals for {symbol}: {e}")
            return False
