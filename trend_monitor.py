"""
trend_monitor.py — Alpha Buffalo v5
Trend Analysis: M15 / H1 / H4
ส่ง Trend Update ทุก Session เปิด

Output:
- Impulse Δ+ / Δ- 
- Pullback
- Selling/Buying Pressure
- Wait and See
"""

import logging
import os
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

from auto_fibo_entry import compute_auto_fibo, AutoFiboEstimate, DIRECTION_UP, DIRECTION_DOWN

# ── Logging Setup ──────────────────────────────────────────
logger = logging.getLogger(__name__)

BKK = timezone(timedelta(hours=7))

# ── Trend States ──────────────────────────────────────────
IMPULSE_UP   = "Impulse Δ+"
IMPULSE_DOWN = "Impulse Δ-"
PULLBACK_UP  = "Pullback ↘️"
PULLBACK_DOWN= "Pullback ↗️"
SIDEWAYS     = "Sideways ➡️"
PRESSURE_SELL= "Selling Pressure"
PRESSURE_BUY = "Buying Pressure"

# ── Dow Theory swing-structure trend detection (opt-in) ────────────────
# Default OFF: the original recent_highs[-1] vs recent_highs[-5] proxy
# below (5 bars back, no real pivot confirmation) stays byte-identical
# unless this is explicitly turned on. When enabled, HH/HL/LH/LL come from
# CONFIRMED swing pivots instead -- a bar only counts as a swing high/low
# once `pivot_bars` bars exist on both sides that don't exceed it (the
# same left/right definition Pine's ta.pivothigh/ta.pivotlow use) -- i.e.
# real Dow Theory structure (a run of higher highs + higher lows = uptrend,
# lower highs + lower lows = downtrend), not a fixed-lookback proxy.
DOW_THEORY_ENABLED = os.getenv("ALPHA_TREND_DOW_THEORY_ENABLED", "false").lower() in {"1", "true", "yes", "on"}
DOW_THEORY_PIVOT_BARS = int(os.getenv("ALPHA_TREND_DOW_THEORY_PIVOT_BARS", "3"))

STRUCTURE_UPTREND   = "HH_HL"   # confirmed higher high AND higher low
STRUCTURE_DOWNTREND = "LH_LL"   # confirmed lower high AND lower low
STRUCTURE_MIXED     = "MIXED"   # confirmed swings but highs/lows disagree
STRUCTURE_UNKNOWN   = ""        # disabled, or not enough confirmed swings yet

# ── Estimate Entry (Auto Fibo 144/1.272 style) display (opt-in) ────────
# Default OFF: adds zero output/behavior change unless explicitly turned on.
# Ported from the Pine multi-asset fork's Estimate Entry feature (same Auto
# Fibo 144/1.272 methodology, not kivanc_vsaob.py's small-pivot Golden
# Zone) -- see auto_fibo_entry.py. Display-only here: it never changes
# `bias`/`action`, it just adds informational lines to the Telegram Trend
# Update showing the same estimate the Pine dashboard shows.
AUTO_FIBO_ENABLED = os.getenv("ALPHA_TREND_AUTO_FIBO_ENABLED", "false").lower() in {"1", "true", "yes", "on"}


def _confirmed_swing_pivots(series: "pd.Series", pivot_bars: int, is_high: bool):
    """Return up to the last two CONFIRMED swing pivot values in `series`,
    most-recent first. A bar at index i is a confirmed pivot only once
    `pivot_bars` bars exist on both sides of it (so it can't repaint), and
    it must be the highest (is_high=True) or lowest (is_high=False) value
    in that [i-pivot_bars, i+pivot_bars] window.
    """
    values = series.values
    n = len(values)
    pivots = []
    for i in range(n - 1 - pivot_bars, pivot_bars - 1, -1):
        window = values[i - pivot_bars: i + pivot_bars + 1]
        centre = values[i]
        is_pivot = (centre == window.max()) if is_high else (centre == window.min())
        if is_pivot:
            pivots.append(float(centre))
            if len(pivots) == 2:
                break
    return pivots  # [] , [most_recent] , or [most_recent, prior]


def classify_dow_structure(df: "pd.DataFrame", pivot_bars: int = 3) -> str:
    """Classify swing structure per Dow Theory using the last two CONFIRMED
    swing pivots: higher high + higher low = uptrend, lower high + lower
    low = downtrend. Anything else (not enough confirmed swings yet, or
    highs/lows disagreeing) is reported as mixed/unknown rather than
    guessed at.
    """
    if df is None or len(df) < pivot_bars * 2 + 3:
        return STRUCTURE_UNKNOWN
    highs = _confirmed_swing_pivots(df["high"], pivot_bars, is_high=True)
    lows  = _confirmed_swing_pivots(df["low"],  pivot_bars, is_high=False)
    if len(highs) < 2 or len(lows) < 2:
        return STRUCTURE_UNKNOWN
    higher_high, lower_high = highs[0] > highs[1], highs[0] < highs[1]
    higher_low,  lower_low  = lows[0]  > lows[1],  lows[0]  < lows[1]
    if higher_high and higher_low:
        return STRUCTURE_UPTREND
    if lower_high and lower_low:
        return STRUCTURE_DOWNTREND
    return STRUCTURE_MIXED


