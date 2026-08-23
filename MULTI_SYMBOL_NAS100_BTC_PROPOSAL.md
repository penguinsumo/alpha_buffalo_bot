# เพิ่ม NAS100 และ BTC — ตรวจสอบความพร้อมของโค้ด + แผนเสนอ

จัดทำ 23 ส.ค. 2026 | branch `feature/v5-fusion-newday`

---

## สรุปสั้น

โครงหลักของ engine_v4 **พร้อมรองรับหลาย symbol มากกว่าที่คาด** เพราะโซน Kivanc/PRZ คำนวณจาก Fibonacci ของ range ราคาเอง ไม่ได้ hardcode เป็นราคาทองคำ แต่มี **3 จุดที่ยัง gold-only จริง** ต้องแก้ก่อนใช้งานได้ กับอีก **2 ความเสี่ยงเชิงพฤติกรรมตลาด** ที่ต้องตัดสินใจก่อนปล่อยออกจริง ไม่ใช่แค่แก้โค้ด

---

## 1. สิ่งที่พร้อมอยู่แล้ว (ตรวจโค้ดจริง)

- `run_pipeline(symbol, public_symbol)`, ทุก endpoint ใน `alpha_buffalo_signal.py`, `ScenarioScanner.scan()`, `early_warning.py` — รับ `symbol` เป็นพารามิเตอร์อยู่แล้ว ไม่ได้ล็อกไว้ในโค้ด
- ดึงราคาจาก TwelveData ผ่าน `/time_series?symbol=...` — endpoint เดียวกันใช้ได้กับทุก asset class (forex/crypto/index) แค่เปลี่ยนพารามิเตอร์
- `engine_v4/indicators.py` — Kivanc_Buy_Zone/Sell_Zone คำนวณจาก `Fib_0786`, `Fib_0618` ฯลฯ ซึ่งอิงจาก range ของแท่งเทียนเอง (relative) ไม่ใช่เลขราคาทองคำตายตัว → โครงสร้าง indicator ใช้ข้าม asset ได้โดยหลักการ
- `execution_lifecycle.py` — `self._positions` และ `self._hourly_stats` เก็บเป็น `dict[symbol]` อยู่แล้ว รองรับหลาย symbol พร้อมกันได้โดยไม่ชนกัน
- MT5 EA (`AlphaBuffalo_CloudEA_ExecutionOnly_v304.mq5`) ใช้ `_Symbol` (สัญลักษณ์ของชาร์ตที่รันอยู่) เป็นหลักอยู่แล้ว ไม่ได้ hardcode XAUUSD ในตรรกะการเปิด/ปิดออเดอร์

## 2. จุดที่ยัง gold-only จริง ต้องแก้

1. **`pine_signal_bridge.py`** — คอมเมนต์ในไฟล์เขียนไว้ตรงๆ ว่า `"the bridge is a gold-only relay"` ฟังก์ชัน `_symbol()` มี normalize เฉพาะ prefix `XAUUSD` (ตัด suffix โบรกเกอร์เช่น `XAUUSD-VIP`, `XAUUSD.m`) แต่ symbol อื่นที่ผ่านเข้ามาจะไม่ถูก normalize เลย — ต้องเพิ่ม normalize เทียบเท่าให้ `NAS100`/`BTCUSD` (โบรกเกอร์มักมี suffix แบบ `BTCUSDm`, `NAS100.cash`, `USTECm` ด้วยเช่นกัน)

2. **`CanonicalSymbol()` ใน MT5 EA** — บรรทัด 478 มีแค่ `if(StringFind(value, "XAUUSD") == 0) return "XAUUSD";` ต้องเพิ่มเงื่อนไขเทียบเท่าให้ NAS100/BTC มิฉะนั้นถ้า broker ใช้ symbol suffix (เช่น `BTCUSD.m`) คำสั่งจาก Python จะไม่ match กับ symbol บนชาร์ต แล้วโดน EA reject (`"symbol mismatch"` ที่บรรทัด 118)

