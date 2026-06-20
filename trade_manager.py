"""
AlphaTradeManager v4.2 — Alpha Buffalo v5.4
Visual SL for SELL + Turbo Boost Ready
"""
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

@dataclass
class TradeState:
    phase: str = "IDLE"
    direction: str = "BUY"
    price_L: Optional[float] = None
    price_HL: Optional[float] = None
    price_BOS: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bars_since_L: int = 0
    min_bars_for_HL: int = 5
    active_orders: List[Dict] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)
    mid_crossed: bool = False

    def reset(self):
        self.phase = "IDLE"
        self.price_L = None
        self.price_HL = None
        self.price_BOS = None
        self.bars_since_L = 0
        self.active_orders.clear()
        self.mid_crossed = False
        self.log("State Reset", "🔄")

    def log(self, event, emoji="", **kw):
        entry = {"event": event, "emoji": emoji, "timestamp": datetime.now().isoformat(), **kw}
        self.history.append(entry)
        if len(self.history) > 20:
            self.history.pop(0)
        logger.info(f"{emoji} {event}")

class AlphaTradeManager:
    def __init__(self, direction="BUY"):
        self.state = TradeState()
        self.state.direction = direction
        self.config = {
            "breakeven_buffer_pct": 0.0005,
            "breakeven_buffer_points": 0.5,
            "atr_multiplier": 0.5,
            "v4_partial_ratio": 0.5,
            "sl_buffer_pct": 0.002,
            "v5_sl_buffer_pct": 0.005,
        }

    def detect_phase1(self, df_15m, vsa_ok=False, kivanc_ok=False):
        if self.state.phase != "IDLE":
            return False
        c = float(df_15m['close'].iloc[-1])
        ma = df_15m['close'].rolling(20).mean().iloc[-1]
        std = df_15m['close'].rolling(20).std().iloc[-1]
        self.state.bb_lower = float(ma - std * 2)
        self.state.bb_middle = float(ma)
        self.state.bb_upper = float(ma + std * 2)

        if self.state.direction == "BUY":
            if c <= self.state.bb_lower * 1.01 and vsa_ok and kivanc_ok:
                self.state.price_L = float(df_15m['low'].min())
                self.state.phase = "PHASE1"
                self.state.bars_since_L = 0
                self.state.log("PHASE1_L", "🟢", L=self.state.price_L)
                return True
        else:
            if c >= self.state.bb_upper * 0.99 and vsa_ok and kivanc_ok:
                self.state.price_L = float(df_15m['high'].max())
                self.state.phase = "PHASE1"
                self.state.bars_since_L = 0
                self.state.log("PHASE1_H", "🔴", H=self.state.price_L)
                return True
        return False

    def detect_phase2(self, df_15m):
        if self.state.phase != "PHASE1":
            return False
        mb = 5
        self.state.bars_since_L += 1
        if self.state.bars_since_L < mb:
            return False

        if self.state.direction == "BUY":
            r = float(df_15m['low'].iloc[-10:].min())
            if r > self.state.price_L:
                self.state.price_HL = r
                self.state.phase = "PHASE2"
                self.state.log("PHASE2_HL", "🟡", HL=r)
                return True
            if r < self.state.price_L:
                self.state.reset()
        else:
            r = float(df_15m['high'].iloc[-10:].max())
            if r < self.state.price_L:
                self.state.price_HL = r
                self.state.phase = "PHASE2"
                self.state.log("PHASE2_LH", "🔵", LH=r)
                return True
            if r > self.state.price_L:
                self.state.reset()
        return False

    def manage_trades(self, price, df_15m=None):
        actions = []
        if df_15m is not None:
            self.state.bb_middle = float(df_15m['close'].rolling(20).mean().iloc[-1])
        is_buy = self.state.direction == "BUY"

        for o in self.state.active_orders:
            if o["status"] != "OPEN":
                continue

            # 🆕 Visual SL สำหรับ SELL: แตะ Mid BB → SL = Entry
            if not is_buy and not self.state.mid_crossed and price <= self.state.bb_middle:
                self.state.mid_crossed = True
                o["sl"] = o["entry"]
                self.state.log("VISUAL_SL", "🛡️", price=price)
                actions.append({"type": "VISUAL_SL", "price": price})
                continue

            # SL Check
            if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                o["status"] = "CLOSED"
                self.state.log("SL_HIT", "🔴", price=price)
                actions.append({"type": "SL", "price": price})
                continue

            # TP Check
            if o.get("tp") and ((is_buy and price >= o["tp"]) or (not is_buy and price <= o["tp"])):
                o["status"] = "CLOSED"
                self.state.log("TP_HIT", "🟢", price=price)
                actions.append({"type": "TP", "price": price})
                continue

        if all(o["status"] == "CLOSED" for o in self.state.active_orders):
            self.state.log("CYCLE_COMPLETE", "✅")
            self.state.reset()
        return actions

    def _atr(self, df, period=10):
        h, l, c = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def update(self, df_15m, vsa_ok=False, kivanc_ok=False):
        price = float(df_15m['close'].iloc[-1])
        if self.state.phase == "IDLE":
            self.detect_phase1(df_15m, vsa_ok, kivanc_ok)
        if self.state.phase == "PHASE1":
            if self.detect_phase2(df_15m):
                self.state.active_orders = [{"type": "V4", "entry": price, "sl": price * 0.998 if self.state.direction == "BUY" else price * 1.002, "tp": price * 1.01 if self.state.direction == "BUY" else price * 0.99, "status": "OPEN"}]
                self.state.phase = "PHASE3"
                self.state.log("ENTRY", "🔥", price=price)
        if self.state.phase == "PHASE3":
            return self.manage_trades(price, df_15m)
        return []

trade_manager = AlphaTradeManager(direction="BUY")
trade_manager_sell = AlphaTradeManager(direction="SELL")
