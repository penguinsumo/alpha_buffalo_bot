#!/bin/bash

echo "🧠 Alpha Buffalo Indentation Fix v11 (Auto Repair)"

FILE=~/alpha_buffalo_bot/signal_composer.py

# 1) Backup ก่อนเสมอ
cp "$FILE" "$FILE.bak.$(date +%s)"
echo "✅ Backup created"

# 2) Fix indentation เฉพาะบรรทัดที่พัง (kivanc_score)
python3 - << 'PY'
from pathlib import Path

path = Path.home() / "alpha_buffalo_bot/signal_composer.py"
lines = path.read_text().splitlines()

fixed = []
for i, line in enumerate(lines):
    stripped = line.lstrip()

    # แก้บรรทัดที่ error จาก log
    if stripped.startswith("kivanc_score = 1 if kivanc_sig else 0"):
        # บังคับ indent ให้เป็นระดับ function block (8 spaces)
        fixed.append(" " * 8 + stripped)
        continue

    # กัน indent เพี้ยนแบบ whitespace ล้วน
    if stripped == "":
        fixed.append("")
    else:
        fixed.append(line)

path.write_text("\n".join(fixed) + "\n")

print("✅ Indentation fixed safely")
PY

# 3) Restart PM2
pm2 restart AlphaBuffalo --update-env

sleep 3

# 4) Health check
echo "🔍 HEALTH CHECK"
curl -s "http://localhost:8000/health" || echo "❌ NO RESPONSE"

echo ""
echo "🔍 SIGNAL TEST"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "❌ NO RESPONSE"

echo ""
echo "🚀 FIX COMPLETE"
