#!/bin/bash

set -e

echo "🧠 Alpha Buffalo Hotfix v11.3 (Crash Guard Patch)"

cd ~/alpha_buffalo_bot || exit 1

# ─────────────────────────────────────────────
# 1. Patch signal_composer safety guards
# ─────────────────────────────────────────────
cat << 'PY' > signal_composer_patch.py
# AUTO PATCH (safety layer injection)

def safe_float(x, default=0.0):
    return default if x is None else float(x)

def safe_iter(x):
    return x if x is not None else []

def safe_score(obj):
    if obj is None:
        return 0.0
    if hasattr(obj, "total"):
        return obj.total
    if isinstance(obj, dict):
        return obj.get("total", 0.0)
    if isinstance(obj, (int, float)):
        return float(obj)
    return 0.0
PY

echo "✅ Safety helpers created"

# ─────────────────────────────────────────────
# 2. Patch score_manager fallback wrapper
# ─────────────────────────────────────────────
python3 - << 'PY'
from pathlib import Path

file_path = Path("signal_composer.py")
text = file_path.read_text()

# patch micro_sigs safety
text = text.replace(
    "micro_sigs = run_micro(df_15m)",
    "micro_sigs = run_micro(df_15m) or []"
)

# patch bos_detected safety
text = text.replace(
    "any(s.bos for s in micro_sigs)",
    "any(getattr(s, 'bos', False) for s in (micro_sigs or []))"
)

# patch score access safety
text = text.replace(
    "score_result.total",
    "getattr(score_result, 'total', 0)"
)

file_path.write_text(text)

print("✅ signal_composer patched safely")
PY

# ─────────────────────────────────────────────
# 3. Restart PM2 safely
# ─────────────────────────────────────────────
pm2 restart AlphaBuffalo --update-env

sleep 3

# ─────────────────────────────────────────────
# 4. Health check
# ─────────────────────────────────────────────
echo "🔍 Testing API..."
curl -s "http://localhost:8000/health"
echo ""
curl -s "http://localhost:8000/signal/latest?key=DEMO123"
echo ""

echo "🚀 Hotfix complete"
