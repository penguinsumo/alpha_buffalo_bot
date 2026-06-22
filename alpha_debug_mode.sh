#!/bin/bash

echo "🧠 Alpha Buffalo DEBUG MODE ENABLED (FULL TRACE)"

cd ~/alpha_buffalo_bot || exit 1

# ─────────────────────────────────────────────
# 1. Inject global debug tracer
# ─────────────────────────────────────────────
python3 - << 'PY'

from pathlib import Path

file = Path("signal_composer.py")
text = file.read_text()

# ── DEBUG WRAPPER ──
debug_layer = """
import traceback

def debug_stage(name):
    print(f\"\\n[DEBUG STAGE] {name}\")

def debug_log(label, value):
    print(f\"[DEBUG] {label}: {value}\")

def debug_error(e):
    print(\"[DEBUG ERROR]\")
    print(traceback.format_exc())
"""

if "debug_stage" not in text:
    text = debug_layer + "\n" + text

# ─────────────────────────────────────────────
# 2. Inject stage logging into compose
# ─────────────────────────────────────────────
text = text.replace(
    "def compose(self",
    "def compose(self"
)

text = text.replace(
    "session_info = get_market_session_info()",
    "debug_stage('SESSION')\nsession_info = get_market_session_info()"
)

text = text.replace(
    "kivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)",
    "debug_stage('KIVANC_ENGINE')\nkivanc_sig = run_kivanc(df_1h) or run_kivanc(df_4h)"
)

text = text.replace(
    "prz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)",
    "debug_stage('HARMONIC_ENGINE')\nprz_zones = run_harmonic(df_4h) + run_harmonic(df_1h)"
)

text = text.replace(
    "micro_sigs = run_micro(df_15m)",
    "debug_stage('MICRO_ENGINE')\nmicro_sigs = run_micro(df_15m)"
)

text = text.replace(
    "score_result = self.score_mgr.calculate(",
    "debug_stage('SCORE_ENGINE')\nscore_result = self.score_mgr.calculate("
)

text = text.replace(
    "best_dir = kivanc_sig.direction",
    "debug_stage('DIRECTION')\nbest_dir = kivanc_sig.direction"
)

text = text.replace(
    "return SignalDecision(",
    "debug_stage('DECISION_BUILD')\nreturn SignalDecision("
)

file.write_text(text)

print("✅ DEBUG MODE PATCHED")

PY

# ─────────────────────────────────────────────
# 3. Restart service
# ─────────────────────────────────────────────
pm2 restart AlphaBuffalo --update-env

sleep 3

# ─────────────────────────────────────────────
# 4. Live test
# ─────────────────────────────────────────────
echo "🔍 HEALTH CHECK"
curl -s http://localhost:8000/health
echo ""

echo "🔍 SIGNAL TEST"
curl -s "http://localhost:8000/signal/latest?key=DEMO123"
echo ""

echo "🚀 DEBUG MODE ACTIVE"
