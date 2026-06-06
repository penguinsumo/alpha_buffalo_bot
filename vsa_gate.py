"""
vsa_gate.py — Alpha Buffalo v5.2
VSA Gate รองรับ ASIA multiplier และ Spike detection
"""

import pandas as pd
import numpy as np
from typing import Dict, Literal


def check_vsa_signal(
    df: pd.DataFrame,
    direction: Literal["BUY", "SELL"],
    asia_mode: bool = False,
    spike_detected: bool = False,
    lookback: int = 20,
) -> Dict:
    """
    VSA gate หลัก - รองรับ ASIA multiplier และ spike
    """
    result = {
        "ok": True,
        "signal_type": None,
        "bonus": 0,
        "position_multiplier": 0.5 if asia_mode else 1.0,
        "reason": "VSA gate passed",
        "details": {},
    }
    
    try:
        if len(df) < lookback + 2:
            result["reason"] = "Insufficient data"
            return result
        
        avg_volume = df["volume"].rolling(lookback).mean()
        
        last_close = df["close"].iloc[-1]
        last_open = df["open"].iloc[-1]
        last_high = df["high"].iloc[-1]
        last_low = df["low"].iloc[-1]
        last_volume = df["volume"].iloc[-1]
        avg_vol = avg_volume.iloc[-1]
        
        body = abs(last_close - last_open)
        candle_range = last_high - last_low
        is_up = last_close > last_open
        is_down = last_close < last_open
        
        is_high_volume = last_volume > avg_vol * 1.5 if avg_vol > 0 else False
        is_low_volume = last_volume < avg_vol * 0.7 if avg_vol > 0 else False
        is_wide_spread = body > candle_range * 0.7 if candle_range > 0 else False
        
        # Spike mode
        if spike_detected:
            if direction == "BUY" and is_down and is_low_volume:
                result["signal_type"] = "no_supply_after_spike"
                result["bonus"] = 2
                result["reason"] = "No supply after spike → strong bullish"
                result["position_multiplier"] *= 1.2
            elif direction == "SELL" and is_up and is_low_volume:
                result["signal_type"] = "no_demand_after_spike"
                result["bonus"] = 2
                result["reason"] = "No demand after spike → strong bearish"
                result["position_multiplier"] *= 1.2
            else:
                result["ok"] = False
                result["reason"] = f"Spike detected but no confirmation"
            return result
        
        # Normal VSA logic
        if direction == "BUY":
            if is_high_volume and is_up and is_wide_spread:
                result["signal_type"] = "effort_up"
                result["bonus"] = 2
                result["reason"] = "Effort to rise: high volume + wide spread"
            elif is_down and is_low_volume:
                result["signal_type"] = "no_supply"
                result["bonus"] = 1
                result["reason"] = "No supply: low volume down candle"
            else:
                result["ok"] = False
                result["reason"] = f"No bullish VSA signal"
        else:
            if is_high_volume and is_down and is_wide_spread:
                result["signal_type"] = "effort_down"
                result["bonus"] = 2
                result["reason"] = "Effort to fall: high volume + wide spread"
            elif is_up and is_low_volume:
                result["signal_type"] = "no_demand"
                result["bonus"] = 1
                result["reason"] = "No demand: low volume up candle"
            else:
                result["ok"] = False
                result["reason"] = f"No bearish VSA signal"
        
        if asia_mode and result["ok"]:
            result["position_multiplier"] = 0.5
            result["reason"] += " [Asia session: 0.5x]"
        
        result["details"] = {
            "volume_ratio": last_volume / avg_vol if avg_vol > 0 else 1,
            "is_high_volume": is_high_volume,
            "is_up": is_up,
        }
        
    except Exception as e:
        result["reason"] = f"VSA gate error (fail-open): {e}"
        result["ok"] = True
    
    return result


