
---

## 🕐 Session Clock (Final — BKK Time)

| Session | BKK Time | UTC (Summer) | UTC (Winter) | หมายเหตุ |
|---------|----------|--------------|--------------|-----------|
| **ASIA** | 05:00 – 14:00 | 22:00 – 07:00 | 22:00 – 07:00 | ซิดนีย์ + โตเกียว + ฮ่องกง |
| **LONDON** | 14:00 – 19:00 | 07:00 – 12:00 | 07:00 – 12:00 | EU Session |
| **NY** | 19:00 – 02:15 | 12:00 – 19:15 | 13:00 – 19:15 | ปรับตาม DST |
| **CLOSED** | 02:15 – 05:00 | 19:15 – 22:00 | 19:15 – 22:00 | หยุดเทรด (Rollover / Low Liquidity) |

---

## 🕐 Session Clock (Final — BKK Time)

| Session | BKK Time | UTC (Summer) | หมายเหตุ |
|---------|----------|--------------|-----------|
| **ASIA** | 05:00 – 14:00 | 22:00 – 07:00 | ซิดนีย์ + โตเกียว + ฮ่องกง |
| **LONDON** | 14:00 – 19:00 | 07:00 – 12:00 | EU Session |
| **NY** | 19:00 – 03:15 | 12:00 – 20:15 | NY Extended +1h (Winter: 20:00–03:15) |
| **CLOSED** | 03:15 – 05:00 | 20:15 – 22:00 | หยุดเทรด (Rollover / Low Liquidity) |

---

## 📊 Best Hours (Final — after NY extension)

### BUY 1H (Visual TP) — Hours to Trade
`0, 3, 4, 5, 6, 7, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 22, 23` (UTC)

### SELL 15m (Visual TP) — Hours to Trade
`5, 8, 11, 12, 14, 15, 17` (UTC)

---

## 🏆 Final Hybrid Backtest Results (NY Extended)

| Metric | Value |
|--------|-------|
| Total Trades | 390 |
| Win Rate | 54.1% |
| Return | **+353.59%** |
| Max DD | **-2.79%** |
| Profit Factor | **3.75** |
| Days Stopped | 2 |

---

## 🛠️ Final Hybrid Strategy Table

| Hour (UTC) | Session | BUY 1H (VisTP) | SELL 15m (VisTP) | Lot |
|:----------:|:-------:|:--------------:|:----------------:|:---:|
| 0 | ASIA | ✅ | ❌ | 1x |
| 3 | ASIA | ✅ | ❌ | 1x |
| 4 | ASIA | ✅ | ❌ | 1.5x |
| 5 | ASIA | ✅ | ✅ | 1x |
| 6 | ASIA | ✅ | ❌ | 1x |
| 7 | LONDON | ✅ | ❌ | 2x |
| 8 | LONDON | ❌ | ✅ | 1x |
| 9 | LONDON | ✅ | ❌ | 1x |
| 10 | LONDON | ✅ | ❌ | 2x |
| 11 | LONDON | ✅ | ✅ | 1.5x |
| **12** | **NY** | ✅ | ✅ | **2x** 🔥 |
| 13 | NY | ✅ | ❌ | 1x |
| 14 | NY | ✅ | ✅ | 1.5x |
| 15 | NY | ✅ | ✅ | 1.5x |
| 16 | NY | ✅ | ❌ | 2x |
| 17 | NY | ✅ | ✅ | 1.5x |
| 18 | NY | ✅ | ❌ | 1x |
| 19 | NY | ✅ | ❌ | 1x |
| 22 | ASIA | ✅ | ❌ | 1x |
| 23 | ASIA | ✅ | ❌ | 1x |
