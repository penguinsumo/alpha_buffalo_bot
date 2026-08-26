"""
signal_engine_v2.py — Alpha Buffalo v5.2 (Sprint Clean)
Python Brain: รวม Cascade H4→H1→M15 + Harmonic PRZ + Context + Scenario

Changes from v5.1:
  [PATCH] ลบ inline score += ทุกจุด
          → ใช้ score_manager.calculate() แทน (Bucket A/B/C/D/E)
  [PATCH] กัน double count: VSA / Sweep / PDH ไม่นับซ้ำ
  [KEEP]  Logic ทุกส่วนเหมือนเดิม (cascade, BOS, harmonic, BB, context, scenario)
"""

import pandas as pd
import numpy as np
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

# ── Score Manager (ใหม่) ──────────────────────────────────
from score_manager import calculate_score, THRESHOLD_V4, THRESHOLD_V5

BKK = timezone(timedelta(hours=7))
SYMBOL         = os.getenv("TRADE_SYMBOL", "XAUUSD")
TWELVE_API_KEY = os.getenv("TWELVE_API_KEY", "")

SWEEP_LOOKBACK = 32
BOS_LOOKBACK   = 5
PIVOT_N        = 3
PRZ_BUFFER     = 3.0
BB_PERIOD      = 20
BB_STD         = 2.0


# ── CloudSignal (เหมือนเดิม) ──────────────────────────────
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
    # [NEW] bucket breakdown สำหรับ debug
    score_breakdown: dict = field(default_factory=dict)


# ── Helper functions (เหมือนเดิมทุกบรรทัด) ───────────────

def get_session(dt: datetime) -> str:
    h = dt.astimezone(timezone.utc).hour
    if 7  <= h < 13: return "London"
    if 13 <= h < 19: return "NY"
    return "Asia"

