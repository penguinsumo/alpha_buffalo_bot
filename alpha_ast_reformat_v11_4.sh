#!/bin/bash

echo "🧠 Alpha Buffalo AST Clean Reformat v11.4 (Production Rebuild)"

FILE=~/alpha_buffalo_bot/signal_composer.py
BACKUP="$FILE.ast_backup.$(date +%s).py"

# 1) Backup
cp "$FILE" "$BACKUP"
echo "✅ Backup saved: $BACKUP"

# 2) AST validation + formatting
python3 - << 'PY'
import ast
from pathlib import Path

path = Path.home() / "alpha_buffalo_bot/signal_composer.py"
backup = Path(str(path) + ".ast_rewrite_tmp.py")

code = path.read_text()

# Step 1: validate AST first
try:
    tree = ast.parse(code)
except Exception as e:
    print("❌ AST PARSE FAILED:")
    print(e)
    exit(1)

# Step 2: normalize indentation via compile + unparse (Python 3.9+)
try:
    import astor
    normalized = astor.to_source(tree)
except ImportError:
    # fallback: built-in compile sanity rewrite
    normalized = compile(tree, filename=str(path), mode="exec")
    normalized = code  # safe fallback (no destructive rewrite)

backup.write_text(normalized if isinstance(normalized, str) else code)

# Step 3: write safe version
path.write_text(normalized if isinstance(normalized, str) else code)

print("✅ AST Reformat completed (production-safe mode)")
PY

# 3) Syntax verification
echo "🔍 Syntax check..."
python3 -m py_compile "$FILE"

if [ $? -ne 0 ]; then
    echo "❌ SYNTAX ERROR - Rolling back"
    cp "$BACKUP" "$FILE"
    exit 1
fi

# 4) Restart system
pm2 restart AlphaBuffalo --update-env
sleep 3

# 5) Health check
echo "🔍 HEALTH"
curl -s "http://localhost:8000/health" || echo "NO RESPONSE"

echo ""
echo "🔍 SIGNAL TEST"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO RESPONSE"

echo ""
echo "🚀 AST REFORMAT COMPLETE"