@dataclass
class TFTrend:
    tf:        str    # M15 / H1 / H4
    state:     str    # Impulse/Pullback/Sideways
    emoji:     str
    pressure:  str    # Selling/Buying/None
    ema20:     float
    ema50:     float
    price:     float
    dow:       str = ""   # Dow Theory swing structure: HH_HL / LH_LL / MIXED / "" (disabled or unknown)


@dataclass
class TrendResult:
    symbol:    str
    session:   str
    price:     float
    m15:       TFTrend
    h1:        TFTrend
    h4:        TFTrend
    bias:      str     # "BUY" / "SELL" / "NEUTRAL"
    action:    str     # "WAIT_AND_SEE" / "WATCH_SETUP" / "SIGNAL_READY"
    timestamp: str
    auto_fibo: Optional[AutoFiboEstimate] = None   # opt-in, see AUTO_FIBO_ENABLED


def calc_tf_trend(df: pd.DataFrame, tf_name: str) -> TFTrend:
    """วิเคราะห์ trend ของ timeframe นั้น"""
    if df is None or len(df) < 50:
        return TFTrend(tf=tf_name, state=SIDEWAYS, emoji="➡️",
                      pressure="", ema20=0, ema50=0, price=0)

    close  = df["close"]
    high   = df["high"]
    low    = df["low"]
    volume = df["volume"] if "volume" in df.columns else pd.Series([0]*len(df))

    ema20 = float(close.ewm(span=20).mean().iloc[-1])
    ema50 = float(close.ewm(span=50).mean().iloc[-1])
    price = float(close.iloc[-1])

    dow_structure = classify_dow_structure(df, DOW_THEORY_PIVOT_BARS) if DOW_THEORY_ENABLED else STRUCTURE_UNKNOWN

    if DOW_THEORY_ENABLED:
        # Real swing-pivot Dow Theory structure instead of the 5-bar proxy below.
        hh = dow_structure == STRUCTURE_UPTREND
        hl = dow_structure == STRUCTURE_UPTREND
        lh = dow_structure == STRUCTURE_DOWNTREND
        ll = dow_structure == STRUCTURE_DOWNTREND
    else:
        # ── Higher High / Lower Low Structure ────────────────
        recent_highs = high.tail(10).values
        recent_lows  = low.tail(10).values

        hh = recent_highs[-1] > recent_highs[-5]   # Higher High
        hl = recent_lows[-1]  > recent_lows[-5]    # Higher Low
        lh = recent_highs[-1] < recent_highs[-5]   # Lower High
        ll = recent_lows[-1]  < recent_lows[-5]    # Lower Low

    # ── EMA Trend ─────────────────────────────────────────
    ema_up   = ema20 > ema50 and price > ema20
    ema_down = ema20 < ema50 and price < ema20

    # ── Volume Analysis ───────────────────────────────────
    vol_curr = float(volume.iloc[-1])
    vol_avg  = float(volume.tail(20).mean()) if volume.sum() > 0 else 0
    vol_spike = vol_curr > vol_avg * 1.5 if vol_avg > 0 else False

    bearish_candle = float(close.iloc[-1]) < float(df["open"].iloc[-1])
    bullish_candle = float(close.iloc[-1]) > float(df["open"].iloc[-1])

    # ── Pressure ──────────────────────────────────────────
    pressure = ""
    if vol_spike and bearish_candle:
        pressure = PRESSURE_SELL
    elif vol_spike and bullish_candle:
        pressure = PRESSURE_BUY

    # ── State ─────────────────────────────────────────────
    if ema_up and hh and hl:
        state = IMPULSE_UP
        emoji = "⬆️"
    elif ema_down and ll and lh:
        state = IMPULSE_DOWN
        emoji = "⬇️"
    elif ema_up and (lh or ll):
        state = PULLBACK_UP
        emoji = "↘️"
    elif ema_down and (hh or hl):
        state = PULLBACK_DOWN
        emoji = "↗️"
    else:
        state = SIDEWAYS
        emoji = "➡️"

    return TFTrend(
        tf=tf_name, state=state, emoji=emoji,
        pressure=pressure, ema20=ema20, ema50=ema50, price=price,
        dow=dow_structure,
    )


