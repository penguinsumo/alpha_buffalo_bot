"""
signal_engine_v2.py — Alpha Buffalo v5.1
Python Brain: รวม Cascade H4→H1→M15 + Harmonic PRZ + Context + Scenario

Flow:
1. Cascade Structure Analysis (H4→H1→M15)
2. VSA Selling/Buying Pressure
3. BOS/MSS Detection
4. Harmonic PRZ
5. PDH/PDL
6. Early Warning Stage 1/2
7. Context Engine (News+FG+DXY+COT)
8. Scenario Validation
9. Stage 3 Alert
10. Return CloudSignal
"""

import pandas as pd
import numpy as np
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

BKK = timezone(timedelta(hours=7))
SYMBOL         = os.getenv("TRADE_SYMBOL", "XAUUSD")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "")

# Thresholds
SWEEP_LOOKBACK = 32
BOS_LOOKBACK   = 5
PIVOT_N        = 3
PRZ_BUFFER     = 3.0
BB_PERIOD      = 20
BB_STD         = 2.0
V4_MIN         = 4
V5_MIN         = 8


@dataclass
class CloudSignal:
    action:         str
    direction:      str
    signal_type:    str
    entry:          float
    sl:             float
    be_price:       float
    trail_from:     float
    tp_final:       float
    partial:        list
    pattern:        str
    score:          int
    context_adj:    int
    final_score:    int
    layer:          int
    session:        str
    timestamp:      str
    fallback_sl:    float
    fallback_tp:    float
    st_h4:          str = ""
    st_1h:          str = ""
    st_15m:         str = ""
    visual_sl:      float = 0.0   # ปลายไส้ spike = SL จริงสำหรับ re-entry
    zone_valid:     bool  = True  # ยังอยู่ใน Harmonic/Kivanc zone ไหม
    reentry_ok:     bool  = False # VSA gate อนุญาต re-entry ไหม
    vsa_bias:       str   = ""    # BUY / SELL / NEUTRAL
    gps_confirmed:  bool  = False # Harmonic D ตรงกับ Session HL ไหม


# Session
def get_session(dt: datetime) -> str:
    h = dt.astimezone(timezone.utc).hour
    if 7  <= h < 13: return "London"
    if 13 <= h < 19: return "NY"
    return "Asia"


# EMA
def add_ema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    return df


# ── Cascade Structure Analysis ────────────────────────────
def analyze_structure(df: pd.DataFrame, tf_name: str = "") -> str:
    if df is None or len(df) < 20:
        return "INSUFFICIENT_DATA"

    df = add_ema(df)
    curr = df.iloc[-1]
    prev = df.iloc[-2]

    hh_hl = (curr["high"] > prev["high"]) and (curr["low"] > prev["low"])
    ll_lh = (curr["low"]  < prev["low"])  and (curr["high"] < prev["high"])

    avg_vol    = df["volume"].tail(20).mean() if "volume" in df.columns else 0
    vol_spike  = curr["volume"] > avg_vol * 1.5  if avg_vol > 0 else False
    vol_drop   = curr["volume"] < avg_vol * 0.8  if avg_vol > 0 else False
    is_bullish = curr["close"] > curr["open"]
    is_bearish = curr["close"] < curr["open"]

    resistance = float(df["high"].tail(20).max())
    near_res   = curr["close"] >= resistance * 0.998

    ema20 = float(curr["ema20"])
    ema50 = float(curr["ema50"])

    if vol_spike and is_bearish and near_res:
        return "SELLING_PRESSURE"
    if hh_hl and ema20 > ema50 and is_bullish:
        return "IMPULSE_UP"
    if ll_lh and ema20 < ema50:
        return "IMPULSE_DOWN"
    if ema20 > ema50 and is_bearish and vol_drop:
        return "PULLBACK_UP"
    if ema20 < ema50 and is_bullish and vol_drop:
        return "PULLBACK_DOWN"
    return "SIDEWAYS"


def compute_cascade(df_4h, df_1h, df_15m) -> dict:
    st_h4  = analyze_structure(df_4h,  "H4")
    st_1h  = analyze_structure(df_1h,  "H1")
    st_15m = analyze_structure(df_15m, "M15")

    direction = "NEUTRAL"
    score     = 0

    if st_h4 in ["IMPULSE_UP", "PULLBACK_UP"]:
        direction = "BUY"
        score    += 3
        if st_1h == "PULLBACK_UP":        score += 2
        elif st_1h == "IMPULSE_UP":       score += 3
        if st_15m == "IMPULSE_UP":        score += 3
        elif st_15m == "SELLING_PRESSURE": score -= 5

    elif st_h4 in ["IMPULSE_DOWN", "PULLBACK_DOWN"]:
        direction = "SELL"
        score    += 3
        if st_1h == "PULLBACK_DOWN":      score += 2
        elif st_1h == "IMPULSE_DOWN":     score += 3
        if st_15m == "IMPULSE_DOWN":      score += 3

    return {
        "direction": direction,
        "score":     max(0, score),
        "st_h4":     st_h4,
        "st_1h":     st_1h,
        "st_15m":    st_15m,
    }


