#!/bin/bash
set -e

FILE="alpha_buffalo_signal.py"

echo "🚑 Fixing broken signal contract..."

sed -i '' 's/from signal_composer import compose_signal, SignalDecision, safe_float/from signal_composer import compose_signal/g' $FILE
sed -i '' 's/from signal_composer import compose_signal, SignalDecision/from signal_composer import compose_signal/g' $FILE
sed -i '' 's/from signal_composer import compose_signal, safe_float/from signal_composer import compose_signal/g' $FILE

echo "✔ Import fixed"

sed -i '' 's/SignalDecision/dict/g' $FILE

echo "✔ SignalDecision replaced"

sed -i '' 's/decision.tp1_price/decision["tp1_price"]/g' $FILE
sed -i '' 's/decision.sl_price/decision["sl_price"]/g' $FILE

echo "✔ Decision field patched"

if ! grep -q "def safe_float" $FILE; then
cat << 'INNER' >> $FILE

def safe_float(x, default=0.0):
    try:
        return float(x)
    except:
        return default
INNER
fi

echo "🚀 DONE"