def check_vsa_mtf(
    df_h1: pd.DataFrame,
    df_h4: pd.DataFrame,
    direction: Literal["BUY", "SELL"],
    asia_mode: bool = False,
) -> Dict:
    """Multi-timeframe VSA confirmation"""
    h1_result = check_vsa_signal(df_h1, direction, asia_mode)
    h4_result = check_vsa_signal(df_h4, direction, False)
    
    return {
        "ok": h1_result["ok"] and h4_result["ok"],
        "h1": h1_result,
        "h4": h4_result,
        "mtf_bonus": 2 if (h1_result["ok"] and h4_result["ok"]) else 0,
        "reason": f"H1: {h1_result['signal_type']}, H4: {h4_result['signal_type']}",
    }


# ─────────────────────────────────────────────
# ฟังก์ชันเดิมสำหรับ backward compatibility
# ─────────────────────────────────────────────

VOL_WINDOW = 20


def _vsa_pressure(df: pd.DataFrame, window: int = VOL_WINDOW) -> dict:
    if "volume" not in df.columns or len(df) < window + 2:
        return {"buy": 0.0, "sell": 0.0, "bias": "NEUTRAL"}
    avg_vol = float(df["volume"].iloc[-window-1:-1].mean())
    if avg_vol == 0:
        return {"buy": 0.0, "sell": 0.0, "bias": "NEUTRAL"}
    recent = df.tail(3)
    buy_vol = float(recent[recent["close"] > recent["open"]]["volume"].sum())
    sell_vol = float(recent[recent["close"] < recent["open"]]["volume"].sum())
    buy_norm = buy_vol / avg_vol
    sell_norm = sell_vol / avg_vol
    if buy_norm > sell_norm * 1.3:
        bias = "BUY"
    elif sell_norm > buy_norm * 1.3:
        bias = "SELL"
    else:
        bias = "NEUTRAL"
    return {"buy": round(buy_norm, 2), "sell": round(sell_norm, 2), "bias": bias}


def compare_vsa_pressure(df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> dict:
    h1 = _vsa_pressure(df_1h, VOL_WINDOW)
    m15 = _vsa_pressure(df_15m, VOL_WINDOW)
    biases = [h1["bias"], m15["bias"]]
    if biases.count("BUY") >= 1 and "SELL" not in biases:
        bias = "BUY"
    elif biases.count("SELL") >= 1 and "BUY" not in biases:
        bias = "SELL"
    else:
        bias = "NEUTRAL"
    return {
        "bias": bias,
        "h1_buy": h1["buy"], "h1_sell": h1["sell"],
        "m15_buy": m15["buy"], "m15_sell": m15["sell"],
        "reentry_ok": bias != "NEUTRAL",
    }


def check_reentry_allowed(df_1h, df_15m, direction: str) -> dict:
    vsa = compare_vsa_pressure(df_1h, df_15m)
    allowed = (vsa["bias"] == direction) or (vsa["bias"] == "NEUTRAL" and vsa["reentry_ok"])
    return {
        "allowed": allowed,
        "bias": vsa["bias"],
        "reason": f"VSA {vsa['bias']} H1={vsa['h1_buy']:.1f}B/{vsa['h1_sell']:.1f}S "
                  f"M15={vsa['m15_buy']:.1f}B/{vsa['m15_sell']:.1f}S",
    }


def check_h4_stoch_be(df_4h, direction: str) -> dict:
    if df_4h is None or len(df_4h) < 20:
        return {"be_trigger": False, "k": 50.0}
    low_min = df_4h["low"].rolling(14).min()
    high_max = df_4h["high"].rolling(14).max()
    denom = (high_max - low_min).replace(0, float("nan"))
    k = ((df_4h["close"] - low_min) / denom * 100).fillna(50)
    k_cur = float(k.iloc[-1])
    trigger = (k_cur > 80) if direction == "BUY" else (k_cur < 20)
    return {"be_trigger": trigger, "k": round(k_cur, 1)}
