"""
vsa_gate.py — Alpha Buffalo v5.2
VSA Buy vs Sell Pressure Gate
ใช้เปรียบเทียบแรงซื้อ vs แรงขาย ก่อนอนุญาต Re-entry
"""
import pandas as pd
import numpy as np

VOL_WINDOW = 20
VOL_MULT   = 1.5


def _vsa_pressure(df: pd.DataFrame, window: int = VOL_WINDOW) -> dict:
    if "volume" not in df.columns or len(df) < window + 2:
        return {"buy": 0.0, "sell": 0.0, "bias": "NEUTRAL"}
    avg_vol = float(df["volume"].iloc[-window-1:-1].mean())
    if avg_vol == 0:
        return {"buy": 0.0, "sell": 0.0, "bias": "NEUTRAL"}
    recent = df.tail(3)
    buy_vol  = float(recent[recent["close"] > recent["open"]]["volume"].sum())
    sell_vol = float(recent[recent["close"] < recent["open"]]["volume"].sum())
    buy_norm  = buy_vol  / avg_vol
    sell_norm = sell_vol / avg_vol
    if buy_norm > sell_norm * 1.3:
        bias = "BUY"
    elif sell_norm > buy_norm * 1.3:
        bias = "SELL"
    else:
        bias = "NEUTRAL"
    return {"buy": round(buy_norm, 2), "sell": round(sell_norm, 2), "bias": bias}


def compare_vsa_pressure(df_1h: pd.DataFrame, df_15m: pd.DataFrame) -> dict:
    """
    เปรียบเทียบ VSA H1 vs M15
    คืน {"bias": str, "h1_buy": float, "h1_sell": float,
          "m15_buy": float, "m15_sell": float, "reentry_ok": bool}
    """
    h1  = _vsa_pressure(df_1h,  VOL_WINDOW)
    m15 = _vsa_pressure(df_15m, VOL_WINDOW)

    # ทั้ง 2 TF ต้องไม่ขัดแย้งกัน
    biases = [h1["bias"], m15["bias"]]
    if biases.count("BUY")  >= 1 and "SELL" not in biases:
        bias = "BUY"
    elif biases.count("SELL") >= 1 and "BUY" not in biases:
        bias = "SELL"
    else:
        bias = "NEUTRAL"

    return {
        "bias":       bias,
        "h1_buy":     h1["buy"],  "h1_sell":  h1["sell"],
        "m15_buy":    m15["buy"], "m15_sell": m15["sell"],
        "reentry_ok": bias != "NEUTRAL",
    }


def check_reentry_allowed(df_1h, df_15m, direction: str) -> dict:
    """
    Gate สุดท้ายก่อน Re-entry:
    VSA bias ต้องตรงกับ direction ที่จะเข้า
    """
    vsa = compare_vsa_pressure(df_1h, df_15m)
    allowed = (vsa["bias"] == direction) or (vsa["bias"] == "NEUTRAL" and vsa["reentry_ok"])
    return {
        "allowed": allowed,
        "bias":    vsa["bias"],
        "reason":  f"VSA {vsa['bias']} H1={vsa['h1_buy']:.1f}B/{vsa['h1_sell']:.1f}S "
                   f"M15={vsa['m15_buy']:.1f}B/{vsa['m15_sell']:.1f}S",
    }

# ============================================================
# ฟังก์ชันใหม่สำหรับ VSA แบบ classic + ASIA multiplier + spike
# ============================================================

def check_vsa_signal(
    df,
    direction,
    asia_mode=False,
    spike_detected=False,
    lookback=20
):
    """
    VSA gate หลัก - รองรับ ASIA multiplier และ spike detection
    Returns dict with keys: ok, signal_type, bonus, position_multiplier, reason
    """
    result = {
        "ok": True,  # fail-open default
        "signal_type": None,
        "bonus": 0,
        "position_multiplier": 0.5 if asia_mode else 1.0,
        "reason": "VSA gate passed (default)",
        "details": {},
    }
    try:
        if len(df) < lookback + 2:
            result["reason"] = "Insufficient data"
            return result

        avg_volume = df["volume"].rolling(lookback).mean()
        last = df.iloc[-1]
        avg_vol = avg_volume.iloc[-1]

        body = abs(last["close"] - last["open"])
        candle_range = last["high"] - last["low"]
        is_up = last["close"] > last["open"]
        is_down = last["close"] < last["open"]

        is_high_volume = last["volume"] > avg_vol * 1.5 if avg_vol > 0 else False
        is_low_volume = last["volume"] < avg_vol * 0.7 if avg_vol > 0 else False
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
                result["reason"] = "Effort to rise"
            elif is_down and is_low_volume:
                result["signal_type"] = "no_supply"
                result["bonus"] = 1
                result["reason"] = "No supply"
            else:
                result["ok"] = False
                result["reason"] = "No bullish VSA signal"
        else:  # SELL
            if is_high_volume and is_down and is_wide_spread:
                result["signal_type"] = "effort_down"
                result["bonus"] = 2
                result["reason"] = "Effort to fall"
            elif is_up and is_low_volume:
                result["signal_type"] = "no_demand"
                result["bonus"] = 1
                result["reason"] = "No demand"
            else:
                result["ok"] = False
                result["reason"] = "No bearish VSA signal"

        if asia_mode and result["ok"]:
            result["position_multiplier"] = 0.5
            result["reason"] += " [Asia: 0.5x]"

    except Exception as e:
        result["reason"] = f"VSA gate error: {e}"
        result["ok"] = True  # fail-open
    return result


def check_vsa_mtf(df_h1, df_h4, direction, asia_mode=False):
    """Multi-timeframe VSA confirmation"""
    h1 = check_vsa_signal(df_h1, direction, asia_mode)
    h4 = check_vsa_signal(df_h4, direction, False)
    return {
        "ok": h1["ok"] and h4["ok"],
        "h1": h1,
        "h4": h4,
        "mtf_bonus": 2 if (h1["ok"] and h4["ok"]) else 0,
        "reason": f"H1: {h1['signal_type']}, H4: {h4['signal_type']}"
    }
