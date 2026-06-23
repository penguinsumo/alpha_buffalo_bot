import ast
import sys

file = "signal_composer.py"

try:
    with open(file) as f:
        ast.parse(f.read())
except Exception as e:
    print("❌ AST FAILED:", e)
    sys.exit(1)

print("✅ AST OK")
