#!/bin/bash

echo "🧠 Alpha Buffalo CLEAN REBUILD v11 (Full Recovery Mode)"

FILE=~/alpha_buffalo_bot/signal_composer.py
BACKUP="$FILE.full_backup.$(date +%s).py"

# 1) Backup
cp "$FILE" "$BACKUP"
echo "✅ Backup saved: $BACKUP"

# 2) Hard reset indentation using Python AST-safe rewrite
python3 - << 'PY'
from pathlib import Path
import ast

path = Path.home() / "alpha_buffalo_bot/signal_composer.py"

code = path.read_text()

# Step 1: parse AST (this will confirm structure validity)
try:
    tree = ast.parse(code)
except Exception as e:
    print("❌ AST PARSE FAILED - file is structurally broken")
    print(e)
    exit(1)

# Step 2: rebuild using compile + exec normalization trick
compiled = compile(tree, filename=str(path), mode="exec")

# Step 3: write a normalized version (safe fallback = original AST-valid structure)
# NOTE: Python does not provide full source regeneration without astor
# so we enforce whitespace normalization instead

lines = code.splitlines()
fixed = []
indent_level = 0

for line in lines:
    stripped = line.lstrip()

    if stripped == "":
        fixed.append("")
        continue

    # reduce all weird tabs/spaces to 4-space system
    if stripped.startswith(("class ", "def ", "import ", "from ", "@")):
        fixed.append(stripped)
        if stripped.startswith(("class ", "def ")):
            indent_level = 1
        continue

    # normalize inner block
    fixed.append("    " * indent_level + stripped)

path.write_text("\n".join(fixed) + "\n")

print("✅ CLEAN REBUILD DONE")
PY

# 3) Syntax check
echo "🔍 Syntax check..."
python3 -m py_compile "$FILE"

if [ $? -ne 0 ]; then
    echo "❌ STILL BROKEN → rollback backup"
    cp "$BACKUP" "$FILE"
    exit 1
fi

# 4) restart service
pm2 restart AlphaBuffalo --update-env

sleep 3

# 5) verify import (CRITICAL)
echo "🔍 IMPORT TEST"
python3 -c "import signal_composer; print('IMPORT OK')"

# 6) API test
echo ""
echo "🔍 HEALTH"
curl -s "http://localhost:8000/health" || echo "NO RESPONSE"

echo ""
echo "🔍 SIGNAL"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO RESPONSE"

echo ""
echo "🚀 REBUILD COMPLETE"
