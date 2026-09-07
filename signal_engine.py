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
from auto_fibo_entry import compute_auto_fibo, AutoFiboEstimate, DIRECTION_UP, DIRECTION_DOWN

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
    # [NEW] Estimate Entry (Auto Fibo 144/1.272 style) -- always computed
    # (best-effort) and attached for display, regardless of whether the
    # opt-in ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED filter is on. Empty
    # string / 0.0 when there wasn't enough history to compute it.
    auto_fibo_direction:     str = ""
    auto_fibo_entry_zone_lo: float = 0.0
    auto_fibo_entry_zone_hi: float = 0.0
    auto_fibo_ext_target:    float = 0.0
    # [NEW, for sweep_reentry.py] whether THIS entry actually used the
    # sweep-wick anchor (ALPHA_SIGNAL_SWEEP_WICK_ENTRY) rather than
    # zone/fibo/ATR -- the Round-2 re-entry watcher only ever arms itself
    # off a real sweep-wick entry, per owner's design. swing_high_ref/
    # swing_low_ref carry the same swing this setup's Kivanc zone was
    # measured against (0.0 when unavailable, e.g. harmonic PRZ match), so
    # the watcher can derive a 61.8% reference price for Round 2's SL.
    sweep_wick_entry_used: bool = False
    swing_high_ref:        float = 0.0
    swing_low_ref:         float = 0.0


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

def resolve_zone_based_entry_sl(direction, price, fib_zone, spike, enabled):
    """
    [OPT-IN helper for ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL]

    Given the actual Fib/PRZ zone a setup is based on (`fib_zone`, with
    "prz_low"/"prz_high") and the structure-based reaction-candle SL found by
    detect_h1_spike_at_kivanc() (`spike`), return (entry_price, zone_sl):

      - entry_price: `price` clamped into [prz_low, prz_high] when the zone
        is known -- i.e. unchanged if price is already inside the zone, but
        snapped back to the nearer zone edge if price has already run past
        it (the exact "entry chases live price" problem this flag fixes).
      - zone_sl: spike["sl"] when it was found AND it lands on the correct
        side of entry_price (below entry for BUY, above for SELL); otherwise
        None, meaning the caller should fall back to the flat ATR buffer.

    When `enabled` is False (default) or `fib_zone` is falsy, this always
    returns (price, None) -- i.e. current behavior, byte-identical.
    """
    if not enabled or not fib_zone:
        return price, None

    entry_price = price
    z_lo = fib_zone.get("prz_low")
    z_hi = fib_zone.get("prz_high")
    if z_lo is not None and z_hi is not None and z_hi > z_lo:
        entry_price = min(max(price, z_lo), z_hi)

    zone_sl = None
    if spike.get("found") and spike.get("sl"):
        candidate_sl = spike["sl"]
        if direction == "BUY" and candidate_sl < entry_price:
            zone_sl = candidate_sl
        elif direction == "SELL" and candidate_sl > entry_price:
            zone_sl = candidate_sl

    return entry_price, zone_sl


def resolve_auto_fibo_filter(
    direction: str,
    price: float,
    auto_fibo: Optional[AutoFiboEstimate],
    atr: float,
    enabled: bool,
    tolerance_atr: float,
) -> bool:
    """
    [OPT-IN helper for ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED]

    Estimate Entry (Auto Fibo 144/1.272 style, see auto_fibo_entry.py) as an
    extra confirmation filter -- same idea as the Pine multi-asset fork's
    `estimateEntryFilterEnabled` (opt-in, default OFF, zero change to
    existing signals unless explicitly turned on).

    Returns True if the signal should proceed, False if it should be
    blocked. When `enabled` is False (default) or `auto_fibo` is None
    (not enough history yet), always returns True -- i.e. current behavior,
    unchanged.

    When enabled: a BUY only proceeds if the Auto Fibo swing is an
    up-swing pullback (auto_fibo.direction == DIRECTION_UP) with price
    inside the near/deep retracement zone (widened by `tolerance_atr` * atr
    on each side); a SELL requires a down-swing bounce (DIRECTION_DOWN)
    under the same zone check.
    """
    if not enabled or auto_fibo is None:
        return True

    dir_ok = (
        (direction == "BUY"  and auto_fibo.direction == DIRECTION_UP) or
        (direction == "SELL" and auto_fibo.direction == DIRECTION_DOWN)
    )
    if not dir_ok:
        return False

    tolerance = max(atr, 0.0) * tolerance_atr
    return auto_fibo.in_zone(price, tolerance=tolerance)


