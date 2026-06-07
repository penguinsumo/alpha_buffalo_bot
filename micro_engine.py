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
    return pdh, pdl