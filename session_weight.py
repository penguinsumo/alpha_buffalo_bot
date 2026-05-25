"""
session_weight.py — Alpha Buffalo v5.1
Session-Based Weighting + Overlap Gatekeeper

Sessions (BKK UTC+7):
  Asia   : 02:00-09:00 → 5M entry, max 1 layer, close before London
  London : 14:00-20:00 → 15M entry, max 2 layers, full score
  NY     : 20:00-02:00 → 15M entry, max 2 layers, full score

Gatekeeper Rules:
  1. Hedge Block   — มี BUY open → BLOCK SELL (และกลับกัน)
  2. Layer Check   — BUY ซ้ำ → เช็ค distance + layer count
  3. Asia Close    — ปิด Asia position ก่อน London เปิด
"""

import os
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Optional

BKK = timezone(timedelta(hours=7))

# ── Session Config ────────────────────────────────────────
SESSION_CONFIG = {
    "Asia": {
        "tf_entry":       "5min",
        "tf_bars":        100,
        "max_layers":     1,       # max 1 ไม้ต่อ session
        "v4_min":         4,       # ไม่เพิ่ม threshold
        "v5_min":         8,
        "vsa_mult":       0.7,     # VSA ลดนิดหน่อย (Fakeout)
        "score_adj":      0,       # ไม่ลด score
        "close_before":   14,      # ปิดก่อน London (14:00 BKK)
        "allow_hedge":    False,
        "min_distance":   5.0,     # USD ห่างจากไม้แรก
    },
    "London": {
        "tf_entry":       "15min",
        "tf_bars":        96,
        "max_layers":     2,
        "v4_min":         4,
        "v5_min":         8,
        "vsa_mult":       1.0,
        "score_adj":      0,
        "close_before":   None,
        "allow_hedge":    False,
        "min_distance":   8.0,
    },
    "NY": {
        "tf_entry":       "15min",
        "tf_bars":        96,
        "max_layers":     2,
        "v4_min":         4,
        "v5_min":         8,
        "vsa_mult":       1.0,
        "score_adj":      0,
        "close_before":   None,
        "allow_hedge":    False,
        "min_distance":   8.0,
    },
}


# ── Position State ────────────────────────────────────────
@dataclass
class PositionState:
    """State ของ open positions"""
    buy_layers:     int   = 0
    sell_layers:    int   = 0
    last_buy_entry: float = 0.0
    last_sell_entry:float = 0.0
    asia_opened:    bool  = False   # มี Asia position ไหม
    asia_session:   str   = ""      # session ที่เปิด

_state = PositionState()


# ── Session Detector ──────────────────────────────────────
def get_session_now() -> str:
    h = datetime.now(BKK).hour
    if 2  <= h < 9:  return "Asia"
    if 14 <= h < 20: return "London"
    if h >= 20 or h < 2: return "NY"
    return "Closed"   # 09:00-14:00 = ช่วงปิด


def get_session_config(session: str = "") -> dict:
    s = session or get_session_now()
    return SESSION_CONFIG.get(s, SESSION_CONFIG["London"])


# ── Session Weighting ─────────────────────────────────────
def apply_session_weight(
    score:       int,
    direction:   str,
    session:     str,
    vsa_scored:  bool = False,
) -> dict:
    """
    ปรับ score ตาม session
    คืน {"final_score", "v4_min", "v5_min", "adj_reason"}
    """
    cfg = get_session_config(session)

    adj = cfg["score_adj"]

    # VSA adjustment — ลด VSA score ใน Asia
    vsa_adj = 0
    if vsa_scored and session == "Asia":
        # VSA ใน Asia = อาจ Fakeout → ลด 1 คะแนน
        vsa_adj = -1
        adj += vsa_adj

    final_score = max(0, score + adj)

    reason = ""
    if adj < 0:
        reason = "Asia VSA discount: " + str(vsa_adj)
    elif adj == 0:
        reason = session + " full weight"

    return {
        "final_score": final_score,
        "v4_min":      cfg["v4_min"],
        "v5_min":      cfg["v5_min"],
        "max_layers":  cfg["max_layers"],
        "tf_entry":    cfg["tf_entry"],
        "tf_bars":     cfg["tf_bars"],
        "adj":         adj,
        "reason":      reason,
    }


# ── Overlap & Hedge Gatekeeper ────────────────────────────
def check_overlap(
    direction:    str,
    entry_price:  float,
    session:      str,
    buy_layers:   int = 0,
    sell_layers:  int = 0,
    last_buy:     float = 0.0,
    last_sell:    float = 0.0,
) -> dict:
    """
    ตรวจสอบ Overlap และ Hedge conflict

    คืน:
    {
        "allowed": bool,
        "reason":  str,
        "layer":   int,   # layer ที่จะเปิด
    }
    """
    cfg = get_session_config(session)
    max_layers   = cfg["max_layers"]
    min_distance = cfg["min_distance"]

    # ── Rule 1: Hedge Block ───────────────────────────────
    if direction == "BUY" and sell_layers > 0:
        return {"allowed": False, "reason": "Hedge Block: SELL open → BUY blocked", "layer": 0}
    if direction == "SELL" and buy_layers > 0:
        return {"allowed": False, "reason": "Hedge Block: BUY open → SELL blocked", "layer": 0}

    # ── Rule 2: Max Layers ────────────────────────────────
    current_layers = buy_layers if direction == "BUY" else sell_layers
    if current_layers >= max_layers:
        return {
            "allowed": False,
            "reason":  "Max layers reached: " + str(current_layers) + "/" + str(max_layers),
            "layer":   0,
        }

    # ── Rule 3: Layer Distance ────────────────────────────
    if current_layers > 0:
        last_entry = last_buy if direction == "BUY" else last_sell
        if last_entry > 0:
            distance = abs(entry_price - last_entry)
            if distance < min_distance:
                return {
                    "allowed": False,
                    "reason":  "Too close: " + str(round(distance,2)) + " < " + str(min_distance) + " USD",
                    "layer":   0,
                }

    # ── Rule 4: Asia max 1 layer ──────────────────────────
    if session == "Asia" and current_layers >= 1:
        return {
            "allowed": False,
            "reason":  "Asia session: max 1 layer per session",
            "layer":   0,
        }

    next_layer = current_layers + 1
    return {
        "allowed": True,
        "reason":  "Clear — Layer " + str(next_layer),
        "layer":   next_layer,
    }


