# V4_Buy_Setup Logic Breakdown + Clean v5 vs v12-core Comparison Status

จัดทำระหว่างที่รันแบบ unattended ตามที่อนุญาตไว้ | 22 ส.ค. 2026

---

## 1. โครงสร้างเต็มของ `V4_Buy_Setup` (engine_v4/indicators.py)

ไล่โค้ดจริงจนสุดสายแล้ว สรุปเป็นสูตรได้แบบนี้ (SELL เป็น mirror เป๊ะ):

```
V4_Buy_Setup = standard_buy_setup OR Zone_Buy_Pinbar_Trigger OR Deep_Buy_Reclaim_Trigger
```

มี **3 ทางเข้า** ไม่ใช่ AND เดียวอย่างที่เข้าใจไว้ก่อนหน้า:

**ทางที่ 1 — standard_buy_setup**
```
base_buy_entry_zone AND HA_Bull_Reversal AND VSA_Buy_Wins
```
- `base_buy_entry_zone = buy_location AND buy_reaction`
  - `buy_location = In_Pine_PRZ_Support OR In_Session_Kivanc_Buy_Zone`
  - `buy_reaction = Near_BB_Lower OR Bull_Sweep`
- ต้องมี HA reversal แท่งปิดจริง + VSA ฝั่งซื้อชนะ ยืนยันบนแท่งเดียวกัน — เข้มสุดในสามทาง เพราะทุกเงื่อนไขต้อง "ตรงกันพอดี" บนแท่งเดียว

**ทางที่ 2 — Zone_Buy_Pinbar_Trigger** (setup-only แล้วรอ break)
```
candidate = In_Session_Kivanc_Buy_Zone AND In_Pine_PRZ_Support AND Near_BB_Lower AND Bullish_Pinbar AND VSA_Buy_Wins
→ trigger เมื่อแท่งถัดมา break high ของ pinbar
```

**ทางที่ 3 — Deep_Buy_Reclaim_Trigger** (setup-only แล้วรอ reclaim)
```
candidate = Deep_Buy_Touch_100 AND Deep_Buy_PRZ_Context AND Near_BB_Lower AND Bullish_Pinbar AND VSA_Buy_Wins
→ trigger เมื่อแท่งถัดมา break wall แล้ว reclaim กลับเข้าโซน
```

**ข้อสรุปสำคัญ**: การออกแบบนี้ไม่ได้แข็งเป็น AND เดียวอย่างที่ผมอธิบายไว้แบบง่ายเกินไปในเอกสารรอบก่อน — มันมี 3 เส้นทางคู่ขนาน แต่ทั้ง 3 เส้นทางต้องพึ่ง `VSA_Buy_Wins` ร่วมกันหมด (single point of dependency) และทางที่ 2-3 เป็นแบบ 2-step (setup แล้วรอ break/reclaim อีกแท่ง) ซึ่งหมายความว่าจังหวะเข้าจะช้ากว่า clean v5 ที่ (จากที่เห็นใน `signal_composer.py.full_backup` เดิม) ดูจะยิงจากคะแนนสะสมได้ทันทีโดยไม่ต้องรอ 2-step confirmation แบบนี้

---

## 2. ทำไมยังเทียบกับสัญญาณจริงในภาพไม่ได้ 100% — สรุปเหตุผลอีกครั้งให้ชัด

ลองแล้วทั้ง 2 ทาง (TwelveData ตรง + Railway API) ทั้งคู่โดน sandbox บล็อกที่ระดับ network egress allowlist:

```
Host not in allowlist: api.twelvedata.com
Host not in allowlist: backboard.railway.app
```

ไม่ใช่ปัญหา credential — ทั้ง API key และ Railway token ที่ได้มาถูกต้อง แต่ sandbox นี้ไม่มีสิทธิ์ต่อออกไปหา host พวกนี้เลย ต้องแก้ที่ Admin settings → Capabilities → Network access ของ org (ต้องมีสิทธิ์ admin) — อันนี้ผมทำเองไม่ได้จริงๆ

