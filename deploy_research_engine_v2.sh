#!/bin/bash

echo "🧠 Deploy Alpha Buffalo Research Engine v2 (LEGACY SAFE)"

cp alpha_buffalo_research_engine_v2.py ~/alpha_buffalo_bot/

echo "📦 Copied engine core"

pm2 restart AlphaBuffalo --update-env

sleep 5

echo "🔍 Health check..."
curl -s "http://localhost:8000/health"

echo ""
echo "🔍 Signal test..."
curl -s "http://localhost:8000/signal/latest?key=DEMO123"

echo ""
echo "🚀 DEPLOY COMPLETE (GATES ACTIVE)"