# ── Asia Close Warning ────────────────────────────────────
def should_close_asia_positions() -> dict:
    """
    เช็คว่าถึงเวลาปิด Asia positions ไหม
    ปิดก่อน London เปิด (14:00 BKK)
    """
    now    = datetime.now(BKK)
    hour   = now.hour
    minute = now.minute

    # ช่วง 13:45-14:00 = ส่ง warning ปิด Asia
    if hour == 13 and minute >= 45:
        return {
            "should_close": True,
            "reason":       "Asia positions: close before London open (14:00 BKK)",
            "urgent":       minute >= 55,
        }
    # ถ้า London เปิดแล้วและยังมี Asia position
    if hour == 14 and minute < 15:
        return {
            "should_close": True,
            "reason":       "London opened — close Asia positions",
            "urgent":       True,
        }
    return {"should_close": False, "reason": "", "urgent": False}


# ── Full Gate Check ───────────────────────────────────────
def full_gate_check(
    direction:   str,
    score:       int,
    entry_price: float,
    session:     str,
    vsa_scored:  bool  = False,
    buy_layers:  int   = 0,
    sell_layers: int   = 0,
    last_buy:    float = 0.0,
    last_sell:   float = 0.0,
) -> dict:
    """
    Gate check ครบทุกขั้นตอน
    คืน {"fire": bool, "reason": str, "layer": int, "signal_type": str}
    """

    # Step 1: Session Weight
    weight = apply_session_weight(score, direction, session, vsa_scored)
    final_score = weight["final_score"]

    # Step 2: Score threshold
    if final_score < weight["v4_min"]:
        return {
            "fire":        False,
            "reason":      "Score too low: " + str(final_score) + " < " + str(weight["v4_min"]),
            "layer":       0,
            "signal_type": "",
            "final_score": final_score,
        }

    # Step 3: Overlap + Hedge
    overlap = check_overlap(
        direction, entry_price, session,
        buy_layers, sell_layers, last_buy, last_sell,
    )
    if not overlap["allowed"]:
        return {
            "fire":        False,
            "reason":      overlap["reason"],
            "layer":       0,
            "signal_type": "",
            "final_score": final_score,
        }

    # Step 4: Signal type
    sig_type = "V5_SNIPER" if final_score >= weight["v5_min"] else "V4_SESSION"

    return {
        "fire":        True,
        "reason":      "OK — " + weight["reason"] + " | " + overlap["reason"],
        "layer":       overlap["layer"],
        "signal_type": sig_type,
        "final_score": final_score,
        "tf_entry":    weight["tf_entry"],
        "tf_bars":     weight["tf_bars"],
    }


# ── Update State ──────────────────────────────────────────
def update_state_open(direction: str, entry: float, session: str):
    """อัพเดท state หลังเปิด order"""
    global _state
    if direction == "BUY":
        _state.buy_layers     += 1
        _state.last_buy_entry  = entry
    else:
        _state.sell_layers     += 1
        _state.last_sell_entry = entry
    if session == "Asia":
        _state.asia_opened = True
        _state.asia_session = session


def update_state_close(direction: str):
    """อัพเดท state หลังปิด order"""
    global _state
    if direction == "BUY":
        _state.buy_layers = max(0, _state.buy_layers - 1)
        if _state.buy_layers == 0:
            _state.last_buy_entry = 0.0
    else:
        _state.sell_layers = max(0, _state.sell_layers - 1)
        if _state.sell_layers == 0:
            _state.last_sell_entry = 0.0


def reset_asia_state():
    """Reset Asia state เมื่อปิด positions หมด"""
    global _state
    _state.asia_opened  = False
    _state.asia_session = ""


def get_state() -> dict:
    return {
        "buy_layers":      _state.buy_layers,
        "sell_layers":     _state.sell_layers,
        "last_buy_entry":  _state.last_buy_entry,
        "last_sell_entry": _state.last_sell_entry,
        "asia_opened":     _state.asia_opened,
        "session_now":     get_session_now(),
    }


# ── Telegram Status ───────────────────────────────────────
def format_session_status() -> str:
    session = get_session_now()
    cfg     = get_session_config(session)
    state   = get_state()
    close   = should_close_asia_positions()

    msg = "Session Status\n"
    msg += "Session  : " + session + "\n"
    msg += "TF Entry : " + cfg["tf_entry"] + "\n"
    msg += "Max Layer: " + str(cfg["max_layers"]) + "\n"
    msg += "V4 min   : " + str(cfg["v4_min"]) + "\n"
    msg += "BUY open : " + str(state["buy_layers"]) + "\n"
    msg += "SELL open: " + str(state["sell_layers"]) + "\n"
    if close["should_close"]:
        msg += "URGENT: " + close["reason"] + "\n"
    return msg