# Named Fibonacci ratios that bound the Kivanc Golden Zone -- 61.8% and
# 78.6%, the two "golden numbers" this project has always used for that
# zone (not the whole ratio family). Kept as a module-level constant so the
# golden-number filter and any future caller stay in sync with the same
# two levels the zone itself is built from.
FIBO_GOLDEN_ZONE_LEVELS = (0.618, 0.786)


def resolve_fibo_golden_number(price, swing_high, swing_low, direction,
                                tolerance_pct=0.02, levels=FIBO_GOLDEN_ZONE_LEVELS):
    """
    [OPT-IN helper for ALPHA_SIGNAL_FIBO_GOLDEN_MODE]

    Check whether `price` sits close to one of `levels` (fractions of the
    swing range, measured down from swing_high for SELL / up from
    swing_low for BUY) -- a real named Fibonacci ratio -- rather than just
    "somewhere between the two levels" the way plain band membership does.

    tolerance_pct is a fraction of the swing range (default 0.02 = 2%),
    so the effective price tolerance scales with how big the swing is
    instead of being a fixed dollar amount.

    Returns (matched: bool, level: float | None, level_price: float | None)
    -- level/level_price describe whichever of `levels` matched, or
    (False, None, None) if none did or the swing is degenerate (range <= 0).
    """
    rng = swing_high - swing_low
    if rng <= 0:
        return False, None, None
    tolerance = rng * tolerance_pct
    for lv in levels:
        level_price = (swing_high - rng * lv) if direction == "SELL" else (swing_low + rng * lv)
        if abs(price - level_price) <= tolerance:
            return True, lv, level_price
    return False, None, None


def resolve_fibo_sl_tp(direction, swing_high, swing_low):
    """
    [OPT-IN helper for ALPHA_SIGNAL_FIBO_GOLDEN_MODE]

    Once entry is confirmed at a real golden-ratio level (61.8% or 78.6%
    down from swing_high for SELL / up from swing_low for BUY), derive SL
    and TP from the SAME swing's other named ratios instead of a flat ATR
    buffer or BB/PDH targets that have nothing to do with this swing:

      - SL at 50% -- the golden zone represents a SHALLOW pullback (only
        21.4%-38.2% of the swing reclaimed) that is expected to fail and
        resume in the trade's direction. That thesis is invalidated if the
        pullback turns out NOT to be shallow -- i.e. if price keeps moving
        BACK PAST the zone's near edge (61.8%) toward the swing's origin
        (swing_high for SELL, swing_low for BUY) instead of reversing. 50%
        is the next standard Fibonacci level on that (shallower-pullback)
        side of 61.8%, so that is where the setup should be proven wrong --
        not an arbitrary ATR distance from wherever price is, and NOT a
        deeper level like 88.6% (that is the continuation direction the
        trade is betting FOR, not against).
      - TP1/TP2 at the 127.2%/161.8% (Golden Ratio) extensions beyond the
        swing, continuing in the trade's direction -- the classic
        Fibonacci continuation targets for this swing.

    Returns (sl, tp1, tp2), all rounded to 2 decimals.
    """
    rng = swing_high - swing_low
    if direction == "SELL":
        sl  = swing_high - rng * 0.5
        tp1 = swing_high - rng * 1.272
        tp2 = swing_high - rng * 1.618
    else:
        sl  = swing_low + rng * 0.5
        tp1 = swing_low + rng * 1.272
        tp2 = swing_low + rng * 1.618
    return round(sl, 2), round(tp1, 2), round(tp2, 2)


