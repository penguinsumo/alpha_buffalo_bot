#!/bin/bash

echo "🧠 Alpha Buffalo HARD RESTORE v11 (Production Recovery)"

TARGET=~/alpha_buffalo_bot/signal_composer.py
BACKUP=~/alpha_buffalo_bot/signal_composer.py.full_backup.1782152954.py

echo "📦 Using backup:"
echo "$BACKUP"

if [ ! -f "$BACKUP" ]; then
    echo "❌ Backup not found!"
    exit 1
fi

# 1) Restore
cp "$BACKUP" "$TARGET"
echo "✅ Restored signal_composer.py"

# 2) Syntax check
echo "🔍 Checking syntax..."
python3 -m py_compile "$TARGET"

if [ $? -ne 0 ]; then
    echo "❌ RESTORE FAILED (backup also broken)"
    exit 1
fi

# 3) Restart service
echo "🔄 Restarting PM2..."
pm2 restart AlphaBuffalo --update-env

sleep 3

# 4) Health check
echo ""
echo "🔍 HEALTH CHECK"
curl -s "http://localhost:8000/health" || echo "NO RESPONSE"

echo ""
echo "🔍 SIGNAL CHECK"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO RESPONSE"

echo ""
echo "🚀 RESTORE COMPLETE - SYSTEM SHOULD BE BACK ONLINE"
