#!/bin/bash
echo "🐃 Updating Alpha Buffalo Handover..."
sed -i '' "s/\*\*วันที่:\*\* .*/\*\*วันที่:\*\* $(date '+%d %B %Y')/" HANDOVER.md
echo "✅ HANDOVER updated"