# BOS / MSS
def detect_bos(df: pd.DataFrame, direction: str, n: int = BOS_LOOKBACK) -> bool:
    if len(df) < n + 2: return False
    if direction == "BUY":
        return float(df["high"].iloc[-1]) > float(df["high"].iloc[-n-1:-1].max())
    return float(df["low"].iloc[-1]) < float(df["low"].iloc[-n-1:-1].min())


def detect_mss(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 5: return False
    h = df["high"].iloc[-5:].values
    l = df["low"].iloc[-5:].values
    c = df["close"].iloc[-5:].values
    if direction == "BUY":
        return l[-3] < l[-4] and h[-1] > h[-2] and c[-1] > c[-2]
    return h[-3] > h[-4] and l[-1] < l[-2] and c[-1] < c[-2]


# PDH/PDL
def get_pdh_pdl(df_1h: pd.DataFrame):
    try:
        if df_1h.index.tzinfo is None:
            df_1h = df_1h.copy()
            df_1h.index = df_1h.index.tz_localize("UTC")
        df_bkk = df_1h.copy()
        df_bkk.index = df_bkk.index.tz_convert(BKK)
        df_bkk["date"] = df_bkk.index.strftime("%Y-%m-%d")
        dates = sorted(df_bkk["date"].unique())
        if len(dates) < 2: return None, None
        prev = df_bkk[df_bkk["date"] == dates[-2]]
        return float(prev["high"].max()), float(prev["low"].min())
    except Exception:
        return None, None


# BB
def get_bb(df: pd.DataFrame) -> dict:
    close = df["close"]
    mid   = close.rolling(BB_PERIOD).mean().iloc[-1]
    std   = close.rolling(BB_PERIOD).std().iloc[-1]
    return {"upper": float(mid+BB_STD*std), "mid": float(mid), "lower": float(mid-BB_STD*std)}


# Pin Bar
# ── Kivanc Swing Zone (Stable) ───────────────────────────
def get_kivanc_swing_zone(df_1h: pd.DataFrame, pivot_n: int = 10):
    """
    หา Swing High/Low ล่าสุดจาก Pivot Point จริงบน H1
    [FIX 2] Stable กว่า tail(50).max/min() เพราะ zone ไม่ขยับทุกแท่ง
    คืน (swing_high, swing_low) หรือ (None, None) ถ้าข้อมูลไม่พอ
    """
    if df_1h is None or len(df_1h) < pivot_n * 2 + 1:
        return None, None

    swing_high = None
    swing_low  = None

    highs = df_1h["high"].values
    lows  = df_1h["low"].values
    n     = len(df_1h)

    # วนหาจาก pivot ล่าสุดย้อนไป (ข้าม pivot_n แท่งสุดท้าย เพราะยังไม่ confirm)
    for i in range(n - pivot_n - 1, pivot_n - 1, -1):
        # Pivot High: high[i] สูงกว่าทุก pivot_n แท่งรอบข้าง
        if swing_high is None:
            if all(highs[i] > highs[i-j] for j in range(1, pivot_n+1)) and \
               all(highs[i] > highs[i+j] for j in range(1, pivot_n+1)):
                swing_high = float(highs[i])
        # Pivot Low: low[i] ต่ำกว่าทุก pivot_n แท่งรอบข้าง
        if swing_low is None:
            if all(lows[i] < lows[i-j] for j in range(1, pivot_n+1)) and \
               all(lows[i] < lows[i+j] for j in range(1, pivot_n+1)):
                swing_low = float(lows[i])
        if swing_high is not None and swing_low is not None:
            break

    return swing_high, swing_low


def detect_h1_spike_at_kivanc(
    df_1h: pd.DataFrame,
    direction: str,
    fib_zone_high: float,
    fib_zone_low: float,
    current_price: float = 0.0,
) -> dict:
    """
    H1 Spike ปลายไส้แตะ Kivanc Golden Zone
    [FIX 1] ใช้ iloc[-2] = แท่ง H1 ที่ปิดสมบูรณ์แล้ว (ไม่ใช่แท่งที่กำลังวิ่ง)
    [FIX 3] TP Guard: ถ้า body เล็ก (Doji) fallback ใช้ ATR
    คืน {"found": bool, "sl": float, "tp1": float}
    """
    if df_1h is None or len(df_1h) < 3:
        return {"found": False, "sl": 0, "tp1": 0}

    # [FIX 1] iloc[-2] = แท่งที่ปิดแล้ว (confirmed), ไม่ใช่ iloc[-1] ที่ยังวิ่งอยู่
    c = df_1h.iloc[-2]
    rng = c["high"] - c["low"]
    if rng < 0.001:
        return {"found": False, "sl": 0, "tp1": 0}

    wick_up   = (c["high"] - max(c["close"], c["open"])) / rng
    wick_down = (min(c["close"], c["open"]) - c["low"])   / rng

    # ATR fallback สำหรับ TP Guard (ใช้ 14 แท่งล่าสุด)
    atr_h1 = float((df_1h["high"] - df_1h["low"]).tail(14).mean())
    ref_price = current_price if current_price > 0 else float(df_1h["close"].iloc[-1])

    if direction == "BUY":
        spike     = wick_down > 0.60
        at_kivanc = fib_zone_low <= c["low"] <= fib_zone_high
        if spike and at_kivanc:
            tp1_raw = round(min(c["open"], c["close"]), 2)
            # [FIX 3] TP Guard: tp1 ต้องสูงกว่า entry จริง
            tp1 = tp1_raw if tp1_raw > ref_price else round(ref_price + atr_h1 * 1.5, 2)
            return {
                "found": True,
                "sl":    round(c["low"]  - 0.30, 2),
                "tp1":   tp1,
            }
    else:
        spike     = wick_up > 0.60
        at_kivanc = fib_zone_low <= c["high"] <= fib_zone_high
        if spike and at_kivanc:
            tp1_raw = round(max(c["open"], c["close"]), 2)
            # [FIX 3] TP Guard: tp1 ต้องต่ำกว่า entry จริง
            tp1 = tp1_raw if tp1_raw < ref_price else round(ref_price - atr_h1 * 1.5, 2)
            return {
                "found": True,
                "sl":    round(c["high"] + 0.30, 2),
                "tp1":   tp1,
            }

    return {"found": False, "sl": 0, "tp1": 0}


def detect_pinbar(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 2: return False
    c = df.iloc[-2]
    rng = c["high"] - c["low"]
    if rng < 0.001: return False
    body      = abs(c["close"] - c["open"])
    wick_up   = c["high"] - max(c["close"], c["open"])
    wick_down = min(c["close"], c["open"]) - c["low"]
    if direction == "SELL": return wick_up/rng > 0.60 and body/rng < 0.30
    return wick_down/rng > 0.60 and body/rng < 0.30


# VSA
def is_high_volume(df: pd.DataFrame, window: int = 50) -> bool:
    if "volume" not in df.columns: return False
    vol = df["volume"].iloc[-1]
    avg = df["volume"].iloc[-window-1:-1].mean()
    return float(vol) >= float(avg) * 1.8 if avg > 0 else False


# Harmonic
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
    swings = []
    for i in range(n, len(df)-n):
        h = df["high"].iloc[i]; l = df["low"].iloc[i]
        if all(df["high"].iloc[i-j] < h for j in range(1,n+1)) and            all(df["high"].iloc[i+j] < h for j in range(1,n+1)):
            swings.append((i, float(h), "H"))
        elif all(df["low"].iloc[i-j] > l for j in range(1,n+1)) and              all(df["low"].iloc[i+j] > l for j in range(1,n+1)):
            swings.append((i, float(l), "L"))
    return swings

def scan_harmonic(df_1h, df_4h) -> list:
    results = []
    for df in [df_1h, df_4h]:
        swings = find_pivots(df)
        if len(swings) < 5: continue
        for i in range(len(swings)-4):
            pts   = swings[i:i+5]
            kinds = [p[2] for p in pts]
            if not all(kinds[j] != kinds[j+1] for j in range(4)): continue
            X,A,B,C,D = [p[1] for p in pts]
            XA = abs(A-X); AB = abs(B-A)
            if XA < 0.001 or AB < 0.001: continue
            r_AB = AB/XA; r_CD = abs(D-C)/XA
            for name, pat in PATTERNS.items():
                if abs(r_AB-pat["AB"][0]) <= pat["AB"][1] and                    abs(r_CD-pat["CD"][0]) <= pat["CD"][1]:
                    results.append({
                        "name":name,"direction":pat["dir"],"priority":pat["p"],
                        "prz_mid":D,"prz_high":D+PRZ_BUFFER,"prz_low":D-PRZ_BUFFER,
                    })
    seen=set(); final=[]
    for r in results:
        key=r["direction"]+"_"+str(round(r["prz_mid"],1))
        if key not in seen: seen.add(key); final.append(r)
    return sorted(final, key=lambda x: x["priority"])


# ── Reversal Zone Detection (Counter-trend) ──────────────
def get_stoch(df, k_period=14, d_period=3):
    """Stochastic %K/%D — ตรวจ Oversold/Overbought บน H1"""
    if len(df) < k_period + d_period:
        return {"k": 50.0, "d": 50.0, "cross_up": False, "cross_down": False}
    low_min  = df["low"].rolling(k_period).min()
    high_max = df["high"].rolling(k_period).max()
    denom    = (high_max - low_min).replace(0, float("nan"))
    k = ((df["close"] - low_min) / denom * 100).fillna(50)
    d = k.rolling(d_period).mean().fillna(50)
    k_cur, k_prev = float(k.iloc[-1]), float(k.iloc[-2])
    d_cur, d_prev = float(d.iloc[-1]), float(d.iloc[-2])
    return {
        "k":          k_cur,
        "d":          d_cur,
        "cross_up":   k_prev < d_prev and k_cur > d_cur,
        "cross_down": k_prev > d_prev and k_cur < d_cur,
    }


def check_bb_extreme(df, price, direction):
    """ราคาแตะ BB Lower (BUY) หรือ BB Upper (SELL)"""
    bb = get_bb(df)
    return price <= bb["lower"] if direction == "BUY" else price >= bb["upper"]


def check_reversal_zone(df_1h, df_15m, direction, price, fib_zone, spike_found, bos_confirmed):
    """
    Counter-trend Reversal Scenario:
      Stage 1: BB Lower/Upper แตะ
      Stage 2: เข้า Kivanc Zone + H1 Spike
      Stage 3: M15 BOS ยืนยัน → เช็ค H1 Stoch → override cascade=0
    คืน {"override": bool, "cascade_bonus": int, "stage": int, "reason": str}
    """
    result = {"override": False, "cascade_bonus": 0, "stage": 0, "reason": ""}

    # Stage 1: BB Extreme
    if not check_bb_extreme(df_15m, price, direction):
        return result
    result["stage"] = 1

    # Stage 2: อยู่ใน Kivanc Zone + H1 Spike ยืนยัน
    in_zone = fib_zone["prz_low"] <= price <= fib_zone["prz_high"]
    if not (in_zone and spike_found):
        result["reason"] = "Stage1 only: BB extreme — รอ Spike ใน Kivanc zone"
        return result
    result["stage"] = 2

    # Stage 3: BOS M15 ยืนยัน
    if not bos_confirmed:
        result["reason"] = "Stage2: Spike+Zone — รอ M15 BOS"
        return result
    result["stage"] = 3

    # ตรวจ H1 Stoch หลัง BOS
    stoch = get_stoch(df_1h)
    if direction == "BUY":
        stoch_ok = stoch["k"] < 30 and stoch["cross_up"]
    else:
        stoch_ok = stoch["k"] > 70 and stoch["cross_down"]

    if stoch_ok:
        result["override"]      = True
        result["cascade_bonus"] = 4
        result["reason"]        = f"Reversal Full: BB+Zone+Spike+BOS+Stoch K={stoch['k']:.1f}"
    else:
        result["override"]      = True
        result["cascade_bonus"] = 2
        result["reason"]        = f"Reversal Partial: BB+Zone+Spike+BOS (Stoch K={stoch['k']:.1f} ยังไม่ cross)"

    return result



# Context
def get_context_adj(direction: str, score: int) -> tuple:
    total_adj=0; reasons=[]
    for plugin, func_name, kwargs in [
        ("plugin_news",       "check_news_filter",  {}),
        ("plugin_fear_greed", "get_fg_score_adj",   {"direction":direction}),
        ("plugin_dxy",        "get_dxy_score_adj",  {"direction":direction,"api_key":TWELVE_API_KEY}),
        ("plugin_cot",        "get_cot_score_adj",  {"direction":direction}),
    ]:
        try:
            mod  = __import__(plugin)
            func = getattr(mod, func_name)
            res  = func(**kwargs)
            adj  = res.get("score_adj", 0)
            total_adj += adj
            reasons.append(res.get("reason",""))
            if plugin == "plugin_news" and not res.get("safe", True):
                return total_adj, True, res.get("reason","News blocked")
        except Exception as e:
            reasons.append(plugin + ": error")
    return total_adj, False, " | ".join(reasons)


# ── [NEW 1] Harmonic D + Basket HL GPS Matching ──────────
def match_harmonic_gps(prz_list: list, sess_high: float, sess_low: float,
                        hl_buffer: float = 3.0) -> dict:
    """
    GPS: ตรวจว่า Harmonic Zone D ตรงกับ Session H/L (Liquidity Zone) ไหม
    ถ้าใช่ = High-priority reversal zone — ราคามาถึงเพื่อกวาด SL แล้วกลับ
    คืน {"gps_confirmed": bool, "direction": str, "prz": dict, "reason": str}
    """
    for prz in prz_list:
        d_mid = prz["prz_mid"]
        # Zone D ใกล้ Session Low = BUY reversal zone
        if prz["direction"] == "BUY":
            if abs(d_mid - sess_low) <= hl_buffer:
                return {
                    "gps_confirmed": True,
                    "direction":     "BUY",
                    "prz":           prz,
                    "reason":        f"GPS: {prz['name']} Zone D ({d_mid:.1f}) ≈ Session Low ({sess_low:.1f})",
                }
        # Zone D ใกล้ Session High = SELL reversal zone
        elif prz["direction"] == "SELL":
            if abs(d_mid - sess_high) <= hl_buffer:
                return {
                    "gps_confirmed": True,
                    "direction":     "SELL",
                    "prz":           prz,
                    "reason":        f"GPS: {prz['name']} Zone D ({d_mid:.1f}) ≈ Session High ({sess_high:.1f})",
                }
    return {"gps_confirmed": False, "direction": "", "prz": None, "reason": "No GPS match"}


# ── [NEW 2] Pattern Failure Protocol ─────────────────────
# State เก็บ reversal setup ที่กำลัง monitor อยู่
_reversal_monitor: dict = {
    "active":     False,
    "direction":  "",
    "price_zone": (0.0, 0.0),
    "bars_waited": 0,
    "max_bars":   8,          # M15 × 8 = 2 ชั่วโมง ถ้าไม่เกิด BOS = Pattern failed
}

def update_pattern_failure(price: float, bos_confirmed: bool, direction: str,
                            fib_zone: dict) -> dict:
    """
    Monitor ว่า reversal setup ที่เปิดไว้ยัง valid ไหม
    ถ้าเกิน max_bars แล้ว BOS ยังไม่เกิด → Pattern failed → invalidate
    คืน {"valid": bool, "failed": bool, "reason": str}
    """
    global _reversal_monitor
    in_zone = fib_zone["prz_low"] <= price <= fib_zone["prz_high"]

    # เริ่ม monitor ใหม่
    if in_zone and not _reversal_monitor["active"]:
        _reversal_monitor.update({
            "active":      True,
            "direction":   direction,
            "price_zone":  (fib_zone["prz_low"], fib_zone["prz_high"]),
            "bars_waited": 0,
        })
        return {"valid": True, "failed": False, "reason": "Monitor started"}

    # ถ้า active อยู่
    if _reversal_monitor["active"]:
        # BOS เกิดแล้ว = success → reset
        if bos_confirmed:
            _reversal_monitor["active"] = False
            return {"valid": True, "failed": False, "reason": "BOS confirmed — Pattern valid"}

        # ออกนอก zone → reset
        if not in_zone:
            _reversal_monitor["active"] = False
            return {"valid": False, "failed": True, "reason": "Price left zone — Pattern invalidated"}

        # นับ bars
        _reversal_monitor["bars_waited"] += 1
        waited = _reversal_monitor["bars_waited"]
        max_b  = _reversal_monitor["max_bars"]

        if waited >= max_b:
            _reversal_monitor["active"] = False
            return {
                "valid":  False,
                "failed": True,
                "reason": f"Pattern Failure: {waited} bars in zone, no BOS → Extended D likely",
            }
        return {
            "valid":  True,
            "failed": False,
            "reason": f"Waiting BOS: {waited}/{max_b} bars",
        }

    return {"valid": True, "failed": False, "reason": "Not monitoring"}


# ── [NEW 3] Volume-Confirmed H1 Spike ────────────────────
def score_spike_with_volume(spike: dict, df_1h: pd.DataFrame) -> dict:
    """
    ปรับ score ของ Spike ตาม Volume:
    - Spike + Volume spike (>1.5x avg) = full score +4
    - Spike + No volume              = half score +2
    - No spike                       = 0
    คืน {"score_add": int, "volume_confirmed": bool}
    """
    if not spike["found"]:
        return {"score_add": 0, "volume_confirmed": False}

    if "volume" not in df_1h.columns or len(df_1h) < 20:
        # ไม่มีข้อมูล volume → ให้ full score ไปก่อน
        return {"score_add": 4, "volume_confirmed": False}

    # ใช้ iloc[-2] ตรงกับ confirmed candle ใน detect_h1_spike_at_kivanc
    vol_cur = float(df_1h["volume"].iloc[-2])
    vol_avg = float(df_1h["volume"].iloc[-16:-2].mean())  # avg 14 แท่งก่อนหน้า

    if vol_avg > 0 and vol_cur >= vol_avg * 1.5:
        return {"score_add": 4, "volume_confirmed": True}
    else:
        return {"score_add": 2, "volume_confirmed": False}



# Scenario
def validate_scenario(direction, signal_type, score, pattern,
                      df_15m, buy_layers=0, sell_layers=0, spread=0.0) -> tuple:
    if direction=="BUY"  and buy_layers  >= 2: return False, "BUY layers full"
    if direction=="SELL" and sell_layers >= 2: return False, "SELL layers full"
    if spread > 0.50: return False, "Spread too wide"
    if signal_type=="V5_SNIPER" and not pattern: return False, "V5 needs pattern"
    if df_15m is not None and len(df_15m) >= 14:
        atr_c = float((df_15m["high"]-df_15m["low"]).iloc[-1])
        atr_a = float((df_15m["high"]-df_15m["low"]).tail(14).mean())
        if atr_a > 0 and atr_c > atr_a * 3.0: return False, "Volatility too high"
    return True, "OK"


# ── Main Signal Engine ────────────────────────────────────
def compute_signal(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    buy_layers: int = 0,
    sell_layers: int = 0,
    spread: float = 0.0,
) -> Optional[CloudSignal]:

    if len(df_15m) < 50: return None
    price   = float(df_15m["close"].iloc[-1])
    dt      = df_15m.index[-1]
    session = get_session(dt if hasattr(dt,"hour") else datetime.now(timezone.utc))

    # Step 1: Cascade Structure
    cascade   = compute_cascade(df_4h, df_1h, df_15m)
    direction = cascade["direction"]
    score     = cascade["score"]
    cascade_neutral = direction == "NEUTRAL"
    # ถ้า cascade NEUTRAL → ยังไม่ return ทันที รอตรวจ Reversal Zone ก่อน
    if cascade_neutral:
        direction = None   # จะกำหนดใหม่ใน reversal check
    elif score < 2:
        return None

    # Step 2: Early Warning Stage 1
    if direction:
        try:
            from early_warning import check_vsa_forming
            check_vsa_forming(df_15m, direction, SYMBOL, session)
        except Exception: pass

    # Step 3: BOS / MSS
    bos_buy  = detect_bos(df_15m, "BUY")
    bos_sell = detect_bos(df_15m, "SELL")
    if direction == "BUY"  and bos_buy:  score += 2
    if direction == "SELL" and bos_sell: score += 2
    if direction and detect_mss(df_15m, direction): score += 1
    # สำหรับ Reversal: ดูว่า BOS ฝั่งไหน confirm
    bos_confirmed_buy  = bos_buy
    bos_confirmed_sell = bos_sell

    # Step 4: Early Warning Stage 2
    try:
        from early_warning import check_bos_confirmed
        check_bos_confirmed(df_15m, direction, SYMBOL, score, "")
    except Exception: pass

    # Step 5: PDH/PDL
    pdh, pdl = get_pdh_pdl(df_1h)
    if pdh and pdl:
        if direction=="SELL" and abs(price-pdh)/pdh < 0.003: score += 2
        if direction=="BUY"  and abs(price-pdl)/pdl < 0.003: score += 2

    # Step 6: Session Sweep confirm
    window    = df_15m.tail(SWEEP_LOOKBACK)
    sess_high = float(window["high"].max())
    sess_low  = float(window["low"].min())
    curr_high = float(df_15m["high"].iloc[-1])
    curr_low  = float(df_15m["low"].iloc[-1])
    if direction=="SELL" and curr_high > sess_high*1.0005 and price < sess_high: score += 3
    if direction=="BUY"  and curr_low  < sess_low*0.9995  and price > sess_low:  score += 3

    # Step 7: Harmonic PRZ + GPS Matching
    prz_list     = scan_harmonic(df_1h, df_4h)
    prz_match    = None
    prz_name     = ""
    prz_opposite = None

    # [NEW 1] GPS: ตรวจ Harmonic Zone D vs Session H/L ล่วงหน้า
    gps = match_harmonic_gps(prz_list, sess_high, sess_low, hl_buffer=3.0)
    if gps["gps_confirmed"]:
        print("🗺️  " + gps["reason"])
        # ถ้า cascade NEUTRAL แต่ GPS confirmed → กำหนด direction ล่วงหน้า
        if cascade_neutral and direction is None:
            direction = gps["direction"]
            score    += 3   # GPS bonus: Harmonic D + HL confirmed

    # หา PRZ ที่ราคาอยู่ใน zone ตอนนี้
    for prz in prz_list:
        if prz["direction"]==direction and prz["prz_low"]<=price<=prz["prz_high"]:
            prz_match=prz; prz_name=prz["name"]; score+=(4-prz["priority"]); break
    for prz in prz_list:
        if prz["direction"]!=direction: prz_opposite=prz; break

    # Step 7.5: H1 Spike at Kivanc
    # [FIX 2] Kivanc Zone — ใช้ Swing Pivot จริง (Stable) แทน rolling tail(50)
    fib_zone = None
    if prz_match:
        fib_zone = prz_match
    else:
        # หา Swing High/Low จาก Pivot (lookback 10 แท่ง H1 แต่ละข้าง)
        swing_high, swing_low = get_kivanc_swing_zone(df_1h, pivot_n=10)
        if swing_high is not None and swing_low is not None:
            h1_rng = swing_high - swing_low
            if direction == "BUY":
                fib_lo = swing_low  + h1_rng * 0.618
                fib_hi = swing_low  + h1_rng * 0.786
            else:
                fib_lo = swing_high - h1_rng * 0.786
                fib_hi = swing_high - h1_rng * 0.618
            fib_zone = {"prz_low": fib_lo, "prz_high": fib_hi}
        else:
            # fallback: ถ้าหา pivot ไม่เจอ (ข้อมูลน้อย) ใช้ tail(50)
            h1_high = float(df_1h["high"].tail(50).max())
            h1_low  = float(df_1h["low"].tail(50).min())
            h1_rng  = h1_high - h1_low
            if direction == "BUY":
                fib_lo = h1_low + h1_rng * 0.618
                fib_hi = h1_low + h1_rng * 0.786
            else:
                fib_lo = h1_high - h1_rng * 0.786
                fib_hi = h1_high - h1_rng * 0.618
            fib_zone = {"prz_low": fib_lo, "prz_high": fib_hi}

    # ตรวจ Spike ทั้ง 2 ฝั่ง (รองรับ Reversal ที่ cascade=NEUTRAL)
    spike_buy  = detect_h1_spike_at_kivanc(df_1h, "BUY",
                     fib_zone["prz_high"], fib_zone["prz_low"], price)
    spike_sell = detect_h1_spike_at_kivanc(df_1h, "SELL",
                     fib_zone["prz_high"], fib_zone["prz_low"], price)
    spike = spike_buy if direction == "BUY" else spike_sell if direction == "SELL" else {"found": False, "sl": 0, "tp1": 0}

    # [NEW 3] Volume-Confirmed Spike Score
    spike_vol = score_spike_with_volume(spike if direction else
                (spike_buy if bos_confirmed_buy else spike_sell), df_1h)
    if spike["found"] and direction:
        score += spike_vol["score_add"]
        vol_tag = "✅ Vol confirmed" if spike_vol["volume_confirmed"] else "⚠️ No vol"
        print(f"🎯 H1 Spike at Kivanc: {direction} "
              f"SL={spike['sl']} TP1={spike['tp1']} | {vol_tag} (+{spike_vol['score_add']})")

    # ── Reversal Zone Check (Counter-trend) ──────────────
    # ทำงานเมื่อ cascade=NEUTRAL หรือ cascade ขัดแย้งกับ Zone
    reversal = {"override": False, "cascade_bonus": 0, "stage": 0, "reason": ""}

    for rev_dir in (["BUY", "SELL"] if cascade_neutral else []):
        sp = spike_buy if rev_dir == "BUY" else spike_sell
        bos_ok = bos_confirmed_buy if rev_dir == "BUY" else bos_confirmed_sell
        rev = check_reversal_zone(
            df_1h, df_15m, rev_dir, price, fib_zone,
            spike_found=sp["found"], bos_confirmed=bos_ok,
        )
        if rev["override"]:
            direction = rev_dir
            score    += rev["cascade_bonus"]
            reversal  = rev
            spike     = sp
            print("🔄 " + rev["reason"])
            break

    if cascade_neutral and not reversal["override"]:
        # Reversal ยังไม่ครบเงื่อนไข → รายงาน stage แล้ว return
        if reversal["stage"] > 0:
            print("⏳ Reversal " + reversal["reason"])
        return None

    # [NEW 2] Pattern Failure Protocol
    # ตรวจว่า reversal setup ที่ monitor อยู่ยัง valid ไหม
    if reversal["override"] or (prz_match and direction):
        _bos_ok = bos_confirmed_buy if direction == "BUY" else bos_confirmed_sell
        pf = update_pattern_failure(price, _bos_ok, direction, fib_zone)
        if pf["failed"]:
            print("❌ " + pf["reason"])
            return None
        if not pf["valid"]:
            print("⏳ Pattern Monitor: " + pf["reason"])
            return None

    # Step 8: Pin Bar
    if prz_match:
        if detect_pinbar(df_1h, direction): score += 2
        if detect_pinbar(df_4h, direction): score += 3

    # Step 9: VSA Volume
    if is_high_volume(df_15m): score += 2

    if score < V4_MIN: return None

    # BB Direction Filter — ห้าม BUY ถ้า BB ชี้ลง
    # ยกเว้น: Reversal mode (BB extreme คือ เงื่อนไขที่ 1 แล้ว)
    bb = get_bb(df_15m)
    if not reversal["override"]:
        if direction == "BUY"  and bb["upper"] < price:
            print("BB Filter: BUY blocked — BB bearish")
            return None
        if direction == "SELL" and bb["lower"] > price:
            print("BB Filter: SELL blocked — BB bullish")
            return None

    # Step 10: Context Engine
    ctx_adj, blocked, ctx_reason = get_context_adj(direction, score)
    if blocked:
        print("Context blocked: " + ctx_reason)
        return None
    final_score = max(0, score + ctx_adj)
    if final_score < V4_MIN: return None

    sig_type = "V5_SNIPER" if (final_score>=V5_MIN and prz_match) else "V4_SESSION"

    # Step 11: Scenario Validation
    valid, val_reason = validate_scenario(
        direction, sig_type, final_score, prz_name,
        df_15m, buy_layers, sell_layers, spread)
    if not valid:
        print("Scenario blocked: " + val_reason)
        return None

    # Step 12: Build CloudSignal
    bb  = get_bb(df_15m)
    atr = max(float((df_15m["high"]-df_15m["low"]).tail(14).mean()), 1.0)

    if direction=="BUY":
        sl          = round(spike["sl"] if spike["found"] and reversal["override"] else price - atr*1.0, 2)
        be_price    = round(price + 0.10, 2)
        tp_main     = prz_opposite["prz_mid"] if prz_opposite and prz_opposite["prz_mid"]>price else (pdh if pdh and pdh>price else price+atr*3.0)
        tp_final    = round(max(tp_main, price+atr*1.5), 2)
        tp1_price   = round(max(bb["upper"], price+atr*0.5), 2)
        tp2_price   = round(max(bb["mid"],   price+atr*1.0), 2)
        fallback_tp = round(price+atr*4.0, 2)
        partial  = [
            {"pct":50,"price":tp1_price,"reason":"BB_Upper"},
            {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
            {"pct":20,"price":tp_final, "reason":"PDH_PRZ"},
        ]
    else:
        sl          = round(spike["sl"] if spike["found"] and reversal["override"] else price + atr*1.0, 2)
        be_price    = round(price - 0.10, 2)
        tp_main     = prz_opposite["prz_mid"] if prz_opposite and 0<prz_opposite["prz_mid"]<price else (pdl if pdl and pdl<price else price-atr*3.0)
        tp_final    = round(min(tp_main, price-atr*1.5), 2)
        tp1_price   = round(min(bb["lower"], price-atr*0.5), 2)
        tp2_price   = round(min(bb["mid"],   price-atr*1.0), 2)
        fallback_tp = round(price-atr*4.0, 2)
        partial  = [
            {"pct":50,"price":tp1_price,"reason":"BB_Lower"},
            {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
            {"pct":20,"price":tp_final, "reason":"PDL_PRZ"},
        ]

    now = datetime.now(BKK).strftime("%Y-%m-%d %H:%M:%S")

    # Stage 3 Alert
    try:
        from early_warning import alert_signal_ready
        alert_signal_ready(SYMBOL, direction, sig_type, final_score,
                           price, sl, tp_final, prz_name, session)
    except Exception: pass

    # VSA Gate
    try:
        from vsa_gate import check_reentry_allowed
        vsa = check_reentry_allowed(df_1h, df_15m, direction)
    except Exception:
        vsa = {"allowed": True, "bias": "NEUTRAL", "reason": "vsa_gate N/A"}

    # visual_sl = spike SL ถ้ามี spike, fallback ATR SL
    visual_sl_val = spike["sl"] if spike["found"] else sl

    # zone_valid = ราคายังอยู่ใน fib_zone ไหม
    zone_valid_val = fib_zone["prz_low"] <= price <= fib_zone["prz_high"]

    return CloudSignal(
        action="OPEN", direction=direction, signal_type=sig_type,
        entry=round(price,2), sl=sl, be_price=be_price,
        trail_from=round(bb["mid"],2), tp_final=tp_final,
        partial=partial, pattern=prz_name,
        score=score, context_adj=ctx_adj, final_score=final_score,
        layer=1, session=session, timestamp=now,
        fallback_sl=sl, fallback_tp=fallback_tp,
        st_h4=cascade["st_h4"], st_1h=cascade["st_1h"], st_15m=cascade["st_15m"],
        visual_sl=round(visual_sl_val, 2),
        zone_valid=zone_valid_val,
        reentry_ok=vsa["allowed"],
        vsa_bias=vsa["bias"],
        gps_confirmed=gps.get("gps_confirmed", False),
    )


def signal_to_dict(sig: CloudSignal) -> dict:
    return {
        "action":       sig.action,
        "direction":    sig.direction,
        "signal_type":  sig.signal_type,
        "entry":        sig.entry,
        "sl":           sig.sl,
        "be_price":     sig.be_price,
        "trail_from":   sig.trail_from,
        "tp_final":     sig.tp_final,
        "partial":      sig.partial,
        "pattern":      sig.pattern,
        "score":        sig.score,
        "context_adj":  sig.context_adj,
        "final_score":  sig.final_score,
        "layer":        sig.layer,
        "session":      sig.session,
        "timestamp":    sig.timestamp,
        "fallback_sl":  sig.fallback_sl,
        "fallback_tp":  sig.fallback_tp,
        "st_h4":        sig.st_h4,
        "st_1h":        sig.st_1h,
        "st_15m":       sig.st_15m,
        "visual_sl":    sig.visual_sl,
        "zone_valid":   sig.zone_valid,
        "reentry_ok":   sig.reentry_ok,
        "vsa_bias":     sig.vsa_bias,
        "gps_confirmed":sig.gps_confirmed,
    }
