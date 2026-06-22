#!/bin/bash

set -e

echo "🛡️ Alpha Buffalo EMERGENCY SHIELD PATCH"

cd ~/alpha_buffalo_bot || exit 1

python3 - << 'PY'

from pathlib import Path

file = Path("signal_composer.py")
text = file.read_text()

# ─────────────────────────────
# 1. SAFE ENGINE WRAPPER
# ─────────────────────────────
wrapper = """
def safe_run(fn, default=None):
    try:
        return fn()
    except Exception as e:
        print('[ENGINE ERROR]', fn.__name__, e)
        return default
"""

if "safe_run" not in text:
    text = wrapper + "\n" + text

# ─────────────────────────────
# 2. WRAP ALL ENGINES
# ─────────────────────────────
text = text.replace(
    "run_kivanc(df_1h) or run_kivanc(df_4h)",
    "safe_run(lambda: run_kivanc(df_1h)) or safe_run(lambda: run_kivanc(df_4h))"
)

text = text.replace(
    "run_harmonic(df_4h) + run_harmonic(df_1h)",
    "safe_run(lambda: run_harmonic(df_4h), []) + safe_run(lambda: run_harmonic(df_1h), [])"
)

text = text.replace(
    "self.score_mgr.calculate(",
    "safe_run(lambda: self.score_mgr.calculate("
)

file.write_text(text)

print("✅ EMERGENCY SHIELD APPLIED")

PY

pm2 restart AlphaBuffalo --update-env

sleep 3

echo "🔍 TESTING..."
curl -s "http://localhost:8000/health"
echo ""
curl -s "http://localhost:8000/signal/latest?key=DEMO123"
echo ""

echo "🚀 SHIELD ACTIVE"