def add_ema(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["ema20"] = df["close"].ewm(span=20).mean()
    df["ema50"] = df["close"].ewm(span=50).mean()
    return df

def analyze_structure(df: pd.DataFrame, tf_name: str = "") -> str:
    if df is None or len(df) < 20:
        return "INSUFFICIENT_DATA"
    df = add_ema(df)
    curr = df.iloc[-1]

    # [OPT-IN, default OFF] Compare the current candle against the candle
    # ALPHA_STRUCTURE_WINDOW_BARS bars back instead of only the single
    # previous candle. A window of 1 (the default) is byte-identical to the
    # original single-bar comparison -- this only changes behavior when the
    # env var is explicitly set above 1. Root cause: on a feed with no
    # volume data, comparing only against the immediately preceding H4
    # candle means one noisy/wicked bar (a brief spike) can single-handedly
    # block HH/HL detection even though structure over a slightly longer
    # stretch is clearly trending -- looking a few bars further back rides
    # through that one-bar noise instead of being decided by it.
    window_bars = max(1, int(os.getenv("ALPHA_STRUCTURE_WINDOW_BARS", "1")))
    if window_bars > 1 and len(df) > window_bars:
        ref = df.iloc[-(window_bars + 1)]
    else:
        ref = df.iloc[-2]
    ref_high = float(ref["high"])
    ref_low = float(ref["low"])

    hh_hl = (curr["high"] > ref_high) and (curr["low"] > ref_low)
    ll_lh = (curr["low"]  < ref_low)  and (curr["high"] < ref_high)

    avg_vol   = df["volume"].tail(20).mean() if "volume" in df.columns else 0
    vol_spike = curr["volume"] > avg_vol * 1.5 if avg_vol > 0 else False
    vol_drop  = curr["volume"] < avg_vol * 0.8 if avg_vol > 0 else False

    # [OPT-IN, default OFF] Fibonacci-retracement pullback fallback for feeds
    # that never supply volume (e.g. TwelveData XAU/USD spot -- confirmed no
    # "volume" field in the API response). vol_drop is permanently False on
    # such feeds, so PULLBACK_UP/PULLBACK_DOWN can never fire in production
    # even though the market genuinely corrects and resumes trend. This
    # fallback only engages when the flag is on AND volume is genuinely
    # unavailable/zero -- it never overrides a feed that has real volume.
    fib_fallback_on = os.getenv(
        "ALPHA_STRUCTURE_FIB_PULLBACK_FALLBACK", "false"
    ).lower() in {"1", "true", "yes", "on"}
    fib_pullback_up = False
    fib_pullback_down = False
    if fib_fallback_on and avg_vol <= 0:
        fib_lookback = max(20, int(os.getenv("ALPHA_STRUCTURE_FIB_LOOKBACK", "30")))
        fib_window = df.tail(min(len(df), fib_lookback))
        swing_high = float(fib_window["high"].max())
        swing_low = float(fib_window["low"].min())
        span = swing_high - swing_low
        if span > 0:
            close = float(curr["close"])
            retrace_from_high = (swing_high - close) / span
            retrace_from_low = (close - swing_low) / span
            # Classic 0.382-0.618 retracement band = a correction inside the
            # existing trend, not a full reversal of it.
            fib_pullback_up = 0.382 <= retrace_from_high <= 0.618
            fib_pullback_down = 0.382 <= retrace_from_low <= 0.618

    is_bullish = curr["close"] > curr["open"]
    is_bearish = curr["close"] < curr["open"]
    resistance = float(df["high"].tail(20).max())
    near_res   = curr["close"] >= resistance * 0.998
    ema20 = float(curr["ema20"]); ema50 = float(curr["ema50"])

    # [OPT-IN, default OFF] Drop the EMA20/EMA50 cross requirement for
    # IMPULSE_UP/IMPULSE_DOWN and decide purely on structure (HH/HL vs
    # LL/LH). Root cause: EMA50 lags a fresh reversal by design -- after a
    # strong prior trend, EMA20 can stay on the "wrong" side of EMA50 for
    # many bars even after price has already made a clean structural
    # break (confirmed by BOS on M15/M5 independently). On H4 that lag can
    # keep direction stuck at NEUTRAL for a long stretch after the market
    # has already turned. This does NOT touch PULLBACK_UP/PULLBACK_DOWN,
    # which still use the EMA cross to know which trend is being
    # corrected -- only the IMPULSE (primary direction) gate changes.
    ignore_ema = os.getenv("ALPHA_STRUCTURE_IGNORE_EMA", "false").lower() in {
        "1", "true", "yes", "on",
    }
    impulse_up_ema_ok = ignore_ema or ema20 > ema50
    impulse_down_ema_ok = ignore_ema or ema20 < ema50

    if vol_spike and is_bearish and near_res:  return "SELLING_PRESSURE"
    if hh_hl and impulse_up_ema_ok and is_bullish: return "IMPULSE_UP"
    if ll_lh and impulse_down_ema_ok:              return "IMPULSE_DOWN"
    if ema20 > ema50 and is_bearish and vol_drop: return "PULLBACK_UP"
    if ema20 < ema50 and is_bullish and vol_drop: return "PULLBACK_DOWN"
    if ema20 > ema50 and is_bearish and fib_pullback_up:   return "PULLBACK_UP"
    if ema20 < ema50 and is_bullish and fib_pullback_down: return "PULLBACK_DOWN"
    return "SIDEWAYS"

def compute_cascade(df_4h, df_1h, df_15m) -> dict:
    st_h4  = analyze_structure(df_4h,  "H4")
    st_1h  = analyze_structure(df_1h,  "H1")
    st_15m = analyze_structure(df_15m, "M15")
    direction = "NEUTRAL"
    h4_only   = True   # True = H4 alone, False = H4+H1 confirmed

    if st_h4 in ["IMPULSE_UP", "PULLBACK_UP"]:
        direction = "BUY"
        if st_1h in ["PULLBACK_UP", "IMPULSE_UP"]:
            h4_only = False
    elif st_h4 in ["IMPULSE_DOWN", "PULLBACK_DOWN"]:
        direction = "SELL"
        if st_1h in ["PULLBACK_DOWN", "IMPULSE_DOWN"]:
            h4_only = False

    return {
        "direction": direction,
        "h4_only":   h4_only,
        "st_h4":     st_h4,
        "st_1h":     st_1h,
        "st_15m":    st_15m,
    }

def detect_bos(df: pd.DataFrame, direction: str, n: int = BOS_LOOKBACK) -> bool:
    if len(df) < n + 2: return False
    if direction == "BUY":
        return float(df["high"].iloc[-1]) > float(df["high"].iloc[-n-1:-1].max())
    return float(df["low"].iloc[-1]) < float(df["low"].iloc[-n-1:-1].min())

def detect_mss(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 5: return False
    h = df["high"].iloc[-5:].values; l = df["low"].iloc[-5:].values
    c = df["close"].iloc[-5:].values
    if direction == "BUY":
        return l[-3] < l[-4] and h[-1] > h[-2] and c[-1] > c[-2]
    return h[-3] > h[-4] and l[-1] < l[-2] and c[-1] < c[-2]

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

def get_bb(df: pd.DataFrame) -> dict:
    close = df["close"]
    mid   = close.rolling(BB_PERIOD).mean().iloc[-1]
    std   = close.rolling(BB_PERIOD).std().iloc[-1]
    return {"upper": float(mid+BB_STD*std), "mid": float(mid), "lower": float(mid-BB_STD*std)}

def get_kivanc_swing_zone(df_1h: pd.DataFrame, pivot_n: int = 10):
    if df_1h is None or len(df_1h) < pivot_n * 2 + 1:
        return None, None
    swing_high = None; swing_low = None
    highs = df_1h["high"].values; lows = df_1h["low"].values
    n = len(df_1h)
    for i in range(n - pivot_n - 1, pivot_n - 1, -1):
        if swing_high is None:
            if all(highs[i] > highs[i-j] for j in range(1, pivot_n+1)) and \
               all(highs[i] > highs[i+j] for j in range(1, pivot_n+1)):
                swing_high = float(highs[i])
        if swing_low is None:
            if all(lows[i] < lows[i-j] for j in range(1, pivot_n+1)) and \
               all(lows[i] < lows[i+j] for j in range(1, pivot_n+1)):
                swing_low = float(lows[i])
        if swing_high is not None and swing_low is not None:
            break
    return swing_high, swing_low

def detect_h1_spike_at_kivanc(df_1h, direction, fib_zone_high, fib_zone_low, current_price=0.0) -> dict:
    if df_1h is None or len(df_1h) < 3:
        return {"found": False, "sl": 0, "tp1": 0, "volume_confirmed": False}
    c = df_1h.iloc[-2]
    rng = c["high"] - c["low"]
    if rng < 0.001:
        return {"found": False, "sl": 0, "tp1": 0, "volume_confirmed": False}
    wick_up   = (c["high"] - max(c["close"], c["open"])) / rng
    wick_down = (min(c["close"], c["open"]) - c["low"])   / rng
    atr_h1    = float((df_1h["high"] - df_1h["low"]).tail(14).mean())
    ref_price = current_price if current_price > 0 else float(df_1h["close"].iloc[-1])
    # [NEW] volume confirmed flag สำหรับ score_manager Bucket C
    vol_confirmed = is_high_volume(df_1h)

    if direction == "BUY":
        spike     = wick_down > 0.60
        at_kivanc = fib_zone_low <= c["low"] <= fib_zone_high
        if spike and at_kivanc:
            tp1_raw = round(min(c["open"], c["close"]), 2)
            tp1 = tp1_raw if tp1_raw > ref_price else round(ref_price + atr_h1 * 1.5, 2)
            return {"found": True, "sl": round(c["low"]-0.30, 2), "tp1": tp1,
                    "volume_confirmed": vol_confirmed}
    else:
        spike     = wick_up > 0.60
        at_kivanc = fib_zone_low <= c["high"] <= fib_zone_high
        if spike and at_kivanc:
            tp1_raw = round(max(c["open"], c["close"]), 2)
            tp1 = tp1_raw if tp1_raw < ref_price else round(ref_price - atr_h1 * 1.5, 2)
            return {"found": True, "sl": round(c["high"]+0.30, 2), "tp1": tp1,
                    "volume_confirmed": vol_confirmed}
    return {"found": False, "sl": 0, "tp1": 0, "volume_confirmed": False}

def detect_pinbar(df: pd.DataFrame, direction: str) -> bool:
    if len(df) < 2: return False
    c = df.iloc[-2]; rng = c["high"] - c["low"]
    if rng < 0.001: return False
    body = abs(c["close"]-c["open"])
    wick_up   = c["high"] - max(c["close"], c["open"])
    wick_down = min(c["close"], c["open"]) - c["low"]
    if direction == "SELL": return wick_up/rng > 0.60 and body/rng < 0.30
    return wick_down/rng > 0.60 and body/rng < 0.30

def is_high_volume(df: pd.DataFrame, window: int = 50) -> bool:
    if "volume" not in df.columns: return False
    vol = df["volume"].iloc[-1]
    avg = df["volume"].iloc[-window-1:-1].mean()
    return float(vol) >= float(avg) * 1.8 if avg > 0 else False

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
        if all(df["high"].iloc[i-j] < h for j in range(1,n+1)) and \
           all(df["high"].iloc[i+j] < h for j in range(1,n+1)):
            swings.append((i, float(h), "H"))
        elif all(df["low"].iloc[i-j] > l for j in range(1,n+1)) and \
             all(df["low"].iloc[i+j] > l for j in range(1,n+1)):
            swings.append((i, float(l), "L"))
    return swings

def scan_harmonic(df_1h, df_4h) -> list:
    results = []
    for df in [df_1h, df_4h]:
        swings = find_pivots(df)
        if len(swings) < 5: continue
        for i in range(len(swings)-4):
            pts = swings[i:i+5]
            kinds = [p[2] for p in pts]
            if not all(kinds[j] != kinds[j+1] for j in range(4)): continue
            X,A,B,C,D = [p[1] for p in pts]
            XA = abs(A-X); AB = abs(B-A)
            if XA < 0.001 or AB < 0.001: continue
            r_AB = AB/XA; r_CD = abs(D-C)/XA
            for name, pat in PATTERNS.items():
                if abs(r_AB-pat["AB"][0]) <= pat["AB"][1] and \
                   abs(r_CD-pat["CD"][0]) <= pat["CD"][1]:
                    results.append({
                        "name": name, "direction": pat["dir"], "priority": pat["p"],
                        "prz_mid": D, "prz_high": D+PRZ_BUFFER, "prz_low": D-PRZ_BUFFER,
                    })
    seen = set(); final = []
    for r in results:
        key = r["direction"]+"_"+str(round(r["prz_mid"],1))
        if key not in seen: seen.add(key); final.append(r)
    return sorted(final, key=lambda x: x["priority"])

def get_context_adj(direction: str, score: int) -> tuple:
    total_adj = 0; reasons = []
    for plugin, func_name, kwargs in [
        ("plugin_news",       "check_news_filter",  {}),
        ("plugin_fear_greed", "get_fg_score_adj",   {"direction": direction}),
        ("plugin_dxy",        "get_dxy_score_adj",  {"direction": direction, "api_key": TWELVE_API_KEY}),
        ("plugin_cot",        "get_cot_score_adj",  {"direction": direction}),
    ]:
        try:
            mod = __import__(plugin); func = getattr(mod, func_name)
            res = func(**kwargs); adj = res.get("score_adj", 0)
            total_adj += adj; reasons.append(res.get("reason",""))
            if plugin == "plugin_news" and not res.get("safe", True):
                return total_adj, True, res.get("reason","News blocked")
        except Exception:
            reasons.append(plugin + ": error")
    return total_adj, False, " | ".join(reasons)

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


# ══════════════════════════════════════════════════════════
# MAIN SIGNAL ENGINE — PATCHED
# ══════════════════════════════════════════════════════════

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

    # ── Step 1: Cascade ──────────────────────────────────
    cascade   = compute_cascade(df_4h, df_1h, df_15m)
    direction = cascade["direction"]
    if direction == "NEUTRAL": return None

    # ── Step 2: Early Warning Stage 1 ───────────────────
    try:
        from early_warning import check_vsa_forming
        check_vsa_forming(df_15m, direction, SYMBOL, session)
    except Exception: pass

    # ── Step 3: Collect raw signals (ยังไม่บวก score) ───
    bos_detected = detect_bos(df_15m, direction)
    mss_detected = detect_mss(df_15m, direction)

    # Early Warning Stage 2
    try:
        from early_warning import check_bos_confirmed
        check_bos_confirmed(df_15m, direction, SYMBOL, 0, "")
    except Exception: pass

    # PDH/PDL
    pdh, pdl = get_pdh_pdl(df_1h)
    near_pdh  = bool(pdh and direction=="SELL" and abs(price-pdh)/pdh < 0.003)
    near_pdl  = bool(pdl and direction=="BUY"  and abs(price-pdl)/pdl < 0.003)
    pdh_pdl_hit = near_pdh or near_pdl

    # Session Sweep
    window    = df_15m.tail(SWEEP_LOOKBACK)
    sess_high = float(window["high"].max())
    sess_low  = float(window["low"].min())
    curr_high = float(df_15m["high"].iloc[-1])
    curr_low  = float(df_15m["low"].iloc[-1])
    sweep_valid = (
        (direction=="SELL" and curr_high > sess_high*1.0005 and price < sess_high) or
        (direction=="BUY"  and curr_low  < sess_low*0.9995  and price > sess_low)
    )
    # PDH/PDL sweep = สำคัญกว่า session sweep
    sweep_is_pdh_pdl = pdh_pdl_hit and sweep_valid

    # Harmonic PRZ
    prz_list     = scan_harmonic(df_1h, df_4h)
    prz_match    = None
    prz_name     = ""
    prz_priority = "secondary"
    prz_opposite = None
    for prz in prz_list:
        if prz["direction"]==direction and prz["prz_low"]<=price<=prz["prz_high"]:
            prz_match = prz; prz_name = prz["name"]
            prz_priority = "primary" if prz["priority"]==1 else "secondary"
            break
    for prz in prz_list:
        if prz["direction"] != direction: prz_opposite = prz; break

    # Kivanc Zone
    fib_zone = None
    kivanc_in_golden = False
    if prz_match:
        fib_zone = prz_match
        kivanc_in_golden = True
    else:
        swing_high, swing_low = get_kivanc_swing_zone(df_1h, pivot_n=10)
        if swing_high and swing_low:
            h1_rng = swing_high - swing_low
            if direction == "BUY":
                fib_lo = swing_low  + h1_rng * 0.618
                fib_hi = swing_low  + h1_rng * 0.786
            else:
                fib_lo = swing_high - h1_rng * 0.786
                fib_hi = swing_high - h1_rng * 0.618
            fib_zone = {"prz_low": fib_lo, "prz_high": fib_hi}
            kivanc_in_golden = fib_lo <= price <= fib_hi
        else:
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
            kivanc_in_golden = fib_lo <= price <= fib_hi

    # H1 Spike
    spike = {"found": False, "sl": 0, "tp1": 0, "volume_confirmed": False}
    if fib_zone:
        spike = detect_h1_spike_at_kivanc(
            df_1h, direction,
            fib_zone["prz_high"], fib_zone["prz_low"],
            current_price=price,
        )
    if spike["found"]:
        print(f"🎯 H1 Spike at Kivanc: {direction} SL={spike['sl']} TP1={spike['tp1']}")

    # VSA
    vsa_ok = is_high_volume(df_15m)

    # Pinbar (เพิ่ม Kivanc zone quality ถ้ามี PRZ)
    kivanc_score_raw = 0
    if prz_match:
        if detect_pinbar(df_1h, direction): kivanc_score_raw += 2
        if detect_pinbar(df_4h, direction): kivanc_score_raw += 3
    kivanc_score_raw = min(kivanc_score_raw, 5)

    # AT Bonus (alphatrend_gate — optional)
    at_bonus = 0
    try:
        from alphatrend_gate import check_at_zone
        at_res  = check_at_zone(df_1h, df_4h, direction, "cascade_bonus")
        at_bonus = at_res.get("bonus", 0)
    except Exception: pass

    # FVG verdict
    fvg_verdict = "NONE"
    try:
        from fvg_detector import FVGDetector
        _fvg = FVGDetector()
        swing_h, swing_l = get_kivanc_swing_zone(df_15m, pivot_n=5)
        if swing_h and swing_l:
            fvg_res = _fvg.analyze(df_15m, swing_h, swing_l)
            fvg_verdict = fvg_res.verdict
    except Exception: pass

    # Context
    ctx_adj, blocked, ctx_reason = get_context_adj(direction, 0)
    if blocked:
        print("Context blocked: " + ctx_reason)
        return None

    # ── Step 4: SCORE MANAGER ────────────────────────────
    # [PATCH] คำนวณ score ทั้งหมดที่นี่ที่เดียว
    score_result = calculate_score(
        # Bucket A — Trend Structure
        cascade_direction = direction,
        cascade_h4_only   = cascade["h4_only"],
        reversal_stage    = 0,

        # Bucket B — Entry Zone Quality (priority สูงสุดอันเดียว)
        harmonic_in_prz   = bool(prz_match),
        harmonic_priority = prz_priority,
        kivanc_in_golden  = kivanc_in_golden and not bool(prz_match),
        kivanc_score      = kivanc_score_raw,
        fvg_verdict       = fvg_verdict if not (prz_match or kivanc_in_golden) else "NONE",

        # Bucket C — Trigger Confirmation
        bos_detected      = bos_detected,
        mss_detected      = mss_detected,
        sweep_valid       = sweep_valid,
        sweep_is_pdh_pdl  = sweep_is_pdh_pdl,
        h1_spike          = spike["found"],
        h1_spike_volume   = spike.get("volume_confirmed", False),
        at_bonus          = at_bonus,

        # Bucket D — VSA (single source)
        vsa_ok            = vsa_ok,

        # Bucket E — Context
        news_block        = False,   # ถ้าถึงตรงนี้ news ไม่ block แล้ว
        fg_score          = ctx_adj if abs(ctx_adj) <= 2 else (2 if ctx_adj>0 else -2),
        dxy_score         = 0,       # plugin จัดการแยกกัน — รวมใน ctx_adj แล้ว
        cot_score         = 0,
    )

    final_score = score_result.total
    print(score_result.summary())

    # ── Step 5: Threshold Check ──────────────────────────
    if final_score < THRESHOLD_V4: return None

    # BB Direction Filter (เหมือนเดิม)
    # [OPT-IN, default OFF] ALPHA_BB_FILTER_DISABLE=true skips this block
    # entirely. Root cause this exists for: it blocks BUY once price is
    # already above the upper band, and SELL once price is already below
    # the lower band -- an anti-chasing-extremes guard. Live-tested
    # 2026-08-26/27: this was the one gate still blocking a real SELL setup
    # (score 10, well above threshold) after the H4 cascade/EMA fixes,
    # because price had already pushed $9.40 past the lower band. Default
    # stays ON (current behavior unchanged) until explicitly disabled.
    bb_filter_disabled = os.getenv("ALPHA_BB_FILTER_DISABLE", "false").lower() in {
        "1", "true", "yes", "on",
    }
    bb = get_bb(df_15m)
    if not bb_filter_disabled:
        if direction == "BUY"  and bb["upper"] < price:
            print("BB Filter: BUY blocked — BB bearish"); return None
        if direction == "SELL" and bb["lower"] > price:
            print("BB Filter: SELL blocked — BB bullish"); return None

    sig_type = score_result.signal_type

    # ── Step 6: Scenario Validation (เหมือนเดิม) ────────
    valid, val_reason = validate_scenario(
        direction, sig_type, final_score, prz_name,
        df_15m, buy_layers, sell_layers, spread)
    if not valid:
        print("Scenario blocked: " + val_reason); return None

    # ── Step 7: Build CloudSignal (เหมือนเดิม) ──────────
    atr = max(float((df_15m["high"]-df_15m["low"]).tail(14).mean()), 1.0)

    if direction == "BUY":
        sl          = round(price - atr*1.0, 2)
        be_price    = round(price + 0.10, 2)
        tp_main     = (prz_opposite["prz_mid"] if prz_opposite and prz_opposite["prz_mid"]>price
                       else (pdh if pdh and pdh>price else price+atr*3.0))
        tp_final    = round(max(tp_main, price+atr*1.5), 2)
        tp1_price   = round(max(bb["upper"], price+atr*0.5), 2)
        tp2_price   = round(max(bb["mid"],   price+atr*1.0), 2)
        fallback_tp = round(price+atr*4.0, 2)
        partial = [
            {"pct":50,"price":tp1_price,"reason":"BB_Upper"},
            {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
            {"pct":20,"price":tp_final, "reason":"PDH_PRZ"},
        ]
    else:
        sl          = round(price + atr*1.0, 2)
        be_price    = round(price - 0.10, 2)
        tp_main     = (prz_opposite["prz_mid"] if prz_opposite and 0<prz_opposite["prz_mid"]<price
                       else (pdl if pdl and pdl<price else price-atr*3.0))
        tp_final    = round(min(tp_main, price-atr*1.5), 2)
        tp1_price   = round(min(bb["lower"], price-atr*0.5), 2)
        tp2_price   = round(min(bb["mid"],   price-atr*1.0), 2)
        fallback_tp = round(price-atr*4.0, 2)
        partial = [
            {"pct":50,"price":tp1_price,"reason":"BB_Lower"},
            {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
            {"pct":20,"price":tp_final, "reason":"PDL_PRZ"},
        ]

    now = datetime.now(BKK).strftime("%Y-%m-%d %H:%M:%S")

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
        score=score_result.bucket_a + score_result.bucket_b + score_result.bucket_c,
        context_adj=score_result.bucket_e,
        final_score=final_score,
        layer=1, session=session, timestamp=now,
        fallback_sl=sl, fallback_tp=fallback_tp,
        st_h4=cascade["st_h4"], st_1h=cascade["st_1h"], st_15m=cascade["st_15m"],
        score_breakdown=score_result.breakdown,
    )


def signal_to_dict(sig: CloudSignal) -> dict:
    return {
        "action":          sig.action,
        "direction":       sig.direction,
        "signal_type":     sig.signal_type,
        "entry":           sig.entry,
        "sl":              sig.sl,
        "be_price":        sig.be_price,
        "trail_from":      sig.trail_from,
        "tp_final":        sig.tp_final,
        "partial":         sig.partial,
        "pattern":         sig.pattern,
        "score":           sig.score,
        "context_adj":     sig.context_adj,
        "final_score":     sig.final_score,
        "layer":           sig.layer,
        "session":         sig.session,
        "timestamp":       sig.timestamp,
        "fallback_sl":     sig.fallback_sl,
        "fallback_tp":     sig.fallback_tp,
        "st_h4":           sig.st_h4,
        "st_1h":           sig.st_1h,
        "st_15m":          sig.st_15m,
        "score_breakdown": sig.score_breakdown,  # [NEW] debug field
    }
