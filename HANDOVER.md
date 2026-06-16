# 🐃 Alpha Buffalo v5.4 — Handover Document

**วันที่:** 14 มิถุนายน 2026
**สถานะ:** Production Ready
**Backtest:** +394.70%

## Versioning
Platform: Alpha Buffalo v5.4 | Strategy: Exit Ladder v6C

## Backtest
| Asset | WR | PnL | DD |
|-------|-----|------|-----|
| XAUUSD | 61.1% | +197.98% | -2.28% |
| US100 | 62.7% | +63.47% | -0.75% |
| KOSPI | 71.4% | +67.42% | -0.90% |
| JPN225 | 58.4% | +65.83% | -0.90% |

## Railway
https://alphabuffalobot-production.up.railway.app

## EA Status
VSA Gate ✅ | M5 Data ✅ | Partial Close ✅

## 17. Startup Commands

```bash
pm2 start alpha_buffalo_signal.py --interpreter python3
pm2 save
pm2 startup
curl http://localhost:8000/health
curl https://alphabuffalobot-production.up.railway.app/health
pm2 logs AlphaBuffalo --lines 20
cd ~/alpha_buffalo_bot && cat >> HANDOVER.md << 'ENDOF'
### Health Check
```bash
curl http://localhost:8000/health
curl https://alphabuffalobot-production.up.railway.app/health
pm2 logs AlphaBuffalo --lines 20
pm2 restart AlphaBuffalo --update-env
cd ~/alpha_buffalo_bot && echo '### Health Check' >> HANDOVER.md && echo '```bash' >> HANDOVER.md && echo 'curl http://localhost:8000/health' >> HANDOVER.md && echo 'curl https://alphabuffalobot-production.up.railway.app/health' >> HANDOVER.md && echo '```' >> HANDOVER.md && echo '### Logs' >> HANDOVER.md && echo '```bash' >> HANDOVER.md && echo 'pm2 logs AlphaBuffalo --lines 20' >> HANDOVER.md && echo '```' >> HANDOVER.md && echo '### Restart' >> HANDOVER.md && echo '```bash' >> HANDOVER.md && echo 'pm2 restart AlphaBuffalo --update-env' >> HANDOVER.md && echo '```' >> HANDOVER.md && echo "✅ Done" && wc -l HANDOVER.md
cd ~/alpha_buffalo_bot && cat HANDOVER.md
pwd && ls -la HANDOVER.md && head -5 HANDOVER.md
cd ~/alpha_buffalo_bot && rm HANDOVER.md && cat > HANDOVER.md << 'EOF'
# 🐃 Alpha Buffalo v5.4 — Handover Document

**วันที่:** 14 มิถุนายน 2026
**สถานะ:** Production Ready ✅
**Backtest:** +394.70% รวม 4 assets ใน 5.5 เดือน

## Versioning
Platform: Alpha Buffalo v5.4 | Strategy: Exit Ladder v6C
Kivanc VSA v1 | Harmonic Detector v1 | Micro Engine v1

## Backtest Results
| Asset | WR | PnL | DD |
|-------|-----|------|-----|
| XAUUSD | 61.1% | +197.98% | -2.28% |
| US100 | 62.7% | +63.47% | -0.75% |
| KOSPI | 71.4% | +67.42% | -0.90% |
| JPN225 | 58.4% | +65.83% | -0.90% |
| **รวม** | — | **+394.70%** | **-2.28%** |

## Railway
https://alphabuffalobot-production.up.railway.app

## EA Status
✅ VSA Gate | ✅ M5 Data | ✅ Partial Close | ✅ BE + Trail

## Live Validation KPI
Month 1: Trades > 100 | WR > 55% | DD < 5% | PF > 1.4

## Startup Commands
### Health Check
curl http://localhost:8000/health
curl https://alphabuffalobot-production.up.railway.app/health
### Logs
pm2 logs AlphaBuffalo --lines 20
### Restart
pm2 restart AlphaBuffalo --update-env

---

## 18. Production Data Sources (June 15, 2026)

### Data Providers

| Source | Use Case | Symbol | Status |
|--------|----------|--------|--------|
| **Twelve Data** | Production (Live) | XAU/USD | ✅ Active |
| **Yahoo Finance** | Backtest Only | GC=F | ✅ Test Only |

### Twelve Data
- API Key: `4b75c872...`
- Interval: 15min
- Free Tier: 800 calls/day
- Fields: Open, High, Low, Close
- No Volume on Free Tier

### Yahoo Finance
- Used for: Backtest, Strategy Testing
- Symbol: GC=F (Gold Futures)
- NOT used in Production
- Prices may differ from Twelve Data by ~$20-30

### Important: Do NOT confuse!
- Production Signal → Twelve Data
- Backtest Results → Yahoo Finance
- Both are valid but for DIFFERENT purposes