def resolve_sweep_wick_entry(direction, sweep_valid, curr_high, curr_low, buffer=0.30):
    """
    [OPT-IN helper for ALPHA_SIGNAL_SWEEP_WICK_ENTRY]

    When the setup's own qualifying evidence is a liquidity sweep
    (sweep_valid -- price wicked through session/PDH-PDL liquidity on the
    current M15 candle and closed back inside it), that candle's wick tip
    IS the real point where liquidity was taken -- a far more meaningful
    entry anchor than wherever price has drifted to by the time every
    later gate (score/BB/scenario) finally clears. This mirrors the same
    "anchor to the real reaction point, not the chased price" idea already
    used for detect_h1_spike_at_kivanc's SL (the H1 reaction candle there),
    applied here to the M15 sweep candle's entry instead.

    Returns (entry, sl) anchored at the wick tip with `buffer` beyond it
    for SL, or (None, None) if there is no valid sweep to anchor to (caller
    should fall back to its normal entry/SL resolution in that case).
    """
    if not sweep_valid:
        return None, None
    if direction == "SELL":
        return round(curr_high, 2), round(curr_high + buffer, 2)
    else:
        return round(curr_low, 2), round(curr_low - buffer, 2)


def compute_signal(
    df_4h: pd.DataFrame,
    df_1h: pd.DataFrame,
    df_15m: pd.DataFrame,
    buy_layers: int = 0,
    sell_layers: int = 0,
    spread: float = 0.0,
    symbol_label: Optional[str] = None,
) -> Optional[CloudSignal]:
    # symbol_label lets a caller running this same engine against a symbol
    # other than the module-level SYMBOL (e.g. the opt-in BTC/US100/JPN225
    # extra-symbol scan) get correctly-labeled early_warning Telegram alerts
    # instead of every alert being tagged with the main SYMBOL regardless of
    # which symbol actually triggered it. None (default) preserves old
    # behavior exactly -- falls back to the module-level SYMBOL.
    active_symbol = symbol_label or SYMBOL

    if len(df_15m) < 50: return None
    price   = float(df_15m["close"].iloc[-1])
    dt      = df_15m.index[-1]
    session = get_session(dt if hasattr(dt,"hour") else datetime.now(timezone.utc))
    # Hoisted from Step 7 (unchanged formula, same df_15m) so Estimate Entry
    # filter tolerance below can reuse the same ATR value.
    atr = max(float((df_15m["high"]-df_15m["low"]).tail(14).mean()), 1.0)

    # ── Step 1: Cascade ──────────────────────────────────
    cascade   = compute_cascade(df_4h, df_1h, df_15m)
    direction = cascade["direction"]
    if direction == "NEUTRAL": return None

    # ── Step 2: Early Warning Stage 1 ───────────────────
    try:
        from early_warning import check_vsa_forming
        check_vsa_forming(df_15m, direction, active_symbol, session)
    except Exception: pass

    # ── Step 3: Collect raw signals (ยังไม่บวก score) ───
    bos_detected = detect_bos(df_15m, direction)
    mss_detected = detect_mss(df_15m, direction)

    # Early Warning Stage 2
    try:
        from early_warning import check_bos_confirmed
        check_bos_confirmed(df_15m, direction, active_symbol, 0, "")
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
    # swing_high_for_fibo/swing_low_for_fibo: the swing this fib_zone was
    # measured against, kept around (regardless of which branch below ran)
    # so the opt-in Fibonacci Golden Number filter further down can check
    # price against the swing's *named* ratios (61.8%/78.6%), not just band
    # membership. None for the harmonic-PRZ branch -- that zone already
    # comes from a named harmonic pattern ratio, not this swing.
    swing_high_for_fibo = None
    swing_low_for_fibo  = None
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
            swing_high_for_fibo, swing_low_for_fibo = swing_high, swing_low
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
            swing_high_for_fibo, swing_low_for_fibo = h1_high, h1_low

    # ── [OPT-IN] Fibonacci Golden Number filter (ALPHA_SIGNAL_FIBO_GOLDEN_MODE) ──
    # Root cause this exists for: kivanc_in_golden above accepts price
    # ANYWHERE inside the 61.8%-78.6% band (16.8% of the swing wide) as
    # "golden zone" evidence -- but a real Fibonacci "golden number" is a
    # specific ratio (61.8% or 78.6%), not an arbitrary band between them.
    # When enabled, kivanc_in_golden instead requires price to sit within
    # ALPHA_SIGNAL_FIBO_GOLDEN_TOLERANCE_PCT (default 2% of the swing range)
    # of one of those two exact levels. Does not touch the harmonic-PRZ
    # branch (prz_match) -- that already comes from named harmonic ratios.
    # Default OFF -- current band-based behavior byte-identical until
    # explicitly enabled.
    fibo_golden_mode = os.getenv("ALPHA_SIGNAL_FIBO_GOLDEN_MODE", "false").lower() in {
        "1", "true", "yes", "on",
    }
    fibo_golden_level = None
    if fibo_golden_mode and not prz_match and swing_high_for_fibo and swing_low_for_fibo:
        fibo_golden_tolerance_pct = float(
            os.getenv("ALPHA_SIGNAL_FIBO_GOLDEN_TOLERANCE_PCT", "0.02")
        )
        matched, fibo_golden_level, _ = resolve_fibo_golden_number(
            price, swing_high_for_fibo, swing_low_for_fibo, direction,
            fibo_golden_tolerance_pct,
        )
        kivanc_in_golden = matched

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

    # ── Estimate Entry (Auto Fibo 144/1.272 style) ──────
    # Same methodology as the Pine multi-asset fork's Estimate Entry
    # feature (auto_fibo_entry.py), NOT kivanc_vsaob.py's small-pivot
    # Golden Zone. Always computed (best-effort) below so it can be
    # attached to the returned CloudSignal for display -- it only gates
    # the signal when ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED is explicitly
    # turned on (default OFF = current behavior, byte-identical).
    try:
        auto_fibo = compute_auto_fibo(df_15m)
    except Exception:
        auto_fibo = None

    auto_fibo_filter_enabled = os.getenv(
        "ALPHA_SIGNAL_AUTO_FIBO_FILTER_ENABLED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    auto_fibo_tolerance_atr = float(os.getenv("ALPHA_SIGNAL_AUTO_FIBO_TOLERANCE_ATR", "0.5"))
    if not resolve_auto_fibo_filter(
        direction, price, auto_fibo, atr, auto_fibo_filter_enabled, auto_fibo_tolerance_atr
    ):
        print("Auto Fibo filter: blocked — price not in Estimate Entry zone"); return None

    sig_type = score_result.signal_type

    # ── Step 6: Scenario Validation (เหมือนเดิม) ────────
    valid, val_reason = validate_scenario(
        direction, sig_type, final_score, prz_name,
        df_15m, buy_layers, sell_layers, spread)
    if not valid:
        print("Scenario blocked: " + val_reason); return None

    # ── Step 7: Build CloudSignal (เหมือนเดิม) ──────────
    # atr computed earlier (Step 1) -- reused here unchanged.

    # [OPT-IN, default OFF] ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL=true switches
    # Entry/SL from "raw live price + flat ATR buffer" to "the actual Fib/PRZ
    # zone this setup is based on". Root cause this exists for: by the time
    # the full score/gate cascade finally clears, price has often already
    # moved past the zone edge (fib_zone) the setup was scored against -- so
    # the reported Entry ends up being just "wherever price was when the
    # gates opened", and SL (Entry +/- flat ATR) lands close to where the
    # real structural reversal actually happens instead of Entry itself.
    # When enabled: Entry is clamped into [fib_zone.prz_low, fib_zone.prz_high]
    # (no change if price is already inside the zone), and SL prefers the
    # structure-based spike["sl"] (from detect_h1_spike_at_kivanc, anchored to
    # the real reaction candle at the zone) over the flat ATR buffer, as long
    # as it lands on the correct side of the (possibly clamped) entry.
    # Default stays OFF (current behavior byte-identical) until enabled.
    zone_entry_sl_enabled = os.getenv("ALPHA_SIGNAL_ZONE_BASED_ENTRY_SL", "false").lower() in {
        "1", "true", "yes", "on",
    }

    # [OPT-IN, default OFF] ALPHA_SIGNAL_SWEEP_WICK_ENTRY=true anchors Entry
    # (and SL) to the M15 sweep candle's own wick tip whenever the setup's
    # qualifying evidence is a liquidity sweep (sweep_valid) -- the wick tip
    # is the actual point liquidity was taken, a more meaningful anchor than
    # wherever price has drifted to once every later gate finally clears.
    # Takes priority over zone_entry_sl_enabled/fibo_golden_mode below when
    # both a sweep and a zone/golden-number match are present, since the
    # sweep is the more immediate, already-happened structural event.
    # Default OFF -- current behavior byte-identical until enabled.
    sweep_wick_entry_enabled = os.getenv("ALPHA_SIGNAL_SWEEP_WICK_ENTRY", "false").lower() in {
        "1", "true", "yes", "on",
    }
    sweep_entry, sweep_sl = resolve_sweep_wick_entry(direction, sweep_valid, curr_high, curr_low)

    fibo_tp_pair = None   # (tp1, tp2) from resolve_fibo_sl_tp, set below when it applies

    if sweep_wick_entry_enabled and sweep_entry is not None:
        entry_price, zone_sl = sweep_entry, sweep_sl
    else:
        entry_price, zone_sl = resolve_zone_based_entry_sl(
            direction, price, fib_zone, spike, zone_entry_sl_enabled)
        # [OPT-IN] ALPHA_SIGNAL_FIBO_GOLDEN_MODE: once a real golden-ratio
        # level matched above (fibo_golden_level), replace the flat-ATR/
        # spike SL and the BB/PDH-derived TPs with levels drawn from the
        # SAME swing's other named Fibonacci ratios (see resolve_fibo_sl_tp)
        # instead. Skipped entirely when the sweep-wick entry above already
        # took priority.
        if fibo_golden_mode and fibo_golden_level is not None and swing_high_for_fibo and swing_low_for_fibo:
            fib_sl, fib_tp1, fib_tp2 = resolve_fibo_sl_tp(direction, swing_high_for_fibo, swing_low_for_fibo)
            zone_sl = fib_sl
            fibo_tp_pair = (fib_tp1, fib_tp2)

    if direction == "BUY":
        sl          = zone_sl if zone_sl is not None else round(entry_price - atr*1.0, 2)
        be_price    = round(entry_price + 0.10, 2)
        if fibo_tp_pair:
            fib_tp1, fib_tp2 = fibo_tp_pair
            tp1_price   = fib_tp1
            tp_final    = round(max(fib_tp2, entry_price+atr*1.5), 2)
            fallback_tp = tp_final
            partial = [
                {"pct":50,"price":tp1_price,"reason":"Fibo_1.272ext"},
                {"pct":50,"price":tp_final, "reason":"Fibo_1.618ext"},
            ]
        else:
            tp_main     = (prz_opposite["prz_mid"] if prz_opposite and prz_opposite["prz_mid"]>entry_price
                           else (pdh if pdh and pdh>entry_price else entry_price+atr*3.0))
            tp_final    = round(max(tp_main, entry_price+atr*1.5), 2)
            tp1_price   = round(max(bb["upper"], entry_price+atr*0.5), 2)
            tp2_price   = round(max(bb["mid"],   entry_price+atr*1.0), 2)
            fallback_tp = round(entry_price+atr*4.0, 2)
            partial = [
                {"pct":50,"price":tp1_price,"reason":"BB_Upper"},
                {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
                {"pct":20,"price":tp_final, "reason":"PDH_PRZ"},
            ]
    else:
        sl          = zone_sl if zone_sl is not None else round(entry_price + atr*1.0, 2)
        be_price    = round(entry_price - 0.10, 2)
        if fibo_tp_pair:
            fib_tp1, fib_tp2 = fibo_tp_pair
            tp1_price   = fib_tp1
            tp_final    = round(min(fib_tp2, entry_price-atr*1.5), 2)
            fallback_tp = tp_final
            partial = [
                {"pct":50,"price":tp1_price,"reason":"Fibo_1.272ext"},
                {"pct":50,"price":tp_final, "reason":"Fibo_1.618ext"},
            ]
        else:
            tp_main     = (prz_opposite["prz_mid"] if prz_opposite and 0<prz_opposite["prz_mid"]<entry_price
                           else (pdl if pdl and pdl<entry_price else entry_price-atr*3.0))
            tp_final    = round(min(tp_main, entry_price-atr*1.5), 2)
            tp1_price   = round(min(bb["lower"], entry_price-atr*0.5), 2)
            tp2_price   = round(min(bb["mid"],   entry_price-atr*1.0), 2)
            fallback_tp = round(entry_price-atr*4.0, 2)
            partial = [
                {"pct":50,"price":tp1_price,"reason":"BB_Lower"},
                {"pct":30,"price":tp2_price,"reason":"BB_Mid"},
                {"pct":20,"price":tp_final, "reason":"PDL_PRZ"},
        ]

    now = datetime.now(BKK).strftime("%Y-%m-%d %H:%M:%S")

    try:
        from early_warning import alert_signal_ready
        # [FIX, not opt-in -- confirmed bug, 2026-09-07] Only the main
        # traded SYMBOL is actually EA-executed -- active_symbol differs
        # from it ONLY when this call came from the extra-symbol scan
        # (symbol_label set, e.g. BTCUSD/US100/JPN225 via
        # run_extra_symbol_pass()), none of which are wired to
        # auto-execution. See alert_signal_ready()'s ea_executes docstring
        # for the exact contradiction this fixes.
        alert_signal_ready(active_symbol, direction, sig_type, final_score,
                           price, sl, tp_final, prz_name, session,
                           ea_executes=(active_symbol == SYMBOL))
    except Exception: pass

    return CloudSignal(
        action="OPEN", direction=direction, signal_type=sig_type,
        entry=round(entry_price,2), sl=sl, be_price=be_price,
        trail_from=round(bb["mid"],2), tp_final=tp_final,
        partial=partial, pattern=prz_name,
        score=score_result.bucket_a + score_result.bucket_b + score_result.bucket_c,
        context_adj=score_result.bucket_e,
        final_score=final_score,
        layer=1, session=session, timestamp=now,
        fallback_sl=sl, fallback_tp=fallback_tp,
        st_h4=cascade["st_h4"], st_1h=cascade["st_1h"], st_15m=cascade["st_15m"],
        score_breakdown=score_result.breakdown,
        auto_fibo_direction=(auto_fibo.direction if auto_fibo else ""),
        auto_fibo_entry_zone_lo=(round(auto_fibo.zone_lo, 2) if auto_fibo else 0.0),
        auto_fibo_entry_zone_hi=(round(auto_fibo.zone_hi, 2) if auto_fibo else 0.0),
        auto_fibo_ext_target=(round(auto_fibo.ext_target, 2) if auto_fibo else 0.0),
        sweep_wick_entry_used=bool(sweep_wick_entry_enabled and sweep_entry is not None),
        swing_high_ref=(round(swing_high_for_fibo, 2) if swing_high_for_fibo else 0.0),
        swing_low_ref=(round(swing_low_for_fibo, 2) if swing_low_for_fibo else 0.0),
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
        "auto_fibo_direction":     sig.auto_fibo_direction,
        "auto_fibo_entry_zone_lo": sig.auto_fibo_entry_zone_lo,
        "auto_fibo_entry_zone_hi": sig.auto_fibo_entry_zone_hi,
        "auto_fibo_ext_target":    sig.auto_fibo_ext_target,
        "sweep_wick_entry_used":   sig.sweep_wick_entry_used,
        "swing_high_ref":          sig.swing_high_ref,
        "swing_low_ref":           sig.swing_low_ref,
    }
