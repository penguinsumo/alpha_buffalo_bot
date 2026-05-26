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
    cascade = compute_cascade(df_4h, df_1h, df_15m)
    direction = cascade["direction"]
    score     = cascade["score"]
    if direction == "NEUTRAL" or score < 2: return None

    # Step 2: Early Warning Stage 1
    try:
        from early_warning import check_vsa_forming
        check_vsa_forming(df_15m, direction, SYMBOL, session)
    except Exception: pass

    # Step 3: BOS / MSS
    if detect_bos(df_15m, direction): score += 2
    if detect_mss(df_15m, direction): score += 1

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

    # Step 7: Harmonic PRZ
    prz_list     = scan_harmonic(df_1h, df_4h)
    prz_match    = None
    prz_name     = ""
    prz_opposite = None
    for prz in prz_list:
        if prz["direction"]==direction and prz["prz_low"]<=price<=prz["prz_high"]:
            prz_match=prz; prz_name=prz["name"]; score+=(4-prz["priority"]); break
    for prz in prz_list:
        if prz["direction"]!=direction: prz_opposite=prz; break

    # Step 8: Pin Bar
    if prz_match:
        if detect_pinbar(df_1h, direction): score += 2
        if detect_pinbar(df_4h, direction): score += 3

    # Step 9: VSA Volume
    if is_high_volume(df_15m): score += 2

    if score < V4_MIN: return None

    # BB Direction Filter — ห้าม BUY ถ้า BB ชี้ลง
    bb = get_bb(df_15m)
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
        sl          = round(price - atr*1.0, 2)
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
        sl          = round(price + atr*1.0, 2)
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

    return CloudSignal(
        action="OPEN", direction=direction, signal_type=sig_type,
        entry=round(price,2), sl=sl, be_price=be_price,
        trail_from=round(bb["mid"],2), tp_final=tp_final,
        partial=partial, pattern=prz_name,
        score=score, context_adj=ctx_adj, final_score=final_score,
        layer=1, session=session, timestamp=now,
        fallback_sl=sl, fallback_tp=fallback_tp,
        st_h4=cascade["st_h4"], st_1h=cascade["st_1h"], st_15m=cascade["st_15m"],
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
    }
