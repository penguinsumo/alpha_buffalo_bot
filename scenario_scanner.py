"""
scenario_scanner.py — Alpha Buffalo v5.2
Predictive VSA Zone Scanner + Telegram Alert (Mode B)

Logic:
  1. สแกนหา Swing Low/High ที่มี VSA confirm
  2. คำนวณ Buy/Sell Limit zone ล่วงหน้า
  3. ส่ง Telegram alert พร้อม zone + invalidation
  4. ถ้าราคามาถึง zone → alert ซ้ำ "Price at zone — ready"
  5. ถ้าราคา invalidate → alert "Zone cancelled"

Mode B: แจ้งเตือนเท่านั้น ไม่ส่ง order อัตโนมัติ
Mode A: (future) ส่ง /signal/pending → EA วาง limit

Endpoint ใหม่:
  GET /signal/scenarios?key=DEMO123
"""

import pandas as pd
import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime, timezone, timedelta
import os
import requests

BKK = timezone(timedelta(hours=7))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
NOTIFY_IDS     = os.getenv("NOTIFY_IDS", "")
SYMBOL         = os.getenv("TRADE_SYMBOL", "XAUUSD")

# ── Constants ──────────────────────────────────────────────
PIVOT_N          = 5      # แท่งซ้าย/ขวาสำหรับ confirm swing
VSA_WINDOW       = 50     # window หา volume percentile
VSA_PERCENTILE   = 85     # top 15% = VSA confirmed
ZONE_BUFFER      = 1.5    # ± USD รอบ swing point
INVALIDATION_PAD = 2.0    # USD เกิน swing → zone invalid
MAX_ZONES        = 3      # แสดงสูงสุด 3 zones
ZONE_EXPIRY_BARS = 24     # zone หมดอายุหลัง 24 แท่ง M15 (~6 ชั่วโมง)
AT_ZONE_BUFFER   = 3.0    # GPS: zone ± pip ใกล้ session H/L


# ── Data Classes ───────────────────────────────────────────
@dataclass
class VSAZone:
    direction:     str      # "BUY" | "SELL"
    zone_high:     float    # บนสุดของ entry zone
    zone_low:      float    # ล่างสุดของ entry zone
    swing_price:   float    # Swing Low/High จริง
    invalidate:    float    # ราคาที่ zone หมดอาย (SL reference)
    vsa_volume:    float    # volume ณ จุด swing
    vsa_confirmed: bool
    absorption:    bool
    formed_bar:    int      # bar index ที่เกิด
    alerted:       bool = False       # ส่ง alert แรกแล้วไหม
    at_zone_alerted: bool = False     # ส่ง "price at zone" แล้วไหม
    invalidated:   bool = False
    label:         str = ""

    @property
    def midpoint(self) -> float:
        return (self.zone_high + self.zone_low) / 2

    def is_price_in_zone(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high

    def is_invalidated(self, price: float) -> bool:
        if self.direction == "BUY":
            return price < self.invalidate
        return price > self.invalidate

    def is_expired(self, current_bar: int) -> bool:
        return (current_bar - self.formed_bar) > ZONE_EXPIRY_BARS


# ── VSA Detection ──────────────────────────────────────────
def _has_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].sum() > 0

def _is_vsa_volume(df: pd.DataFrame, idx: int) -> bool:
    if not _has_volume(df):
        return False
    vol     = float(df["volume"].iloc[idx])
    start   = max(0, idx - VSA_WINDOW)
    rolling = df["volume"].iloc[start:idx]
    if len(rolling) < 5:
        return False
    return vol >= rolling.quantile(VSA_PERCENTILE / 100)

def _is_absorption(df: pd.DataFrame, idx: int) -> bool:
    """Wide range candle + wick > 55% = Smart Money absorbing"""
    c   = df.iloc[idx]
    rng = c["high"] - c["low"]
    if rng == 0:
        return False
    body = abs(c["close"] - c["open"])
    wick = rng - body
    return (wick / rng) > 0.55


# ── Swing Detection ────────────────────────────────────────
def _find_swing_lows(df: pd.DataFrame, n: int = PIVOT_N) -> list[dict]:
    """หา Swing Low ที่ confirm แล้ว (ไม่รวม live candle)"""
    results = []
    safe    = df.iloc[:-1]   # ตัด live candle
    lows    = safe["low"].values

    for i in range(n, len(safe) - n):
        low = lows[i]
        if all(lows[i] < lows[i-j] for j in range(1, n+1)) and \
           all(lows[i] < lows[i+j] for j in range(1, n+1)):
            vsa = _is_vsa_volume(safe, i)
            abs_ = _is_absorption(safe, i)
            results.append({
                "idx":        i,
                "price":      float(low),
                "vsa":        vsa,
                "absorption": abs_,
                "volume":     float(safe["volume"].iloc[i]) if _has_volume(safe) else 0,
            })
    return results


