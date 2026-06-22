#!/bin/bash

echo "🧠 Alpha Buffalo Emergency Recovery v11.5"

APP=AlphaBuffalo
FILE=~/alpha_buffalo_bot/signal_composer.py

echo ""
echo "🔍 1) PM2 STATUS"
pm2 describe $APP

echo ""
echo "🔍 2) LAST CRASH LOG"
pm2 logs $APP --err --lines 50

echo ""
echo "🔍 3) PYTHON SYNTAX CHECK"
python3 -m py_compile "$FILE"
if [ $? -ne 0 ]; then
    echo "❌ SYNTAX STILL BROKEN → ROLLBACK LAST BACKUP"

    LAST_BACKUP=$(ls -t ~/alpha_buffalo_bot/*.bak* 2>/dev/null | head -n 1)

    if [ -f "$LAST_BACKUP" ]; then
        cp "$LAST_BACKUP" "$FILE"
        echo "✅ Restored from: $LAST_BACKUP"
    else
        echo "❌ NO BACKUP FOUND"
    fi
fi

echo ""
echo "🔍 4) RESTART CLEAN"
pm2 restart $APP --update-env

sleep 3

echo ""
echo "🔍 5) HEALTH CHECK"
curl -s "http://localhost:8000/health" || echo "❌ NO RESPONSE"

echo ""
echo "🔍 6) SIGNAL CHECK"
curl -s "http://localhost:8000/signal/latest?key=DEMO123" || echo "❌ NO RESPONSE"

echo ""
echo "🚀 RECOVERY DONE"