def analyze_trend(
    df_4h:  pd.DataFrame,
    df_1h:  pd.DataFrame,
    df_15m: pd.DataFrame,
    symbol: str = "XAUUSD",
) -> TrendResult:
    """วิเคราะห์ trend ทั้ง 3 TF"""

    now     = datetime.now(BKK)
    session = get_session(now)
    price   = float(df_15m["close"].iloc[-1]) if df_15m is not None else 0

    m15 = calc_tf_trend(df_15m, "M15")
    h1  = calc_tf_trend(df_1h,  "H1")
    h4  = calc_tf_trend(df_4h,  "H4")

    # ── Overall Bias ──────────────────────────────────────
    buy_count  = sum(1 for t in [m15, h1, h4] if "Δ+" in t.state or "↗️" in t.emoji)
    sell_count = sum(1 for t in [m15, h1, h4] if "Δ-" in t.state or "↘️" in t.emoji)

    if buy_count >= 2:
        bias = "BUY"
    elif sell_count >= 2:
        bias = "SELL"
    else:
        bias = "NEUTRAL"

    # Dow Theory confluence (opt-in): per-request, M15 and H4 agreeing on
    # real swing structure is a stronger signal than the EMA/HH-proxy vote
    # above, so when both timeframes confirm the same structure it sets
    # the bias directly instead of just adding to the vote count.
    if DOW_THEORY_ENABLED and m15.dow and m15.dow == h4.dow:
        if m15.dow == STRUCTURE_UPTREND:
            bias = "BUY"
        elif m15.dow == STRUCTURE_DOWNTREND:
            bias = "SELL"

    # Estimate Entry (Auto Fibo 144/1.272 style, opt-in, display-only) —
    # computed on M15 (the same trigger TF the Pine version uses). Wrapped
    # in try/except so a computation issue can never break the Trend Update.
    auto_fibo = None
    if AUTO_FIBO_ENABLED:
        try:
            auto_fibo = compute_auto_fibo(df_15m)
        except Exception:
            auto_fibo = None

    # ── Action ────────────────────────────────────────────
    pressures = [t.pressure for t in [m15, h1, h4] if t.pressure]
    if len(pressures) >= 2:
        action = "WATCH_SETUP"
    elif bias != "NEUTRAL":
        action = "WATCH_SETUP"
    else:
        action = "WAIT_AND_SEE"

    return TrendResult(
        symbol=symbol, session=session, price=price,
        m15=m15, h1=h1, h4=h4,
        bias=bias, action=action,
        timestamp=now.strftime("%a %d %b %Y | %H:%M"),
        auto_fibo=auto_fibo,
    )


def format_trend_message(tr: TrendResult) -> str:
    """Format Trend Update สำหรับ Telegram"""

    lines = [
        f"📊 {tr.symbol} TREND UPDATE",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🕐 Session : {tr.session}",
        f"💰 Price   : {tr.price:,.2f}",
        "",
    ]

    # TF rows
    for tf in [tr.m15, tr.h1, tr.h4]:
        tf_emoji = ("📈" if "Δ+" in tf.state or "↗️" in tf.emoji else
                    "📉" if "Δ-" in tf.state or "↘️" in tf.emoji else "➡️")
        line = f"{tf_emoji} {tf.tf}  : {tf.state}"
        lines.append(line)

    # Pressure alerts
    pressures = []
    for tf in [tr.m15, tr.h1, tr.h4]:
        if tf.pressure:
            pressures.append(f"⚡ {tf.pressure} {tf.tf}")
    if pressures:
        lines.append("")
        lines.extend(pressures)

    # Dow Theory swing structure (opt-in via ALPHA_TREND_DOW_THEORY_ENABLED)
    if DOW_THEORY_ENABLED:
        _dow_label = {
            STRUCTURE_UPTREND: "HH/HL ⬆️", STRUCTURE_DOWNTREND: "LH/LL ⬇️",
            STRUCTURE_MIXED: "Mixed ➡️", STRUCTURE_UNKNOWN: "—",
        }
        lines.append("")
        lines.append(f"🌊 Dow M15 : {_dow_label.get(tr.m15.dow, '—')}")
        lines.append(f"🌊 Dow H4  : {_dow_label.get(tr.h4.dow, '—')}")

    # Estimate Entry (Auto Fibo 144/1.272, opt-in via ALPHA_TREND_AUTO_FIBO_ENABLED)
    if AUTO_FIBO_ENABLED and tr.auto_fibo:
        af = tr.auto_fibo
        dir_label = "UP-SWING (BUY zone)" if af.direction == DIRECTION_UP else "DOWN-SWING (SELL zone)"
        lines.append("")
        lines.append(f"🧭 Est. Entry (Auto Fibo) : {dir_label}")
        lines.append(f"    Zone : {af.zone_lo:,.2f} - {af.zone_hi:,.2f}  |  Ext : {af.ext_target:,.2f}")

    lines.append("")

    # Action
    if tr.action == "WAIT_AND_SEE":
        lines.append("⏳ Wait and See...")
    elif tr.action == "WATCH_SETUP":
        bias_sym = "Δ+" if tr.bias == "BUY" else ("Δ-" if tr.bias == "SELL" else "~")
        lines.append(f"👀 Watch for {bias_sym} Setup...")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ Not financial advice. Trade at your own risk.")

    return "\n".join(lines)


