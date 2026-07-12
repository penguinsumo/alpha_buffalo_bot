"""
early_warning.py — Alpha Buffalo v5 Sprint 3B
Early Warning System

Stage 1: VSA Setup forming    → Alert Telegram
Stage 2: BOS confirmed        → Alert + "Entry ready"
Stage 3: Score threshold hit  → Fire Signal to EA

ไม่ต้องนั่งเฝ้า รู้ตั้งแต่ต้น manual override ได้
"""

import os
import pandas as pd
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass, field
from typing import Optional
from telegram_guard import guarded_telegram_post, telegram_market_is_open

BKK          = timezone(timedelta(hours=7))
TELEGRAM_API = f"https://api.telegram.org/bot{os.getenv('TELEGRAM_TOKEN','')}"
ADMIN_ID     = int(os.getenv("ADMIN_ID", "0"))

# ── Stage Definitions ─────────────────────────────────────
STAGE_FORMING  = 1   # VSA setup เริ่มเกิด
STAGE_BOS      = 2   # BOS confirmed
STAGE_READY    = 3   # Score threshold → ยิง signal

# cooldown ต่อ stage (วินาที)
STAGE_COOLDOWN = {
    STAGE_FORMING: 900,   # 15 นาที
    STAGE_BOS:     300,   # 5 นาที
    STAGE_READY:   600,   # 10 นาที
}


@dataclass
class WarnState:
    """State ของ Early Warning ต่อ symbol"""
    symbol:        str
    direction:     str
    stage:         int   = 0
    last_alert:    dict  = field(default_factory=dict)
    setup_price:   float = 0.0
    bos_price:     float = 0.0
    score:         int   = 0
    pattern:       str   = ""
    session:       str   = ""


# Global state per symbol
_warn_states: dict = {}


def send_telegram(msg: str, chat_id: int = None):
    if not telegram_market_is_open():
        return False
    try:
        response = guarded_telegram_post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id or ADMIN_ID,
                  "text": msg, "parse_mode": "HTML"},
            timeout=10,
        )
        return bool(response is not None and response.status_code == 200)
    except Exception as e:
        print(f"⚠️ telegram error: {e}")
        return False


def _can_alert(symbol: str, stage: int) -> bool:
    """เช็ค cooldown ก่อน alert"""
    state = _warn_states.get(symbol)
    if not state: return True
    last = state.last_alert.get(stage, 0)
    elapsed = (datetime.now(BKK).timestamp() - last)
    return elapsed > STAGE_COOLDOWN.get(stage, 600)


def _mark_alert(symbol: str, stage: int):
    if symbol in _warn_states:
        _warn_states[symbol].last_alert[stage] = datetime.now(BKK).timestamp()


# ── Stage 1: VSA Setup Forming ────────────────────────────
def check_vsa_forming(
    df_15m: pd.DataFrame,
    direction: str,
    symbol: str = "XAUUSD",
    session: str = "",
) -> bool:
    """
    เช็คว่ามี VSA Setup กำลังก่อตัว
    = Volume spike + Price near Session H/L
    """
    if len(df_15m) < 20: return False

    # Volume spike
    vol_curr = float(df_15m["volume"].iloc[-1]) if "volume" in df_15m.columns else 0
    vol_avg  = float(df_15m["volume"].tail(20).mean()) if "volume" in df_15m.columns else 0
    vol_spike = vol_curr >= vol_avg * 1.5 if vol_avg > 0 else False

    # Price near Session H/L
    window    = df_15m.tail(32)
    sess_high = float(window["high"].max())
    sess_low  = float(window["low"].min())
    price     = float(df_15m["close"].iloc[-1])

    near_high = abs(price - sess_high) / sess_high < 0.002
    near_low  = abs(price - sess_low)  / sess_low  < 0.002

    forming = vol_spike and (
        (direction == "SELL" and near_high) or
        (direction == "BUY"  and near_low)
    )

    if forming and _can_alert(symbol, STAGE_FORMING):
        # อัพเดท state
        if symbol not in _warn_states:
            _warn_states[symbol] = WarnState(symbol=symbol, direction=direction)
        state = _warn_states[symbol]
        state.direction   = direction
        state.stage       = STAGE_FORMING
        state.setup_price = price
        state.session     = session

        # Alert Stage 1
        emoji = "🟢" if direction == "BUY" else "🔴"
        msg = (
            f"⚡ VSA SETUP FORMING\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{emoji} {symbol} {direction}\n"
            f"💰 Price: {price:,.2f}\n"
            f"📊 Volume spike: {vol_curr:.0f} vs avg {vol_avg:.0f}\n"
            f"🕐 Session: {session}\n"
            f"⏳ รอ BOS confirm..."
        )
        send_telegram(msg)
        _mark_alert(symbol, STAGE_FORMING)
        print(f"⚡ Stage 1 Alert: {symbol} {direction} @ {price:.2f}")

    return forming


