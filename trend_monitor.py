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
from signal_engine import (
    scan_harmonic, get_kivanc_swing_zone, find_nearest_zone_rvol,
    build_harmonic_forecast, compute_cascade,
)

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

# ── Estimate Entry / big-picture PRZ zone (Auto Fibo 144/1.272 style) ──
# display. Ported from the Pine multi-asset fork's Estimate Entry feature
# (same Auto Fibo 144/1.272 methodology, not kivanc_vsaob.py's small-pivot
# Golden Zone) -- see auto_fibo_entry.py. Display-only: it never changes
# `bias`/`action`, it just adds informational lines to the Telegram Trend
# Update showing the same big-picture PRZ zone the Pine dashboard shows.
#
# [CHANGED 9 ก.ย. 2026] Default flipped OFF -> ON. Root cause this exists
# for: the owner asked for the bot to actually show/track the big-picture
# PRZ zone AHEAD of a V5 signal firing ("แต่เป้า prz ต้องทราบเพื่อเตรียม v5
# ต้องมี"), so it needs to be visible in the routine Trend Update, not just
# computed silently. Paired with the same-day fix feeding this off df_4h
# instead of df_15m below -- before that fix this was a ~1.5-day M15 window
# that didn't match the Pine indicator's ~1-month view, so turning it on
# alone would have shown the wrong zone. Set
# ALPHA_TREND_AUTO_FIBO_ENABLED=false to restore the old display-off behavior.
AUTO_FIBO_ENABLED = os.getenv("ALPHA_TREND_AUTO_FIBO_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# ── Support/Resistance RVOL (VSA volume strength) display [ADDED 10 ก.ย.
# 2026, owner's request] ─────────────────────────────────────────────
# Owner: "เราจะมาดูเรื่องการเปรียบเทียบ VSA volume ระหว่างแนวรับกับแนวต้าน
# เพื่อดูว่าแต่ละ zone ไหนมีความแข็งแกร่งมากกว่ากัน" -- compare VSA volume
# between the nearest support (below price) and nearest resistance (above
# price) across all three existing zone sources (harmonic PRZ, Kivanc swing
# zone, Auto Fibo big-picture), to show which side is more likely to hold
# ("ถ้าหลุดแนวหมายถึง SL ที่เราตั้งเอาไม่อยู่" -- if the level breaks, the SL
# we set won't hold). Display-only in the Telegram Trend Update, same
# opt-in-but-default-ON precedent as AUTO_FIBO_ENABLED above -- never gates
# bias/action. Set ALPHA_TREND_ZONE_RVOL_ENABLED=false to turn it off.
ZONE_RVOL_ENABLED = os.getenv("ALPHA_TREND_ZONE_RVOL_ENABLED", "true").lower() in {"1", "true", "yes", "on"}

# ── Harmonic Pattern Forecast (big-picture "awareness") [ADDED 10 ก.ย.
# 2026, owner's request] ─────────────────────────────────────────────
# Owner: "จะทำยังงงัยให้ระบบเรา awareness เองได้ว่าเรามีถาะใหญ่เปนแบบที่
# คุณหามา เพื่อที่จะคาดการณ์ทิศทางใน h4 แท่งต่อ แล้วกลายเปนภาพ new day โดยมี
# harmonic เปนตัวชี้นำ" -- wants the system to routinely surface the same
# "which harmonic pattern is price in right now, and what's next" view
# used to anticipate the next H4 candle's direction. Confirmed scope:
#   - Show the ACTIVE trend-aligned pattern (if price is inside one) + the
#     NEXT trend-aligned target ahead + a confidence-ranked list of EVERY
#     pattern found (see signal_engine.build_harmonic_forecast()).
#   - Detail is OWNER-ONLY for now -- format_harmonic_forecast_message()
#     below, sent to ADMIN_ID only by alpha_buffalo_signal.py, never
#     broadcast to the general room. format_trend_message()'s own PRZ/
#     Zone Strength blocks above are unaffected by this flag.
#   - DOES affect this module's `action` field (escalates to
#     PATTERN_ACTIVE below) -- but never touches compute_signal() or
#     execution, which stay governed entirely by score_manager's own
#     thresholds; this is a display/labeling change only.
# Set ALPHA_TREND_HARMONIC_FORECAST_ENABLED=false to turn the whole thing
# off (computation, action escalation, and the owner-only message all
# stop).
HARMONIC_FORECAST_ENABLED = os.getenv("ALPHA_TREND_HARMONIC_FORECAST_ENABLED", "true").lower() in {"1", "true", "yes", "on"}


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
    zone_rvol: Optional[dict] = None   # opt-in, see ZONE_RVOL_ENABLED -- {"support": {...}/None, "resistance": {...}/None}
    harmonic_forecast: Optional[dict] = None   # opt-in, see HARMONIC_FORECAST_ENABLED -- {"active": {...}/None, "next_target": {...}/None, "ranked": [...]}


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

    # Big-picture PRZ zone (Auto Fibo 144/1.272 style, display-only) —
    # [FIX 9 ก.ย. 2026] computed on df_4h (144 confirmed 4H bars ~= 24 days),
    # not df_15m (was ~1.5 days -- far too short to be the "big picture" the
    # Pine indicator's ~1-month chart shows). Wrapped in try/except so a
    # computation issue can never break the Trend Update.
    auto_fibo = None
    if AUTO_FIBO_ENABLED:
        try:
            auto_fibo = compute_auto_fibo(df_4h)
        except Exception:
            auto_fibo = None

    # Shared harmonic scan -- feeds BOTH Zone Strength (RVOL) and the
    # Harmonic Forecast below, computed once (when either is enabled) so
    # H1/H4 aren't scanned for patterns twice per cycle.
    prz_list = None
    if ZONE_RVOL_ENABLED or HARMONIC_FORECAST_ENABLED:
        try:
            prz_list = scan_harmonic(df_1h, df_4h)
        except Exception:
            prz_list = None

    # Support/Resistance RVOL (VSA volume strength, display-only) — see
    # ZONE_RVOL_ENABLED above. Pulls the nearest support/resistance from
    # ALL THREE existing zone sources and compares each zone-forming
    # candle's own volume against its recent average. Wrapped in
    # try/except, same as auto_fibo above, so a computation issue can
    # never break the Trend Update.
    zone_rvol = None
    if ZONE_RVOL_ENABLED:
        try:
            k_high, k_low, k_high_idx, k_low_idx = get_kivanc_swing_zone(
                df_1h, pivot_n=10, return_idx=True
            )
            zone_rvol = find_nearest_zone_rvol(
                price, prz_list or [], df_1h, df_4h,
                k_high, k_low, k_high_idx, k_low_idx,
                auto_fibo,
            )
        except Exception:
            zone_rvol = None

    # Harmonic Pattern Forecast (big-picture "awareness", owner-only
    # display) — see HARMONIC_FORECAST_ENABLED above. Uses the SAME
    # cascade direction compute_signal() itself uses (compute_cascade()),
    # so "active" here mirrors exactly which pattern a live V5_SNIPER
    # would actually bind to. Skipped entirely when the cascade is
    # NEUTRAL, same as compute_signal() (which returns None outright in
    # that case) -- there is no trend-aligned direction to forecast
    # against.
    harmonic_forecast = None
    if HARMONIC_FORECAST_ENABLED:
        try:
            cascade_direction = compute_cascade(df_4h, df_1h, df_15m)["direction"]
            if cascade_direction != "NEUTRAL":
                harmonic_forecast = build_harmonic_forecast(
                    prz_list or [], cascade_direction, price, auto_fibo,
                )
        except Exception:
            harmonic_forecast = None

    # ── Action ────────────────────────────────────────────
    pressures = [t.pressure for t in [m15, h1, h4] if t.pressure]
    if len(pressures) >= 2:
        action = "WATCH_SETUP"
    elif bias != "NEUTRAL":
        action = "WATCH_SETUP"
    else:
        action = "WAIT_AND_SEE"

    # Harmonic Pattern Forecast escalation [ADDED 10 ก.ย. 2026, owner's
    # request: "ให้มีผลต่อ action ด้วย"] -- when price is CURRENTLY sitting
    # inside a harmonic PRZ that agrees with the cascade direction (the
    # exact same match compute_signal() would bind to for a live
    # V5_SNIPER pattern label -- see harmonic_forecast["active"] above),
    # that is a materially stronger, more specific setup than the generic
    # HH/HL + pressure vote above, so it overrides WATCH_SETUP/
    # WAIT_AND_SEE with PATTERN_ACTIVE. Display-only escalation -- see
    # format_trend_message() below for the generic (non-admin-leaking)
    # wording shown to the general room, and
    # format_harmonic_forecast_message() for the owner-only detail behind
    # it. Never touches compute_signal()/execution -- those stay governed
    # entirely by score_manager's own thresholds, independent of this.
    if HARMONIC_FORECAST_ENABLED and harmonic_forecast and harmonic_forecast.get("active"):
        action = "PATTERN_ACTIVE"

    return TrendResult(
        symbol=symbol, session=session, price=price,
        m15=m15, h1=h1, h4=h4,
        bias=bias, action=action,
        timestamp=now.strftime("%a %d %b %Y | %H:%M"),
        auto_fibo=auto_fibo,
        zone_rvol=zone_rvol,
        harmonic_forecast=harmonic_forecast,
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

    # Big-picture PRZ zone (Auto Fibo 144/1.272 on 4H, via ALPHA_TREND_AUTO_FIBO_ENABLED)
    if AUTO_FIBO_ENABLED and tr.auto_fibo:
        af = tr.auto_fibo
        dir_label = "UP-SWING (BUY zone)" if af.direction == DIRECTION_UP else "DOWN-SWING (SELL zone)"
        lines.append("")
        lines.append(f"🧭 PRZ Zone (Big Picture 4H) : {dir_label}")
        lines.append(f"    Zone : {af.zone_lo:,.2f} - {af.zone_hi:,.2f}  |  Ext : {af.ext_target:,.2f}")

    # Support/Resistance RVOL (VSA volume strength, via ALPHA_TREND_ZONE_RVOL_ENABLED)
    # — nearest support below price vs nearest resistance above price,
    # each zone's own RVOL (that zone-forming candle's volume vs its recent
    # average) as a read on how likely that level is to actually hold if
    # price reaches it. Display-only, never changes bias/action.
    if ZONE_RVOL_ENABLED and tr.zone_rvol:
        sup = tr.zone_rvol.get("support")
        res = tr.zone_rvol.get("resistance")
        if sup or res:
            lines.append("")
            lines.append("📊 Zone Strength (RVOL)")
            if sup:
                rvol_str = f"{sup['rvol']:.2f}x" if sup["rvol"] is not None else "N/A"
                lines.append(f"    🟢 Support    : {sup['level']:,.2f}  ({sup['source']})  RVOL {rvol_str}")
            if res:
                rvol_str = f"{res['rvol']:.2f}x" if res["rvol"] is not None else "N/A"
                lines.append(f"    🔴 Resistance : {res['level']:,.2f}  ({res['source']})  RVOL {rvol_str}")
            if sup and res and sup["rvol"] is not None and res["rvol"] is not None:
                if sup["rvol"] > res["rvol"]:
                    verdict = "Support looks stronger (higher RVOL) — more likely to hold"
                elif res["rvol"] > sup["rvol"]:
                    verdict = "Resistance looks stronger (higher RVOL) — more likely to hold"
                else:
                    verdict = "Support/Resistance about equal strength"
                lines.append(f"    ⚖️ {verdict}")

    lines.append("")

    # Action
    if tr.action == "WAIT_AND_SEE":
        lines.append("⏳ Wait and See...")
    elif tr.action == "PATTERN_ACTIVE":
        # [ADDED 10 ก.ย. 2026] Escalated by the Harmonic Pattern Forecast
        # (see HARMONIC_FORECAST_ENABLED / analyze_trend()) -- deliberately
        # generic wording here, same as WATCH_SETUP below. The actual
        # pattern name/PRZ/confidence behind this stays owner-only, see
        # format_harmonic_forecast_message().
        bias_sym = "Δ+" if tr.bias == "BUY" else ("Δ-" if tr.bias == "SELL" else "~")
        lines.append(f"🔥 Strong {bias_sym} Setup Forming...")
    elif tr.action == "WATCH_SETUP":
        bias_sym = "Δ+" if tr.bias == "BUY" else ("Δ-" if tr.bias == "SELL" else "~")
        lines.append(f"👀 Watch for {bias_sym} Setup...")

    lines.append("━━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️ Not financial advice. Trade at your own risk.")

    return "\n".join(lines)


def format_harmonic_forecast_message(tr: TrendResult) -> str:
    """
    [OWNER-ONLY, ADDED 10 ก.ย. 2026, owner's request] The full detail
    behind PATTERN_ACTIVE above -- the active trend-aligned pattern (if
    any), the next trend-aligned target ahead, and every harmonic pattern
    scan_harmonic() currently sees on H1+H4, ranked by confidence.
    Intentionally kept OUT of format_trend_message()'s broadcast to the
    general room -- the owner asked this detail stay owner-only for now.
    Caller is responsible for sending this ONLY to ADMIN_ID (see
    alpha_buffalo_signal.py's signal_loop()).

    confidence % is a RULE-BASED HEURISTIC (pattern-type tier + timeframe
    weight + cascade-direction agreement + Auto Fibo big-picture zone
    confluence -- see signal_engine.score_harmonic_confidence()), NOT a
    backtested statistical win-rate or ML-calibrated probability. It's a
    ranking aid to compare the patterns the system already found against
    each other -- a proper backtested/learned version of this is a
    separate "AI learning" roadmap item the owner mentioned, not this.
    """
    fc = tr.harmonic_forecast
    if not fc or not fc.get("ranked"):
        return (
            f"🔮 {tr.symbol} HARMONIC FORECAST (Owner Only)\n"
            "━━━━━━━━━━━━━━━━━━━━━\n"
            "No trend-aligned harmonic pattern data available right now "
            "(cascade neutral, or no pattern detected on H1/H4)."
        )

    lines = [
        f"🔮 {tr.symbol} HARMONIC FORECAST (Owner Only)",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"💰 Price   : {tr.price:,.2f}",
        "",
    ]

    active = fc.get("active")
    if active:
        lines.append("🎯 ACTIVE NOW (price inside this PRZ, matches trend):")
        lines.append(
            f"    {active['name']} ({active['tf']}) {active['direction']}  "
            f"PRZ {active['prz_low']:,.2f}-{active['prz_high']:,.2f}  "
            f"Confidence: {active['confidence']:.0f}%"
        )
    else:
        lines.append("🎯 ACTIVE NOW : none — price isn't inside any trend-aligned PRZ yet")
    lines.append("")

    next_target = fc.get("next_target")
    if next_target:
        lines.append("⏭️ NEXT TARGET (nearest trend-aligned PRZ ahead):")
        lines.append(
            f"    {next_target['name']} ({next_target['tf']}) {next_target['direction']}  "
            f"PRZ {next_target['prz_low']:,.2f}-{next_target['prz_high']:,.2f}  "
            f"~{next_target['distance']:,.2f} away  Confidence: {next_target['confidence']:.0f}%"
        )
        lines.append("")

    lines.append("📋 All detected patterns (ranked by confidence):")
    for e in fc["ranked"][:8]:
        if e["is_active"]:
            flag = "  [ACTIVE]"
        elif not e["matches_cascade"]:
            flag = "  (counter-trend)"
        else:
            flag = ""
        lines.append(
            f"    {e['confidence']:>3.0f}%  {e['name']} ({e['tf']}) {e['direction']}  "
            f"{e['prz_low']:,.2f}-{e['prz_high']:,.2f}{flag}"
        )

    lines.append("")
    lines.append("⚠️ Confidence = rule-based heuristic (pattern tier + "
                  "timeframe + cascade agreement + Auto Fibo confluence), "
                  "NOT a backtested win-rate. Ranking aid only.")

    return "\n".join(lines)


def format_multi_symbol_trend_digest(results) -> str:
    """
    [OPT-IN, ALPHA_EXTRA_SYMBOLS_TREND_DIGEST_ENABLED] One combined Telegram
    message covering the Trend Update for several symbols at once (BTC/
    US100/JPN225 today) instead of sending format_trend_message() once per
    symbol on that symbol's own independent timer. This is purely a
    presentation change to reduce message volume in the room -- it carries
    the same per-symbol facts (session/price/bias, per-TF state, Pressure
    flags) as format_trend_message() does, just condensed into one message.

    Does NOT touch BUY/SELL signal messages at all: those are formatted by
    format_signal_message() and sent separately, per symbol, immediately,
    with no throttling, exactly as before -- digest mode only ever affects
    the no-signal "Trend Update" ping.

    `results` is a list of (symbol, TrendResult) pairs, in the order they
    were computed (normally ALPHA_EXTRA_SYMBOLS order). Symbols whose pass
    failed this cycle (data fetch error, etc.) are simply absent from the
    list -- the digest just covers whichever symbols came back.
    """
    lines = [
        "📊 TREND UPDATE (Multi-Symbol)",
        "━━━━━━━━━━━━━━━━━━━━━",
    ]
    for symbol, tr in results:
        bias_emoji = "📈" if tr.bias == "BUY" else ("📉" if tr.bias == "SELL" else "➡️")
        lines.append(f"{bias_emoji} {symbol}  {tr.price:,.2f}  |  {tr.session}  |  Bias: {tr.bias}")
        tf_line = "  ".join(f"{tf.tf}:{tf.state}" for tf in [tr.m15, tr.h1, tr.h4])
        lines.append(f"    {tf_line}")
        pressures = [f"⚡{tf.pressure} {tf.tf}" for tf in [tr.m15, tr.h1, tr.h4] if tf.pressure]
        if pressures:
            lines.append("    " + "  ".join(pressures))
        lines.append("")

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


def format_reentry_message(
    direction:    str,
    entry:        float,
    sl:           float,
    tp:           float,
    trade1_entry: float,
    symbol:       str = "XAUUSD",
) -> str:
    """
    [OPT-IN, ALPHA_SIGNAL_SWEEP_REENTRY_ENABLED] Round-2 re-entry alert
    after a confirmed Sweep Wick Entry (see sweep_reentry.py). Carries
    Trade 1's own final TP forward as this trade's target (same
    directional thesis), and always includes the breakeven instruction
    for Trade 1 -- the Round-2 confirmation is what validates moving
    Trade 1's SL there, so the two are never split into separate messages.
    """
    now   = datetime.now(BKK).strftime("%a %d %b %Y | %H:%M")
    delta = "Δ+" if direction == "BUY" else "Δ-"

    lines = [
        f"🔁 {delta} ALPHA BUFFALO V5 — ROUND 2 RE-ENTRY",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"📌 Asset    : {symbol}",
        f"📊 Type     : SWEEP_REENTRY",
        f"🎯 Entry    : ~{entry:,.2f}",
        f"🛡️ SL       : {sl:,.2f}",
        f"🎯 TP       : {tp:,.2f}  (Trade 1's target, unchanged)",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"🔒 Trade 1 (Entry {trade1_entry:,.2f}): move SL to breakeven "
        f"({trade1_entry:,.2f}) now — this reversal confirms it.",
        "━━━━━━━━━━━━━━━━━━━━━",
        f"⏰ {now}",
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