def format_signal_message(
    direction:   str,
    signal_type: str,
    entry:       float,
    sl:          float,
    tp1:         float,
    tp2:         float,
    pattern:     str = "",
    score:       int = 0,
    session:     str = "",
    symbol:      str = "XAUUSD",
    ea_executes: bool = True,
) -> str:
    """Format Signal Alert — Δ+ / Δ- แทน BUY/SELL"""

    now   = datetime.now(BKK).strftime("%a %d %b %Y | %H:%M")
    delta = "Δ+" if direction == "BUY" else "Δ-"
    sniper= signal_type == "V5_SNIPER"

    tp1_tf = "M15 ~45min" if sniper else "M15 ~30min"
    tp2_tf = "H1  ~3hr"   if sniper else "H1  ~2hr"

    sl_zone_lo = round(sl - 2.0, 1)
    sl_zone_hi = round(sl, 1)

    # [FIX] TP Sort: BUY → TP1 < TP2 (ใกล้→ไกล)
    #               SELL → TP1 > TP2 (ใกล้→ไกล = ค่าน้อย→น้อยกว่า)
    tp_near, tp_far = ((min(tp1,tp2), max(tp1,tp2)) if direction=="BUY"
                       else (max(tp1,tp2), min(tp1,tp2)))

    # Guard: TP ต้องไม่ขัดทิศทาง entry
    if direction == "BUY":
        tp_near = max(tp_near, entry + 0.1)
        tp_far  = max(tp_far,  tp_near + 0.1)
    else:
        tp_near = min(tp_near, entry - 0.1)
        tp_far  = min(tp_far,  tp_near - 0.1)

    emoji_prefix = "🎯 " if sniper else ""
    lines = [
        f"{emoji_prefix}{delta} ALPHA BUFFALO V5",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Asset    : {symbol}",
        f"📊 Type     : {signal_type}",
    ]

    if sniper and pattern:
        lines.append(f"🦋 Pattern  : {pattern}")

    lines += [
        f"🎯 Entry    : ~{entry:,.2f}",
        f"🛡️ SL Zone  : {sl_zone_lo} - {sl_zone_hi}",
        f"🎯 TP1      : {tp_near:,.1f}  ({tp1_tf})",
        f"🎯 TP2      : {tp_far:,.1f}  ({tp2_tf})",
    ]

    if sniper:
        lines.append(f"📈 Score    : {score}/10")

    # ea_executes=False (used for symbols the EA does not yet trade, e.g. the
    # opt-in BTC/US100/JPN225 extra-symbol scan) must never claim an
    # automated trade is happening when none is -- say so honestly instead.
    exec_line = "✅ EA Executing" if ea_executes else "📋 Signal only — not wired to auto-execution yet"

    lines += [
        f"⏰ {now}",
        "━━━━━━━━━━━━━━━━━━━━━",
        exec_line,
        "⚠️ Not financial advice. Trade at your own risk.",
    ]

    return "\n".join(lines)


def format_welcome_message() -> str:
    """ข้อความ /start พร้อม Disclaimer เต็ม"""
    return (
        "🐃 ALPHA BUFFALO V5\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "Gold Trading Signal System\n"
        "XAUUSD | Cloud-Driven AI\n\n"
        "Commands:\n"
        "/price   — ราคาปัจจุบัน\n"
        "/context — Market Context\n"
        "/setup   — Setup Status\n"
        "/status  — Bot Status\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ RISK DISCLAIMER\n"
        "Signals are for informational\n"
        "purposes only. Trading gold\n"
        "involves substantial risk of loss.\n"
        "Past performance does not\n"
        "guarantee future results.\n"
        "Not financial advice.\n"
        "Trade at your own risk."
    )


def get_session(dt: datetime) -> str:
    h = dt.astimezone(timezone.utc).hour
    if 7  <= h < 13: return "London"
    if 13 <= h < 19: return "NY"
    return "Asia"


# ── Session Trigger ───────────────────────────────────────
_last_session_alert = ""

def should_send_trend_alert(session: str) -> bool:
    """ส่ง Trend Alert เมื่อ session เปลี่ยน"""
    global _last_session_alert
    if session != _last_session_alert:
        _last_session_alert = session
        logger.debug(f"Session alert triggered: {session}")
        return True
    return False
