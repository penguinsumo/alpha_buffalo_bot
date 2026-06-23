import sys

REQUIRED = [
    "SignalDecision",
    "safe_float",
    "compose_signal"
]

code = open("signal_composer.py").read()

missing = [r for r in REQUIRED if r not in code]

if missing:
    print("❌ CONTRACT VIOLATION:", missing)
    sys.exit(1)

print("✅ contract ok")