def _find_swing_highs(df: pd.DataFrame, n: int = PIVOT_N) -> list[dict]:
    """หา Swing High ที่ confirm แล้ว"""
    results = []
    safe    = df.iloc[:-1]
    highs   = safe["high"].values

    for i in range(n, len(safe) - n):
        high = highs[i]
        if all(highs[i] > highs[i-j] for j in range(1, n+1)) and \
           all(highs[i] > highs[i+j] for j in range(1, n+1)):
            vsa = _is_vsa_volume(safe, i)
            abs_ = _is_absorption(safe, i)
            results.append({
                "idx":        i,
                "price":      float(high),
                "vsa":        vsa,
                "absorption": abs_,
                "volume":     float(safe["volume"].iloc[i]) if _has_volume(safe) else 0,
            })
    return results


# ── Zone Builder ───────────────────────────────────────────
def build_buy_zones(df: pd.DataFrame) -> list[VSAZone]:
    """BUY zones จาก Swing Low ที่มี VSA"""
    swings = _find_swing_lows(df, PIVOT_N)
    zones  = []

    for s in swings[-MAX_ZONES:]:   # เอาแค่ล่าสุด
        zone = VSAZone(
            direction     = "BUY",
            zone_high     = round(s["price"] + ZONE_BUFFER, 2),
            zone_low      = round(s["price"] - ZONE_BUFFER * 0.5, 2),
            swing_price   = s["price"],
            invalidate    = round(s["price"] - INVALIDATION_PAD, 2),
            vsa_volume    = s["volume"],
            vsa_confirmed = s["vsa"],
            absorption    = s["absorption"],
            formed_bar    = s["idx"],
            label = (
                f"BUY zone @ {s['price']:.2f} "
                f"| VSA:{s['vsa']} Abs:{s['absorption']}"
            ),
        )
        zones.append(zone)

    return zones


def build_sell_zones(df: pd.DataFrame) -> list[VSAZone]:
    """SELL zones จาก Swing High ที่มี VSA"""
    swings = _find_swing_highs(df, PIVOT_N)
    zones  = []

    for s in swings[-MAX_ZONES:]:
        zone = VSAZone(
            direction     = "SELL",
            zone_high     = round(s["price"] + ZONE_BUFFER * 0.5, 2),
            zone_low      = round(s["price"] - ZONE_BUFFER, 2),
            swing_price   = s["price"],
            invalidate    = round(s["price"] + INVALIDATION_PAD, 2),
            vsa_volume    = s["volume"],
            vsa_confirmed = s["vsa"],
            absorption    = s["absorption"],
            formed_bar    = s["idx"],
            label = (
                f"SELL zone @ {s['price']:.2f} "
                f"| VSA:{s['vsa']} Abs:{s['absorption']}"
            ),
        )
        zones.append(zone)

    return zones


# ── Telegram Alert ─────────────────────────────────────────
def _send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not NOTIFY_IDS:
        return
    ids = [x.strip() for x in NOTIFY_IDS.split(",") if x.strip()]
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    for chat_id in ids:
        try:
            requests.post(url, json={
                "chat_id":    chat_id,
                "text":       msg,
                "parse_mode": "HTML",
            }, timeout=5)
        except Exception:
            pass


def _alert_zone_found(zone: VSAZone):
    quality = []
    if zone.vsa_confirmed: quality.append("VSA ✅")
    if zone.absorption:    quality.append("Absorption ✅")
    quality_str = " | ".join(quality) if quality else "Price only"

    direction_emoji = "🟢" if zone.direction == "BUY" else "🔴"
    action = "BUY LIMIT" if zone.direction == "BUY" else "SELL LIMIT"

    msg = (
        f"{direction_emoji} <b>SCENARIO ALERT — {action}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 Asset     : {SYMBOL}\n"
        f"📍 Zone      : {zone.zone_low:.2f} – {zone.zone_high:.2f}\n"
        f"🎯 Swing     : {zone.swing_price:.2f}\n"
        f"🛡 Invalidate: {zone.invalidate:.2f}\n"
        f"⚡ Quality   : {quality_str}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏳ รอราคากลับมาที่ zone\n"
        f"❌ Cancel ถ้าทะลุ {zone.invalidate:.2f}\n"
        f"⚠️ Not financial advice"
    )
    _send_telegram(msg)


