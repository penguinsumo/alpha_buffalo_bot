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

import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass

BKK = timezone(timedelta(hours=7))

# ── Trend States ──────────────────────────────────────────
IMPULSE_UP   = "Impulse Δ+"
IMPULSE_DOWN = "Impulse Δ-"
PULLBACK_UP  = "Pullback ↘️"
PULLBACK_DOWN= "Pullback ↗️"
SIDEWAYS     = "Sideways ➡️"
PRESSURE_SELL= "Selling Pressure"
PRESSURE_BUY = "Buying Pressure"


@dataclass
class TFTrend:
    tf:        str    # M15 / H1 / H4
    state:     str    # Impulse/Pullback/Sideways
    emoji:     str
    pressure:  str    # Selling/Buying/None
    ema20:     float
    ema50:     float
    price:     float


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
    if vol_spike and bearish_candle: pressure = PRESSURE_SELL
    elif vol_spike and bullish_candle: pressure = PRESSURE_BUY

    # ── State ─────────────────────────────────────────────
    if ema_up and hh and hl:
        state = IMPULSE_UP;   emoji = "⬆️"
    elif ema_down and ll and lh:
        state = IMPULSE_DOWN; emoji = "⬇️"
    elif ema_up and (lh or ll):
        state = PULLBACK_UP;  emoji = "↘️"
    elif ema_down and (hh or hl):
        state = PULLBACK_DOWN;emoji = "↗️"
    else:
        state = SIDEWAYS;     emoji = "➡️"

    return TFTrend(
        tf=tf_name, state=state, emoji=emoji,
        pressure=pressure, ema20=ema20, ema50=ema50, price=price,
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

    if buy_count >= 2:   bias = "BUY"
    elif sell_count >= 2: bias = "SELL"
    else:                bias = "NEUTRAL"

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
    )


def format_trend_message(tr: TrendResult) -> str:
    """Format Trend Update สำหรับ Telegram"""

    lines = [
        "📊 " + tr.symbol + " TREND UPDATE",
        "━━━━━━━━━━━━━━━━━━━━━",
        "🕐 Session : " + tr.session,
        "💰 Price   : " + "{:,.2f}".format(tr.price),
        "",
    ]

    # TF rows
    for tf in [tr.m15, tr.h1, tr.h4]:
        tf_emoji = "📈" if "Δ+" in tf.state or "↗️" in tf.emoji else \
                   "📉" if "Δ-" in tf.state or "↘️" in tf.emoji else "➡️"
        line = tf_emoji + " " + tf.tf + "  : " + tf.state
        lines.append(line)

    # Pressure alerts
    pressures = []
    for tf in [tr.m15, tr.h1, tr.h4]:
        if tf.pressure:
            pressures.append("⚡ " + tf.pressure + " " + tf.tf)
    if pressures:
        lines.append("")
        lines.extend(pressures)

    lines.append("")

    # Action
    if tr.action == "WAIT_AND_SEE":
        lines.append("⏳ Wait and See...")
    elif tr.action == "WATCH_SETUP":
        bias_sym = "Δ+" if tr.bias == "BUY" else "Δ-" if tr.bias == "SELL" else "~"
        lines.append("👀 Watch for " + bias_sym + " Setup...")

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
) -> str:
    """Format Signal Alert — Δ+ / Δ- แทน BUY/SELL"""

    now   = datetime.now(BKK).strftime("%a %d %b %Y | %H:%M")
    delta = "Δ+" if direction == "BUY" else "Δ-"
    sniper= signal_type == "V5_SNIPER"

    tp1_tf = "M15 ~45min" if sniper else "M15 ~30min"
    tp2_tf = "H1  ~3hr"   if sniper else "H1  ~2hr"

    sl_zone_lo = round(sl - 2.0, 1)
    sl_zone_hi = round(sl, 1)

    lines = [
        ("🎯 " if sniper else "") + delta + " ALPHA BUFFALO V5",
        "━━━━━━━━━━━━━━━━━━━━━",
        "📌 Asset    : XAUUSD",
        "📊 Type     : " + signal_type,
    ]

    if sniper and pattern:
        lines.append("🦋 Pattern  : " + pattern)

    lines += [
        "🎯 Entry    : ~" + "{:,.2f}".format(entry),
        "🛡️ SL Zone  : " + str(sl_zone_lo) + " - " + str(sl_zone_hi),
        "🎯 TP1      : " + "{:,.1f}".format(tp1) + "  (" + tp1_tf + ")",
        "🎯 TP2      : " + "{:,.1f}".format(tp2) + "  (" + tp2_tf + ")",
    ]

    if sniper:
        lines.append("📈 Score    : " + str(score) + "/10")

    lines += [
        "⏰ " + now,
        "━━━━━━━━━━━━━━━━━━━━━",
        "✅ EA Executing",
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
        return True
    return False
