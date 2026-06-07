import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# === ฟังก์ชันที่แก้ไขให้รองรับ RangeIndex ===
def calc_pdh_pdl(df):
    """คำนวณ Pivot High/Low จากข้อมูลรายวัน รองรับ RangeIndex"""
    if not isinstance(df.index, pd.DatetimeIndex):
        if 'time' in df.columns:
            df = df.set_index(pd.to_datetime(df['time']))
        elif 'datetime' in df.columns:
            df = df.set_index(pd.to_datetime(df['datetime']))
        else:
            from datetime import timedelta
            start = datetime.now() - timedelta(hours=len(df))
            df.index = pd.date_range(start=start, periods=len(df), freq='H')
    if df.index.tzinfo is None:
        df.index = df.index.tz_localize('UTC')
    df['date'] = df.index.date
    daily_high = df.groupby('date')['high'].max()
    daily_low = df.groupby('date')['low'].min()
    if len(daily_high) > 1:
        pdh = daily_high.iloc[-2]
        pdl = daily_low.iloc[-2]
    else:
        pdh = daily_high.iloc[-1]
        pdl = daily_low.iloc[-1]
    return pdh, pdl# force rebuild Sun Jun  7 16:17:00 +07 2026

class MicroEngine:
    def __init__(self):
        self.day_level = None
        self.session_level = None
        self.sweeps = []
    
    def update(self, df_15m):
        # เรียก calc_pdh_pdl ที่เรามีอยู่แล้ว
        self.day_level = calc_pdh_pdl(df_15m)
        # จำลอง session level (เพิ่มตามต้องการ)
        self.session_level = (df_15m['high'].max(), df_15m['low'].min())
        # ตรวจจับ sweep (ตัวอย่าง)
        # ... (จะเพิ่มทีหลัง)
        return []

def run_micro(df_15m):
    """ฟังก์ชันหลักที่ signal_composer เรียก"""
    engine = MicroEngine()
    return engine.update(df_15m)