def _alert_price_at_zone(zone: VSAZone, price: float):
    direction_emoji = "🟢" if zone.direction == "BUY" else "🔴"
    action = "BUY" if zone.direction == "BUY" else "SELL"

    msg = (
        f"{direction_emoji} <b>⚡ PRICE AT ZONE — {action} READY</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {SYMBOL} @ {price:.2f}\n"
        f"📍 Zone : {zone.zone_low:.2f} – {zone.zone_high:.2f}\n"
        f"🛡 SL ref: {zone.invalidate:.2f}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"✅ ราคาเข้า zone แล้ว\n"
        f"👉 พิจารณาเปิด {action} ด้วยตัวเอง\n"
        f"⚠️ Not financial advice"
    )
    _send_telegram(msg)


def _alert_invalidated(zone: VSAZone, price: float):
    direction_emoji = "🟢" if zone.direction == "BUY" else "🔴"

    msg = (
        f"{direction_emoji} <b>❌ ZONE INVALIDATED</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {SYMBOL} @ {price:.2f}\n"
        f"📍 Zone {zone.direction} {zone.zone_low:.2f}–{zone.zone_high:.2f}\n"
        f"💥 ทะลุ invalidation {zone.invalidate:.2f}\n"
        f"🚫 Zone นี้ยกเลิกแล้ว"
    )
    _send_telegram(msg)


# ── Main Scanner ───────────────────────────────────────────
class ScenarioScanner:
    """
    เรียกทุก poll cycle (30 นาที)
    track active zones ข้าม poll ด้วย instance variable
    """

    def __init__(self):
        self.active_zones: list[VSAZone] = []
        self._bar_counter: int = 0

    def scan(self, df_15m: pd.DataFrame) -> list[VSAZone]:
        """
        รับ df_15m → อัพเดท zones → ส่ง alerts
        คืน active zones ทั้งหมด
        """
        self._bar_counter = len(df_15m)
        price = float(df_15m["close"].iloc[-2])   # confirmed candle

        # ── 1. สร้าง zones ใหม่ ───────────────────────────
        new_buy  = build_buy_zones(df_15m)
        new_sell = build_sell_zones(df_15m)

        # เพิ่ม zone ใหม่ที่ยังไม่มีใน active_zones
        existing_swings = {z.swing_price for z in self.active_zones}
        for z in new_buy + new_sell:
            if z.swing_price not in existing_swings:
                self.active_zones.append(z)
                existing_swings.add(z.swing_price)

        # ── 2. อัพเดทและ alert แต่ละ zone ───────────────
        to_remove = []
        for zone in self.active_zones:

            # Expire
            if zone.is_expired(self._bar_counter):
                to_remove.append(zone)
                continue

            # Invalidate
            if zone.is_invalidated(price):
                if not zone.invalidated:
                    zone.invalidated = True
                    pass  # Mode B disabled — admin only
                to_remove.append(zone)
                continue

            # Alert zone found (ครั้งแรก)
            if not zone.alerted:
                zone.alerted = True
                pass  # Mode B disabled — admin only

            # Alert price at zone
            if zone.is_price_in_zone(price) and not zone.at_zone_alerted:
                zone.at_zone_alerted = True
                pass  # Mode B disabled — admin only

        # ลบ zones ที่หมดอายุ/invalid
        self.active_zones = [z for z in self.active_zones if z not in to_remove]

        return self.active_zones

    def get_summary(self) -> list[dict]:
        """สำหรับ /signal/scenarios endpoint"""
        return [
            {
                "direction":     z.direction,
                "zone_high":     z.zone_high,
                "zone_low":      z.zone_low,
                "swing_price":   z.swing_price,
                "invalidate":    z.invalidate,
                "vsa_confirmed": z.vsa_confirmed,
                "absorption":    z.absorption,
                "at_zone":       z.at_zone_alerted,
                "invalidated":   z.invalidated,
                "label":         z.label,
            }
            for z in self.active_zones
        ]

    def clear_expired(self):
        self.active_zones = [
            z for z in self.active_zones
            if not z.is_expired(self._bar_counter) and not z.invalidated
        ]


# ── Singleton ──────────────────────────────────────────────
scenario_scanner = ScenarioScanner()


def run_scenario_scan(df_15m: pd.DataFrame) -> list[dict]:
    """Entry point — เรียกจาก alpha_buffalo_signal.py ทุก poll"""
    zones = scenario_scanner.scan(df_15m)
    return scenario_scanner.get_summary()
