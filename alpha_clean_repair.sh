#!/bin/bash

echo "🧼 Alpha Buffalo CLEAN RECOVERY (STRUCTURE FIX)"

cd ~/alpha_buffalo_bot || exit 1

# ─────────────────────────────
# 1. STOP PM2
# ─────────────────────────────
pm2 stop AlphaBuffalo || true
pm2 delete AlphaBuffalo || true

# ─────────────────────────────
# 2. FIX indentation automatically
# ─────────────────────────────
python3 - << 'PY'

from pathlib import Path

file = Path("signal_composer.py")
text = file.read_text()

# fix common indentation corruption
lines = text.split("\n")
fixed = []

for line in lines:
    # remove accidental double-indents caused by injection
    if "kivanc_score" in line:
        line = line.lstrip()  # force normalize
    fixed.append(line)

file.write_text("\n".join(fixed))

print("✅ indentation cleaned (basic level)")

PY

# ─────────────────────────────
# 3. Restart clean
# ─────────────────────────────
pm2 start alpha_buffalo_signal.py \
  --name AlphaBuffalo \
  --interpreter python3 \
  --restart-delay=3000

sleep 3

# ─────────────────────────────
# 4. Test
# ─────────────────────────────
echo "🔍 HEALTH"
curl -i http://localhost:8000/health || echo "NO SERVER"

echo ""
echo "🔍 SIGNAL"
curl -i "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO SIGNAL"

echo ""
echo "🚀 CLEAN RECOVERY DONE"
