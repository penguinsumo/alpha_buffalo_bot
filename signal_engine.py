"""
signal_engine.py — Alpha Buffalo v5 Cloud-Driven
Python คำนวณทุกอย่าง ส่ง JSON ครบให้ EA
BOS / MSS / PDH/PDL / Partial Levels / Harmonic PRZ
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta

BKK = timezone(timedelta(hours=7))

# ── Config ────────────────────────────────────────────────
SWEEP_LOOKBACK  = 32
BOS_LOOKBACK    = 5
PIVOT_N         = 3
FIBO_TOL        = 0.06
PRZ_BUFFER      = 3.0
BB_PERIOD       = 20
BB_STD          = 2.0
MSS_BUFFER      = 0.30
V4_MIN          = 4
V5_MIN          = 7


# ── Output Signal ─────────────────────────────────────────
@dataclass
class CloudSignal:
    """Signal JSON ครบ ส่งให้ EA execute"""
    action:      str    # OPEN / PARTIAL / CLOSE / KILL
    direction:   str    # BUY / SELL
    signal_type: str    # V4_SESSION / V5_SNIPER
    entry:       float
    sl:          float
    be_price:    float  # ราคา BE
    trail_from:  float  # ราคาเริ่ม trail
    tp_final:    float  # PDH/PDL หรือ PRZ
    partial:     list   # [{pct, price, reason}]
    pattern:     str    # Harmonic pattern name
    score:       int
    layer:       int
    session:     str
    timestamp:   str
    fallback_sl: float  # EA ใช้ถ้า internet ขาด
    fallback_tp: float


# ── BOS Detection ─────────────────────────────────────────
def detect_bos(df: pd.DataFrame, direction: str, n: int = BOS_LOOKBACK) -> bool:
    """Break of Structure — ราคาทะลุ Swing High/Low"""
    if len(df) < n + 2: return False
    if direction == "BUY":
        swing_high = df["high"].iloc[-n-1:-1].max()
        return float(df["high"].iloc[-1]) > swing_high
    else:
        swing_low = df["low"].iloc[-n-1:-1].min()
        return float(df["low"].iloc[-1]) < swing_low


# ── MSS Detection ─────────────────────────────────────────
def detect_mss(df: pd.DataFrame, direction: str) -> bool:
    """Market Structure Shift"""
    if len(df) < 5: return False
    h = df["high"].iloc[-5:].values
    l = df["low"].iloc[-5:].values
    c = df["close"].iloc[-5:].values

    if direction == "BUY":
        lower_low   = l[-3] < l[-4]
        higher_high = h[-1] > h[-2]
        close_up    = c[-1] > c[-2]
        return lower_low and higher_high and close_up
    else:
        higher_high = h[-3] > h[-4]
        lower_low   = l[-1] < l[-2]
        close_down  = c[-1] < c[-2]
        return higher_high and lower_low and close_down


# ── PDH/PDL ───────────────────────────────────────────────
def get_pdh_pdl(df_1h: pd.DataFrame) -> tuple:
    """Previous Day High/Low"""
    if df_1h.index.tzinfo is None:
        df_1h = df_1h.copy()
        df_1h.index = df_1h.index.tz_localize("UTC")
    df_bkk = df_1h.copy()
    df_bkk.index = df_bkk.index.tz_convert(BKK)
    df_bkk["date"] = df_bkk.index.strftime("%Y-%m-%d")
    dates = sorted(df_bkk["date"].unique())
    if len(dates) < 2:
        return None, None
    prev = df_bkk[df_bkk["date"] == dates[-2]]
    return float(prev["high"].max()), float(prev["low"].min())


# ── Bollinger Bands ───────────────────────────────────────
def get_bb(df: pd.DataFrame) -> dict:
    close = df["close"]
    mid   = close.rolling(BB_PERIOD).mean().iloc[-1]
    std   = close.rolling(BB_PERIOD).std().iloc[-1]
    return {
        "upper": float(mid + BB_STD * std),
        "mid":   float(mid),
        "lower": float(mid - BB_STD * std),
    }


# ── Pin Bar ───────────────────────────────────────────────
def detect_pinbar(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 2: return False
    c = df.iloc[-2]
    rng  = c["high"] - c["low"]
    if rng < 0.001: return False
    body      = abs(c["close"] - c["open"])
    wick_up   = c["high"] - max(c["close"], c["open"])
    wick_down = min(c["close"], c["open"]) - c["low"]
    if direction == "SELL":
        return (wick_up / rng > 0.60 and body / rng < 0.30)
    else:
        return (wick_down / rng > 0.60 and body / rng < 0.30)


# ── Harmonic Scanner ──────────────────────────────────────
PATTERNS = {
    "Bullish_Gartley":   {"dir":"BUY",  "p":1, "AB":(0.382,0.06), "CD":(0.786,0.06)},
    "Bullish_Bat":       {"dir":"BUY",  "p":1, "AB":(0.382,0.06), "CD":(0.886,0.06)},
    "Bullish_Butterfly": {"dir":"BUY",  "p":2, "AB":(0.382,0.06), "CD":(1.618,0.08)},
    "Bullish_Crab":      {"dir":"BUY",  "p":2, "AB":(0.500,0.06), "CD":(1.618,0.08)},
    "Bullish_ABCD":      {"dir":"BUY",  "p":1, "AB":(0.618,0.06), "CD":(1.272,0.06)},
    "Bullish_Cypher":    {"dir":"BUY",  "p":2, "AB":(0.382,0.06), "CD":(0.786,0.06)},
    "Bullish_DeepCrab":  {"dir":"BUY",  "p":3, "AB":(0.382,0.06), "CD":(2.618,0.10)},
    "Bearish_Gartley":   {"dir":"SELL", "p":1, "AB":(0.618,0.06), "CD":(0.786,0.06)},
    "Bearish_Bat":       {"dir":"SELL", "p":1, "AB":(0.500,0.06), "CD":(0.886,0.06)},
    "Bearish_Butterfly": {"dir":"SELL", "p":2, "AB":(0.618,0.06), "CD":(1.618,0.08)},
    "Bearish_Crab":      {"dir":"SELL", "p":2, "AB":(0.382,0.06), "CD":(1.618,0.08)},
    "Bearish_ABCD":      {"dir":"SELL", "p":1, "AB":(0.618,0.06), "CD":(1.272,0.06)},
    "Bearish_Cypher":    {"dir":"SELL", "p":2, "AB":(0.382,0.06), "CD":(0.786,0.06)},
    "Bearish_DeepCrab":  {"dir":"SELL", "p":3, "AB":(0.382,0.06), "CD":(2.618,0.10)},
}

def find_pivots(df: pd.DataFrame, n: int = PIVOT_N):
    highs, lows = [], []
    for i in range(n, len(df) - n):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        if all(df["high"].iloc[i-j] < h for j in range(1,n+1)) and \
           all(df["high"].iloc[i+j] < h for j in range(1,n+1)):
            highs.append((i, h, "H"))
        if all(df["low"].iloc[i-j] > l for j in range(1,n+1)) and \
           all(df["low"].iloc[i+j] > l for j in range(1,n+1)):
            lows.append((i, l, "L"))
    swings = sorted(highs + lows, key=lambda x: x[0])
    return swings

def scan_harmonic(df_1h: pd.DataFrame, df_4h: pd.DataFrame) -> list:
    results = []
    for df in [df_1h, df_4h]:
        swings = find_pivots(df)
        if len(swings) < 5: continue
        for i in range(len(swings) - 4):
            pts = swings[i:i+5]
            kinds = [p[2] for p in pts]
            if not all(kinds[j] != kinds[j+1] for j in range(4)):
                continue
            X,A,B,C,D = [p[1] for p in pts]
            XA = abs(A-X); AB = abs(B-A)
            BC = abs(C-B); CD = abs(D-C)
            if XA < 0.001 or AB < 0.001: continue
            r_AB = AB/XA
            r_CD = CD/XA

            for name, pat in PATTERNS.items():
                ab_ok = abs(r_AB - pat["AB"][0]) <= pat["AB"][1]
                cd_ok = abs(r_CD - pat["CD"][0]) <= pat["CD"][1]
                if ab_ok and cd_ok:
                    results.append({
                        "name":      name,
                        "direction": pat["dir"],
                        "priority":  pat["p"],
                        "prz_mid":   D,
                        "prz_high":  D + PRZ_BUFFER,
                        "prz_low":   D - PRZ_BUFFER,
                    })
    # dedup
    seen = set()
    final = []
    for r in results:
        key = f"{r['direction']}_{round(r['prz_mid'],1)}"
        if key not in seen:
            seen.add(key)
            final.append(r)
    final.sort(key=lambda x: x["priority"])
    return final


# ── Session ───────────────────────────────────────────────
def get_session(dt: datetime) -> str:
    h = dt.astimezone(timezone.utc).hour
    if 7  <= h < 13: return "London"
    if 13 <= h < 19: return "NY"
    return "Asia"


# ── VSA ───────────────────────────────────────────────────
def is_high_volume(df: pd.DataFrame, window: int = 50) -> bool:
    if "volume" not in df.columns: return False
    vol = df["volume"].iloc[-1]
    avg = df["volume"].iloc[-window-1:-1].mean()
    return vol >= avg * 1.8


# ── Main Signal Engine ────────────────────────────────────
def compute_signal(
    df_4h:  pd.DataFrame,
    df_1h:  pd.DataFrame,
    df_15m: pd.DataFrame,
) -> Optional[CloudSignal]:
    """
    คำนวณทุกอย่างบน Python
    คืน CloudSignal ครบพร้อมให้ EA execute
    """
    if len(df_15m) < 50: return None

    price   = float(df_15m["close"].iloc[-1])
    dt      = df_15m.index[-1]
    session = get_session(dt)

    # ── 1. Session Sweep ──────────────────────────────────
    window    = df_15m.tail(SWEEP_LOOKBACK)
    sess_high = float(window["high"].max())
    sess_low  = float(window["low"].min())
    curr_high = float(df_15m["high"].iloc[-1])
    curr_low  = float(df_15m["low"].iloc[-1])

    direction = ""
    score     = 0

    if curr_high > sess_high * 1.0005 and price < sess_high:
        direction = "SELL"; score += 5
    elif curr_low < sess_low * 0.9995 and price > sess_low:
        direction = "BUY";  score += 5
    if not direction: return None

    # ── 2. BOS ────────────────────────────────────────────
    bos = detect_bos(df_15m, direction)
    if bos: score += 2

    # ── 3. MSS ────────────────────────────────────────────
    mss = detect_mss(df_15m, direction)
    if mss: score += 2

    # ── 4. PDH/PDL ────────────────────────────────────────
    pdh, pdl = get_pdh_pdl(df_1h)
    if pdh and pdl:
        if direction=="SELL" and abs(price-pdh)/pdh < 0.003: score += 2
        if direction=="BUY"  and abs(price-pdl)/pdl < 0.003: score += 2

    # ── 5. Harmonic PRZ ───────────────────────────────────
    prz_list   = scan_harmonic(df_1h, df_4h)
    prz_match  = None
    prz_name   = ""
    prz_opposite = None

    for prz in prz_list:
        if prz["direction"] == direction:
            if prz["prz_low"] <= price <= prz["prz_high"]:
                prz_match = prz
                prz_name  = prz["name"]
                score    += (4 - prz["priority"])
                break

    for prz in prz_list:
        if prz["direction"] != direction:
            prz_opposite = prz
            break

    # ── 6. Pin Bar H1/H4 ──────────────────────────────────
    if prz_match:
        if detect_pinbar(df_1h, direction): score += 2
        if detect_pinbar(df_4h, direction): score += 3

    # ── 7. VSA ────────────────────────────────────────────
    if is_high_volume(df_15m): score += 2

    # ── Check threshold ───────────────────────────────────
    sig_type = "V5_SNIPER" if (score >= V5_MIN and prz_match) else "V4_SESSION"
    if score < V4_MIN: return None

    # ── 8. คำนวณ levels ───────────────────────────────────
    bb      = get_bb(df_15m)
    atr     = float((df_15m["high"] - df_15m["low"]).tail(14).mean())
    atr     = max(atr, 1.0)

    if direction == "BUY":
        sl        = round(price - atr * 1.0, 2)
        be_price  = round(price + 0.10, 2)
        trail_from= bb["mid"]
        tp_pdh    = pdh if pdh and pdh > price else price + atr * 3.0
        tp_prz    = prz_opposite["prz_mid"] if prz_opposite else 0
        tp_final  = round(tp_prz if tp_prz > price else tp_pdh, 2)

        partial = [
            {"pct": 50, "price": round(bb["upper"], 2), "reason": "BB_Upper"},
            {"pct": 30, "price": round(bb["mid"],   2), "reason": "BB_Mid"},
            {"pct": 20, "price": round(tp_final,    2), "reason": "PDH_or_PRZ"},
        ]
        fallback_tp = round(price + atr * 4.0, 2)

    else:  # SELL
        sl        = round(price + atr * 1.0, 2)
        be_price  = round(price - 0.10, 2)
        trail_from= bb["mid"]
        tp_pdl    = pdl if pdl and pdl < price else price - atr * 3.0
        tp_prz    = prz_opposite["prz_mid"] if prz_opposite else 0
        tp_final  = round(tp_prz if 0 < tp_prz < price else tp_pdl, 2)

        partial = [
            {"pct": 50, "price": round(bb["lower"], 2), "reason": "BB_Lower"},
            {"pct": 30, "price": round(bb["mid"],   2), "reason": "BB_Mid"},
            {"pct": 20, "price": round(tp_final,    2), "reason": "PDL_or_PRZ"},
        ]
        fallback_tp = round(price - atr * 4.0, 2)

    now = datetime.now(BKK).strftime("%Y-%m-%d %H:%M:%S")

    return CloudSignal(
        action      = "OPEN",
        direction   = direction,
        signal_type = sig_type,
        entry       = round(price, 2),
        sl          = sl,
        be_price    = be_price,
        trail_from  = round(trail_from, 2),
        tp_final    = tp_final,
        partial     = partial,
        pattern     = prz_name,
        score       = score,
        layer       = 1,
        session     = session,
        timestamp   = now,
        fallback_sl = sl,
        fallback_tp = fallback_tp,
    )


def signal_to_dict(sig: CloudSignal) -> dict:
    """แปลงเป็น JSON dict สำหรับส่งผ่าน API"""
    return {
        "action":      sig.action,
        "direction":   sig.direction,
        "signal_type": sig.signal_type,
        "entry":       sig.entry,
        "sl":          sig.sl,
        "be_price":    sig.be_price,
        "trail_from":  sig.trail_from,
        "tp_final":    sig.tp_final,
        "partial":     sig.partial,
        "pattern":     sig.pattern,
        "score":       sig.score,
        "layer":       sig.layer,
        "session":     sig.session,
        "timestamp":   sig.timestamp,
        "fallback_sl": sig.fallback_sl,
        "fallback_tp": sig.fallback_tp,
    }
