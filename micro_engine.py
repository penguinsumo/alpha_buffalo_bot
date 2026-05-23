"""
micro_engine.py — Alpha Buffalo v5
Session H/L, PDH/PDL, Sweep Detection
Sprint 1D

Sessions (Bangkok Time UTC+7):
  Asia   : 02:00 - 09:00
  London : 14:00 - 20:00
  NY     : 20:00 - 23:59 + 00:00 - 02:00

Logic:
1. หา PDH/PDL จาก candle เมื่อวาน
2. หา Session H/L ของ Asia, London, NY
3. Detect Sweep (กวาด H/L แล้วกลับ)
4. ส่ง zone ให้ signal_composer จับ entry
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone, timedelta

# ── Bangkok Timezone ───────────────────────────────────────
BKK = timezone(timedelta(hours=7))

# ── Session Windows (Bangkok Time) ────────────────────────
SESSION_WINDOWS = {
    "Asia":   (2,  9),    # 02:00 - 09:00 BKK
    "London": (14, 20),   # 14:00 - 20:00 BKK
    "NY":     (20, 26),   # 20:00 - 02:00 next day (26 = 02:00+24)
}

# ── Sweep Config ──────────────────────────────────────────
SWEEP_PIPS     = 0.30    # ต้องกวาดเกิน X USD ถึงจะนับ
SWEEP_CLOSE_BACK = True  # close ต้องกลับเข้า range หลัง sweep


# ── Data Classes ──────────────────────────────────────────
@dataclass
class DayLevel:
    """Previous Day High/Low"""
    pdh: float   # Prev Day High
    pdl: float   # Prev Day Low
    date: str    # วันที่ของ prev day


@dataclass
class SessionLevel:
    """Session High/Low"""
    name: str
    high: float
    low: float
    start_idx: int
    end_idx: int
    swept_high: bool = False
    swept_low: bool  = False


@dataclass
class SweepEvent:
    """Sweep Detection Result"""
    session_name: str
    sweep_type: str      # "High_Sweep" or "Low_Sweep"
    swept_level: float
    sweep_price: float   # ราคาที่กวาดสูงสุด/ต่ำสุด
    close_price: float   # close หลัง sweep
    closed_back: bool    # close กลับเข้า range ไหม
    direction: str       # "SELL" (sweep high) or "BUY" (sweep low)
    candle_idx: int
    label: str = ""


@dataclass
class MicroSignal:
    """Output จาก micro_engine"""
    direction: str
    trigger: str          # "PDH_Sweep", "PDL_Sweep", "Asia_High_Sweep" etc.
    entry_zone_high: float
    entry_zone_low: float
    sl_price: float
    tp1_price: float
    sweep_event: SweepEvent
    confluence_score: int = 0
    label: str = ""


# ── Timezone Helper ───────────────────────────────────────
def to_bkk(ts) -> datetime:
    """แปลง timestamp เป็น Bangkok time"""
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.astimezone(BKK)
    return ts


def get_hour_bkk(ts) -> int:
    dt = to_bkk(ts)
    return dt.hour


def get_date_bkk(ts) -> str:
    dt = to_bkk(ts)
    return dt.strftime("%Y-%m-%d")


# ── PDH/PDL ───────────────────────────────────────────────
def calc_pdh_pdl(df: pd.DataFrame) -> Optional[DayLevel]:
    """
    หา Previous Day High/Low
    df ต้องมี index เป็น DatetimeIndex
    """
    if df.index.tzinfo is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")

    df_bkk = df.copy()
    df_bkk.index = df_bkk.index.tz_convert(BKK)
    df_bkk["date"] = df_bkk.index.strftime("%Y-%m-%d")

    dates = sorted(df_bkk["date"].unique())
    if len(dates) < 2:
        return None

    prev_date = dates[-2]
    prev_day  = df_bkk[df_bkk["date"] == prev_date]

    return DayLevel(
        pdh=float(prev_day["high"].max()),
        pdl=float(prev_day["low"].min()),
        date=prev_date,
    )


# ── Session H/L ───────────────────────────────────────────
def calc_session_levels(df: pd.DataFrame) -> dict[str, SessionLevel]:
    """หา High/Low ของแต่ละ Session ใน วันปัจจุบัน"""
    if df.index.tzinfo is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")

    df_bkk = df.copy()
    df_bkk.index = df_bkk.index.tz_convert(BKK)
    df_bkk["hour"] = df_bkk.index.hour
    df_bkk["date"] = df_bkk.index.strftime("%Y-%m-%d")

    today = df_bkk["date"].iloc[-1]
    today_df = df_bkk[df_bkk["date"] == today]

    sessions = {}

    for name, (h_start, h_end) in SESSION_WINDOWS.items():
        if h_end <= 24:
            mask = (today_df["hour"] >= h_start) & (today_df["hour"] < h_end)
        else:
            # NY cross midnight: 20-23 + 0-2
            mask = (today_df["hour"] >= h_start) | (today_df["hour"] < (h_end - 24))

        seg = today_df[mask]
        if seg.empty:
            continue

        sessions[name] = SessionLevel(
            name=name,
            high=float(seg["high"].max()),
            low=float(seg["low"].min()),
            start_idx=df.index.get_loc(seg.index[0])
            if seg.index[0] in df.index else 0,
            end_idx=df.index.get_loc(seg.index[-1])
            if seg.index[-1] in df.index else len(df) - 1,
        )

    return sessions


# ── Sweep Detection ───────────────────────────────────────
def detect_sweeps(
    df: pd.DataFrame,
    day_level: Optional[DayLevel],
    sessions: dict[str, SessionLevel],
) -> list[SweepEvent]:
    """
    ตรวจ Sweep events:
    - PDH/PDL sweep
    - Session H/L sweep
    """
    sweeps = []
    n = len(df)

    # รวม levels ที่จะเช็ค
    levels_to_check = []

    if day_level:
        levels_to_check.append(("PDH", day_level.pdh, "SELL"))
        levels_to_check.append(("PDL", day_level.pdl, "BUY"))

    for sess_name, sess in sessions.items():
        levels_to_check.append((f"{sess_name}_High", sess.high, "SELL"))
        levels_to_check.append((f"{sess_name}_Low",  sess.low,  "BUY"))

    # เช็คแต่ละ candle ล่าสุด (50 แท่งล่าสุด)
    check_range = range(max(0, n - 50), n)

    for i in check_range:
        candle = df.iloc[i]
        for level_name, level_price, direction in levels_to_check:
            if direction == "SELL":
                # High Sweep: high กวาดเกิน level แต่ close ต่ำกว่า
                if candle["high"] > level_price + SWEEP_PIPS:
                    closed_back = candle["close"] < level_price
                    ev = SweepEvent(
                        session_name=level_name,
                        sweep_type="High_Sweep",
                        swept_level=level_price,
                        sweep_price=candle["high"],
                        close_price=candle["close"],
                        closed_back=closed_back,
                        direction="SELL",
                        candle_idx=i,
                        label=f"🔴 Sweep {level_name} | {level_price:.2f} → close:{candle['close']:.2f}",
                    )
                    sweeps.append(ev)

            else:
                # Low Sweep: low กวาดต่ำกว่า level แต่ close สูงกว่า
                if candle["low"] < level_price - SWEEP_PIPS:
                    closed_back = candle["close"] > level_price
                    ev = SweepEvent(
                        session_name=level_name,
                        sweep_type="Low_Sweep",
                        swept_level=level_price,
                        sweep_price=candle["low"],
                        close_price=candle["close"],
                        closed_back=closed_back,
                        direction="BUY",
                        candle_idx=i,
                        label=f"🟢 Sweep {level_name} | {level_price:.2f} → close:{candle['close']:.2f}",
                    )
                    sweeps.append(ev)

    return sweeps


# ── Micro Signal Builder ──────────────────────────────────
def build_micro_signal(
    sweep: SweepEvent,
    df: pd.DataFrame,
) -> Optional[MicroSignal]:
    """
    สร้าง MicroSignal จาก SweepEvent ที่ closed_back = True
    """
    if not sweep.closed_back:
        return None

    candle = df.iloc[sweep.candle_idx]
    price  = sweep.close_price

    if sweep.direction == "SELL":
        # Entry zone = wick ที่กวาด
        entry_high = sweep.sweep_price
        entry_low  = sweep.swept_level
        sl         = sweep.sweep_price + 0.50
        tp1        = price - (sweep.swept_level - price) * 0.618
        score      = 5 if sweep.closed_back else 2
    else:
        entry_high = sweep.swept_level
        entry_low  = sweep.sweep_price
        sl         = sweep.sweep_price - 0.50
        tp1        = price + (price - sweep.swept_level) * 0.618
        score      = 5 if sweep.closed_back else 2

    return MicroSignal(
        direction=sweep.direction,
        trigger=f"{sweep.session_name}_{sweep.sweep_type}",
        entry_zone_high=entry_high,
        entry_zone_low=entry_low,
        sl_price=sl,
        tp1_price=tp1,
        sweep_event=sweep,
        confluence_score=score,
        label=f"🎯 Micro {sweep.direction} | {sweep.label}",
    )


# ── Main Engine ───────────────────────────────────────────
class MicroEngine:
    def __init__(self):
        self.day_level:  Optional[DayLevel]         = None
        self.sessions:   dict[str, SessionLevel]    = {}
        self.sweeps:     list[SweepEvent]           = []
        self.signals:    list[MicroSignal]          = []

    def update(self, df_15m: pd.DataFrame) -> list[MicroSignal]:
        """
        รับ df_15m → คืน list ของ MicroSignal
        เรียกทุก 15 นาที
        """
        self.day_level = calc_pdh_pdl(df_15m)
        self.sessions  = calc_session_levels(df_15m)
        self.sweeps    = detect_sweeps(df_15m, self.day_level, self.sessions)

        self.signals = []
        for sweep in self.sweeps:
            # เอาเฉพาะ sweep ที่เกิดใน 3 แท่งล่าสุด
            if sweep.candle_idx < len(df_15m) - 3:
                continue
            sig = build_micro_signal(sweep, df_15m)
            if sig:
                self.signals.append(sig)

        return self.signals

    def summary(self) -> str:
        lines = []
        if self.day_level:
            lines.append(
                f"📊 PDH:{self.day_level.pdh:.2f} | PDL:{self.day_level.pdl:.2f}"
            )
        for name, sess in self.sessions.items():
            lines.append(
                f"🕐 {name} H:{sess.high:.2f} L:{sess.low:.2f}"
            )
        if self.sweeps:
            lines.append(f"💥 Sweeps: {len(self.sweeps)}")
            for s in self.sweeps[-3:]:
                lines.append(f"   {s.label}")
        if self.signals:
            lines.append(f"🎯 Signals: {len(self.signals)}")
        return "\n".join(lines) if lines else "⏳ Micro Engine: No data"


# ── Singleton ──────────────────────────────────────────────
micro_engine = MicroEngine()


def run_micro(df_15m: pd.DataFrame) -> list[MicroSignal]:
    """Entry point สำหรับ signal_composer"""
    return micro_engine.update(df_15m)


def get_micro_summary() -> str:
    return micro_engine.summary()
