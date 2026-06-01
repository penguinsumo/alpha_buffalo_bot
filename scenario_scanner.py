"""
scenario_scanner.py — Alpha Buffalo v5.3 (Sniper Ready)
Predictive VSA Zone Scanner + Payload Generator
"""

import pandas as pd
import numpy as np
import time
from dataclasses import dataclass
from typing import Optional
from datetime import datetime, timezone, timedelta
import os
import requests

BKK = timezone(timedelta(hours=7))
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
NOTIFY_IDS     = os.getenv("NOTIFY_IDS", "")
SYMBOL         = os.getenv("TRADE_SYMBOL", "XAUUSD")

# ── Constants ──────────────────────────────────────────────
PIVOT_N          = 5
VSA_WINDOW       = 50
VSA_PERCENTILE   = 85
ZONE_BUFFER      = 1.5
INVALIDATION_PAD = 2.0
MAX_ZONES        = 3
ZONE_EXPIRY_BARS = 24

# ── Data Classes ───────────────────────────────────────────
@dataclass
class VSAZone:
    direction:     str
    zone_high:     float
    zone_low:      float
    swing_price:   float
    invalidate:    float
    open_price:    float    # ← จุด Trigger เลื่อนบังหน้าทุน (BE)
    vsa_volume:    float
    vsa_confirmed: bool
    absorption:    bool
    formed_bar:    int
    alerted:       bool = False
    at_zone_alerted: bool = False
    invalidated:   bool = False
    label:         str = ""

    def is_price_in_zone(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high
        
    def is_invalidated(self, price: float) -> bool:
        return price < self.invalidate if self.direction == "BUY" else price > self.invalidate
        
    def is_expired(self, current_bar: int) -> bool:
        return (current_bar - self.formed_bar) > ZONE_EXPIRY_BARS

# ── Helper Functions ───────────────────────────────────────
def _has_volume(df: pd.DataFrame) -> bool:
    return "volume" in df.columns and df["volume"].sum() > 0

def _is_vsa_volume(df: pd.DataFrame, idx: int) -> bool:
    if not _has_volume(df): return False
    vol = float(df["volume"].iloc[idx])
    start = max(0, idx - VSA_WINDOW)
    rolling = df["volume"].iloc[start:idx]
    return vol >= rolling.quantile(VSA_PERCENTILE / 100) if len(rolling) >= 5 else False

def _is_absorption(df: pd.DataFrame, idx: int) -> bool:
    c = df.iloc[idx]
    rng = c["high"] - c["low"]
    if rng == 0: return False
    body = abs(c["close"] - c["open"])
    wick = rng - body
    return (wick / rng) > 0.55

def _find_swings(df: pd.DataFrame, n: int, is_low: bool) -> list[dict]:
    results = []
    safe = df.iloc[:-1]
    prices = safe["low"].values if is_low else safe["high"].values
    
    for i in range(n, len(safe) - n):
        target = prices[i]
        cond = all(target < prices[i-j] for j in range(1, n+1)) and all(target < prices[i+j] for j in range(1, n+1)) if is_low \
          else all(target > prices[i-j] for j in range(1, n+1)) and all(target > prices[i+j] for j in range(1, n+1))
        
        if cond:
            results.append({
                "idx": i, 
                "price": float(target),
                "open_price": float(safe["open"].iloc[i]),
                "vsa": _is_vsa_volume(safe, i),
                "absorption": _is_absorption(safe, i),
                "volume": float(safe["volume"].iloc[i]) if _has_volume(safe) else 0,
            })
    return results

def build_buy_zones(df: pd.DataFrame) -> list[VSAZone]:
    return [VSAZone("BUY", round(s["price"]+ZONE_BUFFER,2), round(s["price"]-ZONE_BUFFER*0.5,2), s["price"],
                    round(s["price"]-INVALIDATION_PAD,2), s["open_price"], s["volume"], s["vsa"], s["absorption"], s["idx"]) 
            for s in _find_swings(df, PIVOT_N, True)[-MAX_ZONES:]]

def build_sell_zones(df: pd.DataFrame) -> list[VSAZone]:
    return [VSAZone("SELL", round(s["price"]+ZONE_BUFFER*0.5,2), round(s["price"]-ZONE_BUFFER,2), s["price"],
                    round(s["price"]+INVALIDATION_PAD,2), s["open_price"], s["volume"], s["vsa"], s["absorption"], s["idx"]) 
            for s in _find_swings(df, PIVOT_N, False)[-MAX_ZONES:]]

# ── Main Scanner ───────────────────────────────────────────
class ScenarioScanner:
    def __init__(self):
        self.active_zones: list[VSAZone] = []
        self._bar_counter: int = 0

    def scan(self, df_15m: pd.DataFrame) -> list[VSAZone]:
        self._bar_counter = len(df_15m)
        price = float(df_15m["close"].iloc[-2])
        
        new_zones = build_buy_zones(df_15m) + build_sell_zones(df_15m)
        existing_swings = {z.swing_price for z in self.active_zones}
        
        for z in new_zones:
            if z.swing_price not in existing_swings:
                self.active_zones.append(z)
                existing_swings.add(z.swing_price)
                
        to_remove = []
        for zone in self.active_zones:
            if zone.is_expired(self._bar_counter): 
                to_remove.append(zone)
                continue
            if zone.is_invalidated(price): 
                to_remove.append(zone)
                continue
            if not zone.alerted: 
                zone.alerted = True
                
        self.active_zones = [z for z in self.active_zones if z not in to_remove]
        return self.active_zones

    def get_summary(self) -> list[dict]:
        return [{"direction": z.direction, "zone_high": z.zone_high, "zone_low": z.zone_low} for z in self.active_zones]

    # === 🚀 Payload Generator แบบโครงสร้างแบน (Flatten) ===
    def generate_sniper_payload(self) -> dict:
        if not self.active_zones: 
            return {"status": "NO_TRAP"}
            
        z = self.active_zones[-1] # ดึงโซนล่าสุดที่สแกนเจอ
        
        trap_id = f"spike_{int(time.time())}"
        buy_lim = z.zone_high if z.direction == "BUY" else 0.0
        sell_lim = z.zone_low if z.direction == "SELL" else 0.0
        
        # Fibo Projection สำหรับเป้าหมาย TP 3 ระยะ
        rng = 2.0 if z.direction == "BUY" else -2.0 
        tp_1 = z.swing_price + (rng * 0.5)
        tp_2 = z.swing_price + (rng * 1.118)
        tp_3 = z.swing_price + (rng * 1.618)

        return {
            "status": "ARMED",
            "trap_id": trap_id,
            "direction": z.direction,
            "expires_at": int(time.time()) + (30 * 60),
            "buy_limit": round(buy_lim, 2),
            "sell_limit": round(sell_lim, 2),
            "hard_sl": round(z.invalidate, 2),
            "tp_fibo_1_00": round(tp_1, 2),
            "tp_fibo_1_118": round(tp_2, 2),
            "tp_fibo_1_618": round(tp_3, 2),
            "spike_bar_open": round(z.open_price, 2)
        }

    def clear_expired(self):
        self.active_zones = [
            z for z in self.active_zones
            if not z.is_expired(self._bar_counter) and not z.invalidated
        ]

# ── Singleton ──────────────────────────────────────────────
scenario_scanner = ScenarioScanner()

def run_scenario_scan(df_15m: pd.DataFrame) -> list[dict]:
    scenario_scanner.scan(df_15m)
    return scenario_scanner.get_summary()