**เพิ่มเติมจากที่เจอไปแล้ว (session label ไม่ตรง)**: ตอนนี้เจอหลักฐานอีกจุดที่หนักกว่าเดิม —

ภาพสัญญาณจริงโชว์ **"Score: 7/10"** แต่ `score_manager.py` ใน git `main` (clean v5) ใช้สเกล **max theoretical = 22** (Bucket A max+6, B max+5, C max+5, D max+2, E ±4) ไม่มีทางออกมาเป็น "X/10" ได้เลยจากสูตรนี้

รวมกับที่เจอไปก่อนหน้า (session label "Asia" ไม่ตรงกับเวลาจริงที่ควรเป็น NY ตามทั้ง git `main` และ v12-core) — **หลักฐาน 2 จุดนี้ชี้ไปทางเดียวกันอย่างหนักแน่นว่า: โค้ดที่รันจริงบน Railway ไม่ใช่โค้ดที่อยู่ใน git `main` ที่ผมเข้าถึงได้** น่าจะเป็นคนละเวอร์ชัน (อาจแก้ไขแล้ว deploy ตรงจากเครื่อง โดยไม่เคย push, หรือมี scoring formula คนละสูตรที่ไม่ได้ commit)

**ผลที่ตามมา**: ต่อให้เปิด network access ให้ผมดึงราคาย้อนหลังได้สำเร็จ การเอาโค้ด `main` ที่ผมมีอยู่มา "จำลอง" สิ่งที่ clean v5 คำนวณ ก็จะได้ผลลัพธ์ที่ไม่ตรงกับของจริงบน Railway อยู่ดี เพราะสูตรคะแนนไม่ตรงกันตั้งแต่ต้น — **สิ่งที่ต้องทำก่อนเปรียบเทียบได้จริง คือต้องรู้ก่อนว่า Railway รันซอร์สโค้ดตัวไหนกันแน่** (ตามที่แนะนำให้เช็คหน้า Railway → Settings → Source กับ Deployments ไปแล้วในข้อความก่อนหน้า)

---

## 3. Newday — สถานะล่าสุด

โครงสร้างที่ต่อสายไว้ในรอบก่อน (`runtime_layers/newday.py`, `GET /newday/map`) ยังใช้งานได้ปกติ แต่ยัง**ไม่มีข้อมูลจริงให้ทดสอบ** เพราะ `scripts/daily_market_scan.py` ต้องดึง TwelveData เหมือนกัน — โดนบล็อก host เดียวกัน ทดสอบซ้ำแล้วยืนยันว่าโค้ดฝั่ง wiring (loader, diagnostic endpoint, regression tests) ทำงานถูกต้องกับข้อมูลจำลอง (86 regression tests ผ่านหมดตามที่ยืนยันไปแล้วรอบก่อน) — พร้อมใช้งานทันทีที่มีข้อมูลจริงเข้ามา ไม่ต้องแก้โค้ดเพิ่ม

---

## สิ่งที่รอคุณตอนตื่นมา — 2 อย่างที่ต้องตัดสินใจ/เช็ค

1. **เช็ค Railway → service → Settings → Source + Deployments** ว่าจริงๆ deploy จากไหน branch ไหน commit hash อะไร — นี่คือกุญแจสำคัญที่สุดตอนนี้ ถ้าไม่ตรงกับ git `main` เลย แปลว่าโค้ด production จริงหายไปจาก repo ต้องหาทางดึงกลับมา sync
2. **เปิด network egress allowlist** สำหรับ `api.twelvedata.com` และ `backboard.railway.app` ที่ Admin settings → Capabilities → Network access (ถ้าคุณมีสิทธิ์ owner ของ org) — เปิดแล้วผมจะดึงราคาจริงและเช็ค Railway source ให้ได้ทันทีโดยไม่ต้องรอส่ง token/ข้อมูลเพิ่ม
