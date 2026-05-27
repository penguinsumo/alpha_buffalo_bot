"""
alphatrend_gate.py — Alpha Buffalo v5.2
AlphaTrend confirmation gate สำหรับ V4 zone check เท่านั้น
(V5 ใช้ Harmonic hard floor ของตัวเอง ไม่ผ่าน gate นี้)

Logic:
    AlphaTrend = trailing ATR stop + MFI/RSI momentum
    ถ้า H1 AT ยังสีเดียวกับ direction → zone ยัง valid
    ถ้า H1 AT flip → momentum อ่อน → block reentry

Author: Alpha Buffalo Team
"""

import pandas as pd
import numpy as np
from typing import Literal

# ─────────────────────────────────────────────
# Core AlphaTrend Calculation
# ─────────────────────────────────────────────

def calc_alphatrend(
    df: pd.DataFrame,
    coeff: float = 1.0,
    period: int = 14,
    use_rsi: bool = False,
) -> pd.DataFrame:
    """
    คำนวณ AlphaTrend line ตาม Kivanç Özbilgiç original formula

    Args:
        df        : OHLCV DataFrame (columns: open, high, low, close, volume)
        coeff     : ATR multiplier (default=1.0)
        period    : ATR + MFI/RSI period (default=14)
        use_rsi   : True = ใช้ RSI แทน MFI (กรณีไม่มี volume)

    Returns:
        df พร้อม columns เพิ่ม:
            at_line   : AlphaTrend line value
            at_color  : 'green' | 'red'
            at_signal : 'buy' | 'sell' | 'hold' (crossover)
    """
    df = df.copy()

    # ATR (Simple MA ตาม original)
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift(1)).abs(),
        (df["low"] - df["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(period).mean()

    # Momentum: MFI หรือ RSI
    if use_rsi or "volume" not in df.columns:
        # RSI
        delta = df["close"].diff()
        gain = delta.clip(lower=0).rolling(period).mean()
        loss = (-delta.clip(upper=0)).rolling(period).mean()
        rs = gain / loss.replace(0, np.nan)
        momentum_ok = (100 - 100 / (1 + rs)) >= 50
    else:
        # MFI (Money Flow Index)
        hlc3 = (df["high"] + df["low"] + df["close"]) / 3
        mf = hlc3 * df["volume"]
        pos_mf = mf.where(hlc3 > hlc3.shift(1), 0).rolling(period).sum()
        neg_mf = mf.where(hlc3 < hlc3.shift(1), 0).rolling(period).sum()
        mfi = 100 - (100 / (1 + pos_mf / neg_mf.replace(0, np.nan)))
        momentum_ok = mfi >= 50

    # AlphaTrend line (trailing)
    up_t = df["low"] - atr * coeff
    down_t = df["high"] + atr * coeff

    at = pd.Series(np.nan, index=df.index)
    for i in range(1, len(df)):
        prev = at.iloc[i - 1] if not np.isnan(at.iloc[i - 1]) else 0
        if momentum_ok.iloc[i]:
            at.iloc[i] = max(up_t.iloc[i], prev)
        else:
            at.iloc[i] = min(down_t.iloc[i], prev)

    df["at_line"] = at

    # Color: green ถ้า at_line กำลังขึ้น (เทียบกับ 2 แท่งก่อน)
    df["at_color"] = np.where(df["at_line"] > df["at_line"].shift(2), "green", "red")

    # Signal: crossover เท่านั้น (ไม่ใช่ทุกแท่ง)
    prev_color = df["at_color"].shift(1)
    df["at_signal"] = "hold"
    df.loc[(df["at_color"] == "green") & (prev_color == "red"), "at_signal"] = "buy"
    df.loc[(df["at_color"] == "red") & (prev_color == "green"), "at_signal"] = "sell"

    return df


# ─────────────────────────────────────────────
# Gate Function — ใช้ใน signal_engine.py
# ─────────────────────────────────────────────

def check_at_zone(
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    direction: Literal["BUY", "SELL"],
    mode: Literal["reentry", "asia_block", "cascade_bonus"],
    coeff: float = 1.0,
    period: int = 14,
) -> dict:
    """
    Gate หลักสำหรับ 3 use cases ใน Alpha Buffalo:

    1. reentry     → V4 reentry check (H1 AT ต้องสีเดียวกับ direction)
    2. asia_block  → H4 AT flip detection ระหว่าง Asia session
    3. cascade_bon → H4 + H1 AT สีเดียวกัน → bonus +1

    Args:
        df_h1     : H1 OHLCV (ต้องมีอย่างน้อย 50 แท่ง)
        df_h4     : H4 OHLCV (ต้องมีอย่างน้อย 30 แท่ง)
        direction : 'BUY' | 'SELL'
        mode      : use case
        coeff     : AT multiplier
        period    : AT period

    Returns:
        {
            "ok"          : bool,   # pass gate หรือไม่
            "bonus"       : int,    # 0 หรือ 1 (cascade_bonus mode)
            "h1_color"    : str,    # 'green' | 'red' | 'unknown'
            "h4_color"    : str,
            "h4_flipped"  : bool,   # True ถ้า H4 เพิ่ง flip ใน 2 แท่งล่าสุด
            "reason"      : str,    # อธิบาย decision
        }
    """
    result = {
        "ok": True,
        "bonus": 0,
        "h1_color": "unknown",
        "h4_color": "unknown",
        "h4_flipped": False,
        "reason": "",
    }

    try:
        h1 = calc_alphatrend(df_h1, coeff, period)
        h4 = calc_alphatrend(df_h4, coeff, period)

        # ดึง confirmed candle (iloc[-2] ไม่ใช่ live)
        h1_color = h1["at_color"].iloc[-2]
        h4_color = h4["at_color"].iloc[-2]
        h4_prev_color = h4["at_color"].iloc[-3]

        result["h1_color"] = h1_color
        result["h4_color"] = h4_color
        result["h4_flipped"] = h4_color != h4_prev_color

        # ─── Mode: reentry ───
        if mode == "reentry":
            expected = "green" if direction == "BUY" else "red"
            if h1_color != expected:
                result["ok"] = False
                result["reason"] = (
                    f"H1 AT={h1_color} ≠ {expected} → momentum อ่อน block reentry"
                )
            else:
                result["reason"] = f"H1 AT={h1_color} สอดคล้องกับ {direction} → reentry ok"

        # ─── Mode: asia_block ───
        elif mode == "asia_block":
            if result["h4_flipped"]:
                result["ok"] = False
                result["reason"] = (
                    f"H4 AT เพิ่ง flip {h4_prev_color}→{h4_color} → block Asia entry"
                )
            else:
                result["reason"] = f"H4 AT stable ({h4_color}) → Asia entry allowed"

        # ─── Mode: cascade_bonus ───
        elif mode == "cascade_bonus":
            expected = "green" if direction == "BUY" else "red"
            if h4_color == expected and h1_color == expected:
                result["bonus"] = 1
                result["reason"] = (
                    f"H4+H1 AT ทั้งคู่เป็น {h4_color} → cascade_bonus +1"
                )
            else:
                result["reason"] = (
                    f"H4={h4_color} H1={h1_color} ไม่ align → no bonus"
                )

    except Exception as e:
        result["ok"] = True  # fail-open ไม่ block ถ้า data มีปัญหา
        result["reason"] = f"AT gate error (fail-open): {e}"

    return result


# ─────────────────────────────────────────────
# Convenience wrapper — เรียกจาก signal_engine
# ─────────────────────────────────────────────

def get_at_confluence(
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    direction: Literal["BUY", "SELL"],
) -> dict:
    """
    รัน 3 modes พร้อมกันในครั้งเดียว
    ใช้สำหรับ debug หรือ logging ใน /signal/latest

    Returns:
        {
            "reentry_ok"    : bool,
            "asia_block"    : bool,   # True = ควร block
            "cascade_bonus" : int,    # 0 หรือ 1
            "details"       : dict,   # raw results ทั้ง 3
        }
    """
    r_reentry = check_at_zone(df_h1, df_h4, direction, "reentry")
    r_asia = check_at_zone(df_h1, df_h4, direction, "asia_block")
    r_cascade = check_at_zone(df_h1, df_h4, direction, "cascade_bonus")

    return {
        "reentry_ok": r_reentry["ok"],
        "asia_block": not r_asia["ok"],
        "cascade_bonus": r_cascade["bonus"],
        "details": {
            "reentry": r_reentry,
            "asia": r_asia,
            "cascade": r_cascade,
        },
    }
