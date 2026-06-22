#!/bin/bash

echo "🧯 Alpha Buffalo RECOVERY MODE"

cd ~/alpha_buffalo_bot || exit 1

# ─────────────────────────────
# 1. Restore PM2 clean restart
# ─────────────────────────────
pm2 restart AlphaBuffalo --update-env || pm2 start alpha_buffalo_signal.py

sleep 3

# ─────────────────────────────
# 2. Check process
# ─────────────────────────────
pm2 list

echo ""
echo "🔍 HEALTH CHECK"
curl -i http://localhost:8000/health || echo "NO RESPONSE"

echo ""
echo "🔍 SIGNAL CHECK"
curl -i "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO RESPONSE"

echo ""
echo "🚀 RECOVERY DONE"
