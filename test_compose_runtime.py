#!/usr/bin/env python3
"""ทดสอบ runtime compose_signal หลัง refactor"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from data_provider_twelvedata import fetch_twelvedata
from signal_composer import compose_signal

print("Loading data...")
df_15m = fetch_twelvedata('XAU/USD', '15min', 2)  # ดึงแค่ 2 แท่งล่าสุด
print(f"Got {len(df_15m)} candles")

# สร้าง DataFrame 4h, 1h จำลอง (ไม่จำเป็นต้องสมบูรณ์)
df_1h = df_15m.resample('1h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()
df_4h = df_15m.resample('4h').agg({'open':'first','high':'max','low':'min','close':'last'}).dropna()

print("Calling compose_signal...")
try:
    sig = compose_signal(df_4h, df_1h, df_15m)
    if sig:
        print(f"✅ Signal generated: {sig.direction} {sig.signal_type} @ {sig.entry_price}")
        print(f"   SL={sig.sl_price}, TP1={sig.tp1_price}, TP2={sig.tp2_price}")
        print(f"   Label: {sig.label}")
    else:
        print("ℹ️ No signal (normal if outside session/score threshold)")
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
