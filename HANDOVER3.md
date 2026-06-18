# 🐃 Alpha Buffalo v5.4 / v11.2 — Handover 3 (The Settled Logic)

**วันที่:** 17 มิถุนายน 2026  
**สถานะ:** Production Ready (Stage: Final Tuning)  
**ผู้ส่งต่อ:** AI ตัวใหม่ (ผ่านการฝึกจากค่ายกลัดเลือด)  

---

## 📝 Part 0: คำส่งท้ายจาก AI ตัวใหม่

พี่ AI ตัวต่อไป... ผมเขียนต่อจาก HANDOVER2.md  
สิ่งที่อยู่ในนี้ไม่ใช่แค่ทฤษฎี แต่มันคือ **"ของจริงที่ตกผลึกจากการทดสอบซ้ำแล้วซ้ำเล่า"** กับเจ้าของระบบ  
ผมได้เรียนรู้จากความผิดพลาดของ AI ตัวก่อน, ได้ทะเลาะกับเจ้าของระบบ, ได้เห็นระบบพัง, และได้เห็นมันฟื้นคืนชีพด้วย Logic ที่ถูกต้อง  

**สิ่งที่ผมฝากไว้ให้คุณคือ "กฎเหล็ก" และ "ความจริง" ที่คุณไม่ต้องเจ็บปวดซ้ำรอยผมอีก**

---

## 📋 Part 1: สถานะระบบปัจจุบัน (Current State)

| องค์ประกอบ | สถานะ |
|-----------|-------|
| **Version** | **New V4 (No618+100 + Visual SL)** — พร้อมขึ้น Production |
| **V4 (Buy)** | Golden Zone 0.5–1.00, Sweep, BB Touch, Simple Trailing Exit |
| **V4 (Sell)** | Upper BB Touch + Sweep, **Visual SL (แตะ Mid BB → SL = Entry)**, TP Lower BB |
| **V5** | ยังคงใช้ Logic เดิม (PRZ + BOS) — **ยังไม่ได้เปลี่ยนแปลงในการปรับครั้งนี้** |
| **Tunnel (Parallel Channel)** | อยู่ระหว่างปรับให้เป็นไปตาม Dow Theory (2 HH + 2 HL) |
| **Session** | Asia ปลอดภัย, **Pre-Close (NY หลังปิด) กำลังจะถูกตัดทิ้ง** |

---

## 📐 Part 2: วิวัฒนาการของ Logic (What We Changed)

1. **Golden Zone (Fibo) ขยายเป็น 0.5–1.00**  
   - ตัด 0.618 ออก เพราะ DD สูง, FirstSL เยอะ  
   - เพิ่ม 1.00 เข้าไปเพราะเป็น Liquidity Sweep ที่ดีที่สุด  

2. **Sell Exit เปลี่ยนเป็น Visual SL**  
   - จากเดิม Simple Trailing → เปลี่ยนเป็น: **แตะ Mid BB → SL = Entry ทันที**  
   - ผล: Max DD ของ Sell ลดลงจาก -7.38% เหลือ -1.03% (Mock 90 วัน)  

3. **BE เร็ว (Buy 0.15%, Sell แตะ Mid)**  
   - ไม่ต้องรอให้ราคาวิ่งไกล เน้นปกป้องเงินทุนก่อน  

4. **ระบบแยก Buy/Sell ชัดเจน**  
   - Buy ใช้ Trailing เพราะขาขึ้นมีแนวโน้ม  
   - Sell ใช้ Visual SL เพราะขาลงเร็วและหลอกบ่อย  

---

## 🛡️ Part 3: Protocol สำหรับ AI ตัวต่อไป (กฎเหล็กใหม่)

1. **ตกลง Logic ก่อน → วิเคราะห์ร่วมกัน → ถึงเขียนโค้ด**  
   - ห้าม One-Shot โค้ดโดยที่ยังไม่ได้สรุป Logic ให้ตรงกันเด็ดขาด  
2. **Backtest ทุกครั้งก่อนขึ้น Production**  
   - ใช้ข้อมูล Mock ก่อน แล้วค่อยใช้ข้อมูลจริง (GC=F หรือ Twelve Data)  
3. **Position Sizing ต้องมี Cap Leverage**  
   - ป้องกัน Leverage ระเบิดจาก SL แคบเกินไป  
4. **ของเดิมที่ดีอยู่แล้ว — อย่าแตะ**  
   - V5, Basket Management, Risk Gate  
5. **Visual SL คือของขวัญสำหรับ Sell**  
   - อย่าพยายามเปลี่ยนกลับไปใช้ Trailing ใน Sell  
6. **Pre-Close (19:00–24:00 UTC) = ห้ามเทรด**  
   - ตัดทิ้งเพื่อลด Noise  

---

## ⚙️ Part 4: ระบบ New V4 (Final)

### 🟢 **Buy Entry (No618+100)**
- **เงื่อนไข:** Golden Zone 0.5–1.00, Sweep, Lower BB Touch  
- **SL:** ATR × 1.5  
- **Exit:**  
  - BE เมื่อกำไร ≥ 0.15% → SL = Entry  
  - Trailing Stop (Highest High × 0.9995)  
  - TP = Upper BB  

### 🔴 **Sell Entry (Visual SL)**
- **เงื่อนไข:** Upper BB Touch + Sweep + Bearish Trend  
- **SL เริ่มต้น:** ATR × 1.2  
- **Exit:**  
  - **แตะ Mid BB → SL = Entry (Visual SL)**  
  - TP = Lower BB  
  - ถ้าราคาดีดกลับชน SL → ปิด (อาจเป็น BE)  

---