3. **TwelveData symbol string ที่ถูกต้อง** — ยืนยันได้แค่บางส่วนจากเอกสารสาธารณะ (เชื่อมต่อ TwelveData โดยตรงจาก sandbox นี้ยังโดนบล็อกเหมือนเดิม):
   - **BTC** → `BTC/USD` ยืนยันแล้วจากหน้า TwelveData เอง
   - **NAS100** → **ยังไม่ยืนยัน** ต้องเช็คผ่าน `/symbol_search` เมื่อเปิด network access แล้ว ตัวเลือกที่เป็นไปได้คือ `NDX` (ticker มาตรฐานของ Nasdaq-100 index) หรืออาจต้องใช้ CFD/futures proxy แทน เพราะ TwelveData เอกสารสาธารณะไม่ได้ระบุ index ticker ตรงๆ — **อย่าฮาร์ดโค้ดเดาไปก่อน** เสี่ยงดึงข้อมูลผิด symbol ไปคำนวณสัญญาณ

## 3. ความเสี่ยงเชิงพฤติกรรมตลาด (ไม่ใช่บั๊ก แต่ต้องตัดสินใจ)

- **BTC เทรด 24/7** ไม่มีวันหยุดสุดสัปดาห์เหมือนทอง/forex — แต่ `session_clock.py` และ `SessionGate`/`weekend-closed` logic ทั้งหมดออกแบบมาสำหรับตลาดที่ปิดเสาร์-อาทิตย์ ถ้าเอา BTC เข้าไปตรงๆ จะโดน weekend-gate บล็อกทั้งที่ตลาดจริงยังเปิดอยู่ — ต้อง exempt BTC จาก weekend/session gate หรือสร้าง session profile ใหม่เฉพาะ crypto
- **NAS100 มีเวลาเปิด-ปิดตลาดจริงแบบ equity** (09:30–16:00 ET + pre/after market) ต่างจากทองที่เกือบ 24/5 — Asia/London/NY session mapping ที่ปรับสำหรับทองคำจะไม่ตรงกับพฤติกรรม NAS100 เลย (เช่น ช่วง "Asia session" ของทองคือช่วงที่ NAS100 แทบไม่มี volume) — ต้องมี session profile แยกต่างหากต่อ symbol ไม่ใช่ใช้ threshold ชุดเดียวกัน

## 4. แผนที่เสนอ (เรียงตามความเสี่ยง)

**Phase 1 — Diagnostic-only multi-symbol scan (ต่ำสุด, แนะนำเริ่มก่อน)**
เพิ่ม NAS100/BTC เข้าไปใน `/signal/scenarios` และ pipeline แบบ **อ่านอย่างเดียว ไม่ส่งคำสั่งเปิดออเดอร์จริง** เก็บผลสัญญาณคู่ขนานกับทองไปสักพัก (เช่น 1-2 สัปดาห์) เพื่อดูว่า Kivanc zone/VSA logic ที่ tune มาเพื่อทองให้ signal คุณภาพขนาดไหนกับ asset อื่น ก่อนเสี่ยงเงินจริง — ใช้โครงที่มีอยู่แล้วได้เกือบทั้งหมด แก้แค่จุดที่ 2.3 (symbol string)

**Phase 2 — เปิด Bridge + EA ให้รองรับ**
แก้ 2 จุดใน section 2 (pine_signal_bridge normalize + EA CanonicalSymbol) พร้อม regression test ใหม่ยืนยันว่า XAUUSD path เดิมไม่เปลี่ยนพฤติกรรม

**Phase 3 — Session profile แยกต่อ symbol**
ออกแบบ session gate ให้เลือก profile ตาม symbol (crypto = ไม่มี weekend gate, index = ใช้เวลาตลาดจริงของ NASDAQ) แทนใช้ค่าเดียวที่ tune มาเพื่อทอง

**Phase 4 — เปิดสาย execution จริง**
เปิดใช้เมื่อ Phase 1 ยืนยันคุณภาพสัญญาณเป็นที่น่าพอใจแล้วเท่านั้น

---

## สิ่งที่ต้องตัดสินใจ/ทำเมื่อว่าง

1. ยืนยัน TwelveData ticker ที่ถูกต้องสำหรับ NAS100 ผ่าน `/symbol_search` (รอเปิด network access)
2. ยืนยันชื่อ symbol ที่โบรกเกอร์ MT5 ของคุณใช้จริงสำหรับ NAS100/BTC (เช่น `BTCUSDm`, `NAS100.cash`) เพื่อ map ให้ EA ถูก
3. เลือกว่าจะเริ่ม Phase 1 (diagnostic only) เลยไหม หรือรอตัดสินใจเรื่อง session profile ก่อน
