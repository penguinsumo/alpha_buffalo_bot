"""
micro_engine.py — Alpha Buffalo v5.2 (Sprint Clean)
Session H/L, PDH/PDL, Sweep Detection

Changes from v5.1:
  [FIX] detect_sweeps: ตัด live candle (n-1) ออก
  [FIX] SweepEvent: เพิ่ม event_id สำหรับ dedup ข้าม poll cycle
  [FIX] SL คำนวณจาก ATR แทนค่าคงที่ 0.50 USD
  [FIX] Singleton: เพิ่ม seen_events set เพื่อกัน sweep ซ้ำ
  [NOTE] micro_engine ไม่ return score อีกต่อไป
         score translation อยู่ใน score_manager.py (Bucket C)
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

BKK = timezone(timedelta(hours=7))

SESSION_WINDOWS = {
    "Asia":   (2,  9),
    "London": (14, 20),
    "NY":     (20, 26),
}

SWEEP_PIPS       = 0.30
ATR_PERIOD       = 14
ATR_SL_MULT      = 1.5   # SL = sweep extreme ± ATR * multiplier


# ── Data Classes ───────────────────────────────────────────
@dataclass
class DayLevel:
    pdh:  float
    pdl:  float
    date: str


@dataclass
class SessionLevel:
    name:      str
    high:      float
    low:       float
    start_idx: int
    end_idx:   int
    swept_high: bool = False
    swept_low:  bool = False


@dataclass
class SweepEvent:
    session_name: str
    sweep_type:   str       # "High_Sweep" | "Low_Sweep"
    swept_level:  float
    sweep_price:  float
    close_price:  float
    closed_back:  bool
    direction:    str       # "SELL" | "BUY"
    candle_idx:   int
    atr:          float = 0.0   # [NEW] ATR ณ จุดที่ sweep
    label:        str = ""

    @property
    def event_id(self) -> str:
        """[NEW] unique key สำหรับ dedup ข้าม poll"""
        return f"{self.session_name}_{self.sweep_type}_{self.swept_level:.2f}_{self.candle_idx}"


@dataclass
class MicroSignal:
    direction:        str
    trigger:          str
    entry_zone_high:  float
    entry_zone_low:   float
    sl_price:         float
    tp1_price:        float
    sweep_event:      SweepEvent
    # [NOTE] ไม่มี confluence_score แล้ว — อยู่ใน score_manager
    label: str = ""


# ── Helpers ────────────────────────────────────────────────
def to_bkk(ts) -> datetime:
    if isinstance(ts, pd.Timestamp):
        if ts.tzinfo is None:
            ts = ts.tz_localize("UTC")
        return ts.astimezone(BKK)
    return ts


def calc_atr(df: pd.DataFrame, period: int = ATR_PERIOD) -> float:
    """คำนวณ ATR แท่งล่าสุด (confirmed)"""
    if len(df) < period + 1:
        return 1.0  # fallback
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"]  - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-2])  # confirmed candle


# ── PDH/PDL ────────────────────────────────────────────────
def calc_pdh_pdl(df: pd.DataFrame) -> Optional[DayLevel]:
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
        pdh  = float(prev_day["high"].max()),
        pdl  = float(prev_day["low"].min()),
        date = prev_date,
    )


# ── Session H/L ────────────────────────────────────────────
def calc_session_levels(df: pd.DataFrame) -> dict[str, SessionLevel]:
    if df.index.tzinfo is None:
        df = df.copy()
        df.index = df.index.tz_localize("UTC")
    df_bkk = df.copy()
    df_bkk.index = df_bkk.index.tz_convert(BKK)
    df_bkk["hour"] = df_bkk.index.hour
    df_bkk["date"] = df_bkk.index.strftime("%Y-%m-%d")
    today    = df_bkk["date"].iloc[-1]
    today_df = df_bkk[df_bkk["date"] == today]
    sessions = {}
    for name, (h_start, h_end) in SESSION_WINDOWS.items():
        if h_end <= 24:
            mask = (today_df["hour"] >= h_start) & (today_df["hour"] < h_end)
        else:
            mask = (today_df["hour"] >= h_start) | (today_df["hour"] < (h_end - 24))
        seg = today_df[mask]
        if seg.empty:
            continue
        sessions[name] = SessionLevel(
            name      = name,
            high      = float(seg["high"].max()),
            low       = float(seg["low"].min()),
            start_idx = df.index.get_loc(seg.index[0])  if seg.index[0]  in df.index else 0,
            end_idx   = df.index.get_loc(seg.index[-1]) if seg.index[-1] in df.index else len(df) - 1,
        )
    return sessions


# ── Sweep Detection ────────────────────────────────────────
def detect_sweeps(
    df: pd.DataFrame,
    day_level: Optional[DayLevel],
    sessions:  dict[str, SessionLevel],
    seen_events: set,                   # [NEW] dedup set จาก MicroEngine
) -> list[SweepEvent]:

    sweeps = []
    # [FIX] ตัด live candle ออก — check เฉพาะ confirmed candles
    n = len(df) - 1
    if n < 3:
        return sweeps

    current_atr = calc_atr(df)

    levels_to_check = []
    if day_level:
        levels_to_check.append(("PDH", day_level.pdh, "SELL"))
        levels_to_check.append(("PDL", day_level.pdl, "BUY"))
    for sess_name, sess in sessions.items():
        levels_to_check.append((f"{sess_name}_High", sess.high, "SELL"))
        levels_to_check.append((f"{sess_name}_Low",  sess.low,  "BUY"))

    # check 50 confirmed candles ล่าสุด (ไม่รวม live)
    check_range = range(max(0, n - 50), n)

    for i in check_range:
        candle = df.iloc[i]
        for level_name, level_price, direction in levels_to_check:
            if direction == "SELL":
                if candle["high"] > level_price + SWEEP_PIPS:
                    closed_back = candle["close"] < level_price
                    ev = SweepEvent(
                        session_name = level_name,
                        sweep_type   = "High_Sweep",
                        swept_level  = level_price,
                        sweep_price  = float(candle["high"]),
                        close_price  = float(candle["close"]),
                        closed_back  = closed_back,
                        direction    = "SELL",
                        candle_idx   = i,
                        atr          = current_atr,
                        label = f"🔴 Sweep {level_name} | {level_price:.2f}",
                    )
                    # [FIX] dedup ด้วย event_id
                    if ev.event_id not in seen_events:
                        sweeps.append(ev)
            else:
                if candle["low"] < level_price - SWEEP_PIPS:
                    closed_back = candle["close"] > level_price
                    ev = SweepEvent(
                        session_name = level_name,
                        sweep_type   = "Low_Sweep",
                        swept_level  = level_price,
                        sweep_price  = float(candle["low"]),
                        close_price  = float(candle["close"]),
                        closed_back  = closed_back,
                        direction    = "BUY",
                        candle_idx   = i,
                        atr          = current_atr,
                        label = f"🟢 Sweep {level_name} | {level_price:.2f}",
                    )
                    if ev.event_id not in seen_events:
                        sweeps.append(ev)

    return sweeps


# ── Micro Signal Builder ───────────────────────────────────
def build_micro_signal(
    sweep: SweepEvent,
    df:    pd.DataFrame,
) -> Optional[MicroSignal]:
    if not sweep.closed_back:
        return None

    atr   = sweep.atr if sweep.atr > 0 else calc_atr(df)
    price = sweep.close_price

    if sweep.direction == "SELL":
        entry_high = sweep.sweep_price
        entry_low  = sweep.swept_level
        # [FIX] SL = ATR-based แทนค่าคงที่
        sl  = sweep.sweep_price + atr * ATR_SL_MULT
        tp1 = price - (sweep.swept_level - price) * 0.618
    else:
        entry_high = sweep.swept_level
        entry_low  = sweep.sweep_price
        sl  = sweep.sweep_price - atr * ATR_SL_MULT
        tp1 = price + (price - sweep.swept_level) * 0.618

    return MicroSignal(
        direction       = sweep.direction,
        trigger         = f"{sweep.session_name}_{sweep.sweep_type}",
        entry_zone_high = entry_high,
        entry_zone_low  = entry_low,
        sl_price        = sl,
        tp1_price       = tp1,
        sweep_event     = sweep,
        label = f"🎯 Micro {sweep.direction} | {sweep.label} | SL_ATR:{sl:.2f}",
    )


# ── Main Engine ────────────────────────────────────────────
class MicroEngine:
    def __init__(self):
        self.day_level:   Optional[DayLevel]      = None
        self.sessions:    dict[str, SessionLevel] = {}
        self.sweeps:      list[SweepEvent]        = []
        self.signals:     list[MicroSignal]       = []
        self._seen_events: set = set()            # [NEW] dedup store

    def update(self, df_15m: pd.DataFrame) -> list[MicroSignal]:
        self.day_level = calc_pdh_pdl(df_15m)
        self.sessions  = calc_session_levels(df_15m)
        # [FIX] ส่ง seen_events เข้าไปใน detect_sweeps
        self.sweeps    = detect_sweeps(
            df_15m, self.day_level, self.sessions, self._seen_events
        )
        self.signals = []
        for sweep in self.sweeps:
            # เฉพาะ sweep ที่เกิดใน 3 confirmed candles ล่าสุด
            if sweep.candle_idx < len(df_15m) - 4:   # -4 เพราะตัด live ไปแล้ว 1
                continue
            sig = build_micro_signal(sweep, df_15m)
            if sig:
                self.signals.append(sig)
                # บันทึก event_id ลง seen_events
                self._seen_events.add(sweep.event_id)

        # ล้าง seen_events ที่เก่าเกิน 200 entries
        if len(self._seen_events) > 200:
            self._seen_events = set(list(self._seen_events)[-100:])

        return self.signals

    def summary(self) -> str:
        lines = []
        if self.day_level:
            lines.append(f"📊 PDH:{self.day_level.pdh:.2f} | PDL:{self.day_level.pdl:.2f}")
        for name, sess in self.sessions.items():
            lines.append(f"🕐 {name} H:{sess.high:.2f} L:{sess.low:.2f}")
        if self.sweeps:
            lines.append(f"💥 Sweeps (new): {len(self.sweeps)}")
            for s in self.sweeps[-3:]:
                lines.append(f"   {s.label} closed_back={s.closed_back}")
        if self.signals:
            lines.append(f"🎯 Signals: {len(self.signals)}")
        return "\n".join(lines) if lines else "⏳ Micro Engine: No data"


# ── Singleton ──────────────────────────────────────────────
micro_engine = MicroEngine()


def run_micro(df_15m: pd.DataFrame) -> list[MicroSignal]:
    return micro_engine.update(df_15m)


def get_micro_summary() -> str:
    return micro_engine.summary()

def detect_spike_15m(df, atr_mult=1.5):
    """
    ตรวจจับ spike ใน 15M candle
    คืนค่า (spike_detected: bool, spike_type: str or None)
    """
    if len(df) < 2:
        return False, None
    # คำนวณ ATR แบบง่าย
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs()
    ], axis=1).max(axis=1)
    atr = tr.rolling(14).mean().iloc[-1]
    last = df.iloc[-1]
    body = abs(last['close'] - last['open'])
    candle_range = last['high'] - last['low']
    if candle_range > atr * atr_mult and body > candle_range * 0.6:
        if last['close'] > last['open']:
            return True, 'bullish'
        else:
            return True, 'bearish'
    return False, None
