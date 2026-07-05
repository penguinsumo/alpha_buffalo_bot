# =========================
# PATCH AUTO FIX - PHASE 1 UNBLOCK
# =========================

import re

# ---- 1) FIX ScenarioBlueprint frozen ----
with open("scenario_scanner.py", "r") as f:
    code = f.read()

code = re.sub(
    r"@dataclass\s*\(frozen=True\)",
    "@dataclass",
    code
)

# ---- 2) FIX golden fibo safety ----
code = code.replace(
    "if L4 and H4 and H4 > L4:",
    "if L4 is None or H4 is None:\n            return"
)

# ---- 3) FIX PRZ safety guard ----
code = code.replace(
    "from harmonic_detector import recalculate_prz_after_bos",
    "from harmonic_detector import recalculate_prz_after_bos\n\n        if bp.swing_L is None or bp.swing_H is None or bp.swing_HL is None:\n            return"
)

with open("scenario_scanner.py", "w") as f:
    f.write(code)

print("PATCH COMPLETE: Phase 1 Unblocked")