## 📊 Part 5: ผล Backtest

### **Mock 90 วัน (New V4)**
| ด้าน | Buy | Sell | Total |
|------|-----|------|-------|
| **Trades** | 797 | 1,626 | 2,423 |
| **Win Rate** | 74.4% | 31.4% | 45.5% |
| **PnL%** | +10.71% | +11.03% | **+21.74%** |
| **Max DD%** | -1.66% | -1.03% | **-1.66%** |

### **ข้อมูลจริง GC=F 60 วัน (New V4 vs v11.2)**
| Metric | v11.2 | New V4 |
|--------|-------|--------|
| **Trades** | 1,564 | 586 |
| **Win Rate** | 69.7% | 23.4% |
| **PnL%** | +3.65% | **+12.02%** |
| **Max DD%** | -11.34% | **-2.50%** |

**ข้อสรุป:** New V4 มี Max DD ต่ำกว่ามาก และ PnL รวมสูงกว่า แม้ Win Rate จะต่ำกว่า เพราะตัดขาดทุนหนักได้เกือบหมด  

---

## 📂 Part 6: ไฟล์ที่ถูกแก้ไข

- **`scenario_scanner.py`** — เพิ่ม Parallel Channel (Dow Theory)  
- **`signal_composer.py`** — เพิ่ม Golden Zone 0.5–1.00, Visual SL สำหรับ Sell  
- **`trade_manager.py`** — เพิ่มการรับ Blueprint, ปรับ Exit เป็น Visual SL  
- **`ASIA_TUNING_v5p3.py`** — ปลดล็อก Asia Rebound ด้วย Golden Zone  

---

## 📌 Part 7: Backlog (สิ่งที่ต้องทำต่อ)

1. **Harmonic Detector → นักปราชญ์** (ข้อ 2 จาก 7 ข้อเดิม)  
2. **kivanc_absorb → Swing ใกล้สุดใน Session** (ข้อ 6)  
3. **Scenario Scanner → การบ้านหลังตลาดปิด** (ข้อ 7)  
4. **ตัด Pre-Close Session** (19:00–24:00 UTC) ออกจากการเทรด  
5. **Position Sizing Capped Leverage** เพื่อป้องกันระเบิด  
6. **เทส V5 ร่วมกับ New V4** บนข้อมูลจริง  

---

**"Stop Guessing. Start Executing."** 🐃  
**พร้อมส่งต่อ — ให้ AI ตัวต่อไป ไม่ต้องเสียเวลาหลงทางแบบผมอีก**

---

## 📊 Part 9: ผล Backtest บน Twelve Data 15m (ข้อมูลจริง 60 วัน)

**แหล่งข้อมูล:** Twelve Data API (XAU/USD 15m) — 5,000 candles  
**วิธีการวัดผล:** แยก Session Equity (Reset $10,000 ทุก Session) — ไม่ใช้ Compounding  
**Risk Gates:** Daily DD 3%, Consec Loss 5, Position Sizing 1% Risk (Max 10 Contracts)

| Session | Trades | Win Rate | Return | Max DD | Profit Factor |
|---------|--------|----------|--------|--------|---------------|
| **ASIA** | 344 | 56.69% | +134.10% | -7.48% | 3.27 |
| **LONDON** | 222 | 44.14% | +96.79% | -16.74% | 2.87 |
| **NY** | 289 | 57.09% | +136.12% | -3.94% | 7.46 |

### 🔍 ข้อสังเกตสำคัญ
- **NY ดีที่สุด:** Visual SL ลด Max DD จาก 9.52% (GC=F) เหลือ 3.94% — ควบคุมความเสี่ยงได้ดีเยี่ยมในตลาดผันผวน
- **LONDON ต้องปรับปรุง:** Max DD ยังสูง 16.74% แม้ใช้ Visual SL → เสนอให้ใช้ 1H Filter สำหรับ Sell ใน London
- **ASIA ทรงตัวดี:** Return +134%, Max DD 7.48% — Golden Zone 0.5-1.00 ทำงานได้ดี

### ⚠️ ความแตกต่างจาก Backtest ก่อนหน้า
| ปัจจัย | Backtest เดิม (GC=F/Mock) | Backtest ล่าสุด (Twelve Data) |
|--------|---------------------------|-------------------------------|
| **แหล่งข้อมูล** | GC=F หรือ Mock | Twelve Data XAU/USD จริง |
| **การวัด Return** | Compounding (สะสมข้าม Session) | Reset Equity ทุก Session |
| **Return ที่เห็น** | +1,600%+ (เกินจริง) | +96-136% (สมจริง) |
| **ข้อสรุป** | ใช้สำหรับเปรียบเทียบ Logic ได้ | ใช้สำหรับตัดสินใจ Production |

**บทเรียน:** การใช้ Compounding ทำให้ Return สูงเกินจริง และบดบังจุดอ่อนของระบบ  
**นับจากนี้:** ใช้ Twelve Data เป็นแหล่งข้อมูลหลัก และวัดผลแบบแยก Session เสมอ

---

## 🔧 Part 10: Backlog เพิ่มเติม (จากการทดสอบล่าสุด)

1. **ปรับ London Sell ใช้ 1H Filter** — เพื่อลด DD จาก 16.74% (สาเหตุจาก Noise ใน 15m)
2. **ทดสอบ Merged System** — London ใช้ v11.2 Entry + Visual SL, NY ใช้ New V4 Entry
3. **IG Client Sentiment API** — ดึง Long/Short % มาใช้เป็น Contrarian Filter
4. **ตัด Sell ในช่วง NY ที่มีแนวโน้ม Buy แรง** — จากผล Best 3Hr Windows

