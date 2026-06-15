
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
            df.index = pd.date_range(start=start, periods=len(df), freq='h')
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

class MicroSignal:
    """Signal from micro engine (sweep, session H/L, etc.)"""
    def __init__(self, direction: str, trigger: str, confluence_score: int = 1):
        self.direction = direction
        self.trigger = trigger
        self.confluence_score = confluence_score

def get_micro_summary(signals):
    """Return summary of micro signals"""
    if not signals:
        return {"has_signal": False, "buy_count": 0, "sell_count": 0, "bias": "NEUTRAL"}
    buy_count = sum(1 for s in signals if s.direction == "BUY")
    sell_count = sum(1 for s in signals if s.direction == "SELL")
    return {
        "has_signal": len(signals) > 0,
        "buy_count": buy_count,
        "sell_count": sell_count,
        "bias": "BUY" if buy_count > sell_count else ("SELL" if sell_count > buy_count else "NEUTRAL")
    }

# ========== ฟังก์ชันที่เพิ่มสำหรับ VSA + Spike detection ==========
def detect_spike_15m(df, atr_mult=1.5):
    """ตรวจจับ spike จาก candle ปิด 15M"""
    if len(df) < 2:
        return False, None
    # คำนวณ ATR แบบง่าย
    tr = pd.concat([df['high']-df['low'],
                    (df['high']-df['close'].shift(1)).abs(),
                    (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    range_ = last['high'] - last['low']
    if range_ > atr * atr_mult and body > range_ * 0.6:
        if last['close'] > last['open']:
            return True, 'bullish'
        else:
            return True, 'bearish'
    return False, None

# ตรวจสอบว่ามี class MicroSignal หรือยัง (ถ้ายังให้เพิ่ม)
if 'class MicroSignal' not in open(__file__).read():
    class MicroSignal:
        def __init__(self, direction, trigger, confluence_score=1):
            self.direction = direction
            self.trigger = trigger
            self.confluence_score = confluence_score

    def get_micro_summary(signals):
        if not signals:
            return {"has_signal": False, "buy_count": 0, "sell_count": 0, "bias": "NEUTRAL"}
        buy_count = sum(1 for s in signals if s.direction == "BUY")
        sell_count = sum(1 for s in signals if s.direction == "SELL")
        return {
            "has_signal": len(signals) > 0,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "bias": "BUY" if buy_count > sell_count else ("SELL" if sell_count > buy_count else "NEUTRAL")
        }

# ตรวจสอบว่ามี class MicroEngine และ run_micro หรือยัง
if 'class MicroEngine' not in open(__file__).read():
    class MicroEngine:
        def __init__(self):
            self.day_level = None
            self.session_level = None
            self.sweeps = []
        def update(self, df_15m):
            self.day_level = calc_pdh_pdl(df_15m)
            self.session_level = (df_15m['high'].max(), df_15m['low'].min())
            return []

    def run_micro(df_15m):
        engine = MicroEngine()
        return engine.update(df_15m)

# ========== ฟังก์ชันที่เพิ่มสำหรับ VSA + Spike detection ==========
def detect_spike_15m(df, atr_mult=1.5):
    """ตรวจจับ spike จาก candle ปิด 15M"""
    if len(df) < 2:
        return False, None
    # คำนวณ ATR แบบง่าย
    tr = pd.concat([df['high']-df['low'],
                    (df['high']-df['close'].shift(1)).abs(),
                    (df['low']-df['close'].shift(1)).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    range_ = last['high'] - last['low']
    if range_ > atr * atr_mult and body > range_ * 0.6:
        if last['close'] > last['open']:
            return True, 'bullish'
        else:
            return True, 'bearish'
    return False, None

# ตรวจสอบว่ามี class MicroSignal หรือยัง (ถ้ายังให้เพิ่ม)
if 'class MicroSignal' not in open(__file__).read():
    class MicroSignal:
        def __init__(self, direction, trigger, confluence_score=1):
            self.direction = direction
            self.trigger = trigger
            self.confluence_score = confluence_score

    def get_micro_summary(signals):
        if not signals:
            return {"has_signal": False, "buy_count": 0, "sell_count": 0, "bias": "NEUTRAL"}
        buy_count = sum(1 for s in signals if s.direction == "BUY")
        sell_count = sum(1 for s in signals if s.direction == "SELL")
        return {
            "has_signal": len(signals) > 0,
            "buy_count": buy_count,
            "sell_count": sell_count,
            "bias": "BUY" if buy_count > sell_count else ("SELL" if sell_count > buy_count else "NEUTRAL")
        }

# ตรวจสอบว่ามี class MicroEngine และ run_micro หรือยัง
if 'class MicroEngine' not in open(__file__).read():
    class MicroEngine:
        def __init__(self):
            self.day_level = None
            self.session_level = None
            self.sweeps = []
        def update(self, df_15m):
            self.day_level = calc_pdh_pdl(df_15m)
            self.session_level = (df_15m['high'].max(), df_15m['low'].min())
            return []

    def run_micro(df_15m):
        engine = MicroEngine()
        return engine.update(df_15m)
