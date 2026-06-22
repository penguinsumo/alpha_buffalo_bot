#!/bin/bash

echo "🧠 Alpha Buffalo SOURCE RESTORE MODE"

FILE=~/alpha_buffalo_bot/signal_composer.py

# หา backup ล่าสุด
BACKUP=$(ls -t ~/alpha_buffalo_bot/*.bak* ~/alpha_buffalo_bot/*.full_backup* 2>/dev/null | head -n 1)

if [ -z "$BACKUP" ]; then
    echo "❌ NO BACKUP FOUND - MANUAL REBUILD REQUIRED"
    exit 1
fi

echo "📦 Restoring from: $BACKUP"

cp "$BACKUP" "$FILE"

echo "🔍 Syntax check after restore..."
python3 -m py_compile "$FILE"

if [ $? -ne 0 ]; then
    echo "❌ BACKUP ALSO BROKEN → SYSTEM CORRUPTED"
    exit 1
fi

echo "🔄 Restart PM2..."
pm2 restart AlphaBuffalo --update-env

sleep 3

echo ""
echo "🔍 HEALTH CHECK"
curl -s "http://localhost:8000/health" || echo "NO RESPONSE"

echo ""
echo "🔍 SIGNAL CHECK"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "NO RESPONSE"

echo ""
echo "🚀 RESTORE COMPLETE"
