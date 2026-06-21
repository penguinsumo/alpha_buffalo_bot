"""
signal_composer.py — Alpha Buffalo v5.4 (Updated: V4 + Sweep + Visual TP)
"""
import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from zoneinfo import ZoneInfo

# Engines
from kivanc_vsaob import run_kivanc, KivancSignal
from harmonic_detector import run_harmonic, PRZZone
from micro_engine import run_micro, MicroSignal

# Session
from session_clock import get_market_session_info, H4SessionTracker
from ASIA_TUNING_v5p3 import ASIAScalpTriggerGate, ASITuningManager

BKK = ZoneInfo("Asia/Bangkok")

@dataclass
class ComposedSignal:
    direction: str
    signal_type: str
    entry_price: float
    sl_price: float
    tp1_price: float
    tp2_price: float
    lot_multiplier: float
    basket_layer: int
    confluence_score: int
    sources: list[str] = field(default_factory=list)
    label: str = ""
    timestamp: str = ""
    turbo_boost: bool = False
    visual_sl: float = 0.0

def calc_bb(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> dict:
    close = df["close"]
    mid = close.rolling(period).mean().iloc[-1]
    s = close.rolling(period).std().iloc[-1]
    return {"upper": mid + std * s, "mid": mid, "lower": mid - std * s}

def calc_atr(df: pd.DataFrame, period: int = 10) -> float:
    high, low, close = df['high'], df['low'], df['close'].shift(1)
    tr = pd.concat([high-low, (high-close).abs(), (low-close).abs()], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def has_bull_sweep(df_15m) -> bool:
    """ตรวจจับ Bull Sweep: Low < Previous Low และ Close > Previous Low"""
    if len(df_15m) < 2:
        return False
    return float(df_15m['low'].iloc[-1]) < float(df_15m['low'].iloc[-2]) and \
           float(df_15m['close'].iloc[-1]) > float(df_15m['low'].iloc[-2])

def has_bear_sweep(df_15m) -> bool:
    """ตรวจจับ Bear Sweep: High > Previous High และ Close < Previous High"""
    if len(df_15m) < 2:
        return False
    return float(df_15m['high'].iloc[-1]) > float(df_15m['high'].iloc[-2]) and \
           float(df_15m['close'].iloc[-1]) < float(df_15m['high'].iloc[-2])

class SignalComposer:
    def __init__(self):
        self.last_signal: Optional[ComposedSignal] = None

    def compose(self, df_4h: pd.DataFrame, df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> Optional[ComposedSignal]:
        session_info = get_market_session_info()
        current_session = session_info["session"]
        if current_session == "CLOSED":
            return None

        current_price = float(df_15m["close"].iloc[-1])
        atr10_1h = calc_atr(df_1h, 10)
        atr10_15m = calc_atr(df_15m, 10)

        bb_1h = calc_bb(df_1h)
        bb_15m = calc_bb(df_15m)

        ema20_1h = float(df_1h['close'].ewm(span=20).mean().iloc[-1])
        ema50_1h = float(df_1h['close'].ewm(span=50).mean().iloc[-1])
        ema20_15m = float(df_15m['close'].ewm(span=20).mean().iloc[-1])
        ema50_15m = float(df_15m['close'].ewm(span=50).mean().iloc[-1])

        # ── BUY 1H Logic (V4 + Sweep + Visual TP) ──
        buy_ok = ema20_1h > ema50_1h and float(df_1h['low'].iloc[-1]) <= bb_1h['lower'] * 1.02
        bull_sweep_1h = has_bull_sweep(df_1h)
        if buy_ok and bull_sweep_1h:
            entry = current_price
            sl = entry - atr10_1h * 1.5
            # Visual TP: Fib 1.272
            swing_high_1h = float(df_1h['high'].rolling(100).max().iloc[-1])
            swing_low_1h = float(df_1h['low'].rolling(100).min().iloc[-1])
            diff_1h = swing_high_1h - swing_low_1h
            tp1 = swing_low_1h + diff_1h * 1.272 if diff_1h > 0 else entry * 1.01
            tp2 = tp1 * 1.01
            return ComposedSignal(
                direction="BUY", signal_type="V4_SWEEP",
                entry_price=entry, sl_price=sl,
                tp1_price=tp1, tp2_price=tp2,
                lot_multiplier=1.0, basket_layer=1,
                confluence_score=5,
                sources=["BB Touch", "Bull Sweep", "EMA Trend"],
                timestamp=datetime.now(BKK).strftime("%H:%M:%S"),
                label="BUY 1H",
                visual_sl=sl
            )

        # ── SELL 15m Logic (V4 + Sweep + Visual SL) ──
        sell_ok = ema20_15m < ema50_15m and float(df_15m['high'].iloc[-1]) >= bb_15m['upper'] * 0.98
        bear_sweep_15m = has_bear_sweep(df_15m)
        if sell_ok and bear_sweep_15m:
            entry = current_price
            sl = entry + atr10_15m * 1.5
            # Visual TP: Fib 0.72
            swing_high_15m = float(df_15m['high'].rolling(100).max().iloc[-1])
            swing_low_15m = float(df_15m['low'].rolling(100).min().iloc[-1])
            diff_15m = swing_high_15m - swing_low_15m
            tp1 = swing_high_15m - diff_15m * 0.72 if diff_15m > 0 else entry * 0.99
            tp2 = tp1 * 0.99
            # Visual SL: จะทำงานเมื่อแตะ Mid BB (trade_manager.py จัดการ)
            visual_sl = entry  # เตรียมไว้ให้ trade_manager ใช้
            return ComposedSignal(
                direction="SELL", signal_type="V4_SWEEP",
                entry_price=entry, sl_price=sl,
                tp1_price=tp1, tp2_price=tp2,
                lot_multiplier=1.0, basket_layer=1,
                confluence_score=5,
                sources=["BB Touch", "Bear Sweep", "EMA Trend"],
                timestamp=datetime.now(BKK).strftime("%H:%M:%S"),
                label="SELL 15M",
                visual_sl=visual_sl
            )

        return None

composer = SignalComposer()

def compose_signal(df_4h, df_1h, df_15m):
    return composer.compose(df_4h, df_1h, df_15m)
