"""
ScenarioBlueprint Contract Guard
FREEZE v5.4 STRUCTURE
"""

REQUIRED_FIELDS = {
    "trend_h4",
    "bos_triggered",
    "current_price",
    "golden_zone_low",
    "golden_zone_high",
    "base_score",
    "decision_bias"
}


def validate_blueprint(bp):
    missing = []

    for f in REQUIRED_FIELDS:
        if not hasattr(bp, f):
            missing.append(f)

    if missing:
        return False, missing

    return True, []