# ── Stage 2: BOS Confirmed ────────────────────────────────
def check_bos_confirmed(
    df_15m: pd.DataFrame,
    direction: str,
    symbol: str = "XAUUSD",
    score: int = 0,
    pattern: str = "",
) -> bool:
    """
    BOS confirmed → Alert Stage 2
    """
    if len(df_15m) < 10: return False

    # BOS = ราคาทะลุ Swing High/Low ก่อนหน้า
    n = 5
    price = float(df_15m["close"].iloc[-1])
    if direction == "BUY":
        swing_high = float(df_15m["high"].iloc[-n-1:-1].max())
        bos = float(df_15m["high"].iloc[-1]) > swing_high
    else:
        swing_low = float(df_15m["low"].iloc[-n-1:-1].min())
        bos = float(df_15m["low"].iloc[-1]) < swing_low

    if bos and _can_alert(symbol, STAGE_BOS):
        state = _warn_states.get(symbol)
        if state and state.direction == direction:
            state.stage     = STAGE_BOS
            state.bos_price = price
            state.score     = score
            state.pattern   = pattern

        # Alert Stage 2
        emoji = "🟢" if direction == "BUY" else "🔴"
        pat_str = f"\n🦋 Pattern : {pattern}" if pattern else ""
        msg = (
            f"🎯 BOS CONFIRMED — ENTRY READY\n"
            f"━━━━━━━━━━━━━━━━━\n"
            f"{emoji} {symbol} {direction}\n"
            f"💰 BOS @ {price:,.2f}\n"
            f"📈 Score: {score}/10{pat_str}\n"
            f"⚡ Signal กำลังประมวลผล..."
        )
        send_telegram(msg)
        _mark_alert(symbol, STAGE_BOS)
        print(f"🎯 Stage 2 Alert: {symbol} {direction} BOS @ {price:.2f}")

    return bos


# ── Stage 3: Signal Ready ─────────────────────────────────
def alert_signal_ready(
    symbol:     str,
    direction:  str,
    signal_type:str,
    score:      int,
    entry:      float,
    sl:         float,
    tp:         float,
    pattern:    str = "",
    session:    str = "",
) -> None:
    """
    Score threshold hit → Alert ก่อนยิง EA
    """
    if not _can_alert(symbol, STAGE_READY):
        return

    state = _warn_states.get(symbol)
    if state:
        state.stage = STAGE_READY

    emoji   = "🟢" if direction == "BUY" else "🔴"
    sniper  = signal_type == "V5_SNIPER"
    pat_str = f"\n🦋 Pattern : {pattern}" if pattern else ""

    msg = (
        f"{'🎯' if sniper else emoji} {'SNIPER' if sniper else 'SESSION'} SIGNAL FIRING\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"📌 {symbol} {direction}\n"
        f"🎯 Entry  : {entry:,.2f}\n"
        f"🛡️ SL     : {sl:.2f}\n"
        f"🏆 TP     : {tp:,.2f}\n"
        f"📈 Score  : {score}/10{pat_str}\n"
        f"🕐 Session: {session}\n"
        f"━━━━━━━━━━━━━━━━━\n"
        f"🤖 EA executing..."
    )
    send_telegram(msg)
    _mark_alert(symbol, STAGE_READY)
    print(f"🚀 Stage 3 Signal: {symbol} {direction} @ {entry:.2f}")


# ── Get Warning Status ─────────────────────────────────────
def get_warning_status(symbol: str = "XAUUSD") -> dict:
    state = _warn_states.get(symbol)
    if not state:
        return {"symbol": symbol, "stage": 0, "status": "No active setup"}
    stage_names = {0:"Idle", 1:"VSA Forming", 2:"BOS Confirmed", 3:"Signal Fired"}
    return {
        "symbol":      state.symbol,
        "direction":   state.direction,
        "stage":       state.stage,
        "status":      stage_names.get(state.stage, "Unknown"),
        "setup_price": state.setup_price,
        "score":       state.score,
        "pattern":     state.pattern,
        "session":     state.session,
    }
