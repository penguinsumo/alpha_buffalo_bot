"""
AlphaTradeManager v4.0 — Alpha Buffalo v5.3
Dual Engine: Buy + Sell Independent
Clean Architecture + Production Features
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
import pandas as pd
import logging

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════
# TRADE STATE
# ═══════════════════════════════════════

@dataclass
class TradeState:
    phase: str = "IDLE"
    direction: str = "BUY"
    price_L: Optional[float] = None
    price_HL: Optional[float] = None
    price_BOS: Optional[float] = None
    price_HH: Optional[float] = None
    bb_upper: Optional[float] = None
    bb_middle: Optional[float] = None
    bb_lower: Optional[float] = None
    bars_since_L: int = 0
    min_bars_for_HL: int = 5
    active_orders: List[Dict] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)

    def reset(self):
        self.phase = "IDLE"
        self.price_L = None
        self.price_HL = None
        self.price_BOS = None
        self.price_HH = None
        self.bars_since_L = 0
        self.active_orders.clear()
        self.log("State Reset", "🔄")

    def log(self, event, emoji="", **kw):
        entry = {"event": event, "emoji": emoji, "timestamp": datetime.now().isoformat(), **kw}
        self.history.append(entry)
        if len(self.history) > 20:
            self.history.pop(0)
        logger.info(f"{emoji} {event}")

    def summary(self):
        return {
            "phase": self.phase,
            "direction": self.direction,
            "price_L": self.price_L,
            "price_HL": self.price_HL,
            "price_BOS": self.price_BOS,
            "active_orders": self.active_orders,
            "history": self.history[-5:]
        }

# ═══════════════════════════════════════
# ALPHA TRADE MANAGER
# ═══════════════════════════════════════

class AlphaTradeManager:
    def __init__(self, direction="BUY"):
        self.state = TradeState()
        self.state.direction = direction

        if direction == "BUY":
            self.config = {
                "breakeven_buffer_pct": 0.0005,
                "breakeven_buffer_points": 0.5,
                "bos_tp_discount": 0.98,
                "atr_multiplier": 0.5,
                "v4_partial_ratio": 0.5,
                "sl_buffer_pct": 0.002,
                "v5_sl_buffer_pct": 0.005,
                "min_bars_for_HL": 5,
            }
        else:  # SELL
            self.config = {
                "breakeven_buffer_pct": 0.0005,
                "breakeven_buffer_points": 0.5,
                "bos_tp_discount": 1.02,
                "atr_multiplier": 0.5,
                "v4_partial_ratio": 0.5,
                "sl_buffer_pct": 0.002,
                "v5_sl_buffer_pct": 0.005,
                "min_bars_for_LH": 5,
            }

    # ── Phase 1: Detect L (Buy) or H (Sell) ──
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
            near = c <= self.state.bb_lower * 1.01
            if near and vsa_ok and kivanc_ok:
                self.state.price_L = float(df_15m['low'].min())
                self.state.phase = "PHASE1"
                self.state.bars_since_L = 0
                self.state.log("PHASE1_L", "🟢", L=self.state.price_L)
                return True
        else:
            near = c >= self.state.bb_upper * 0.99
            if near and vsa_ok and kivanc_ok:
                self.state.price_L = float(df_15m['high'].max())
                self.state.phase = "PHASE1"
                self.state.bars_since_L = 0
                self.state.log("PHASE1_H", "🔴", H=self.state.price_L)
                return True
        return False

    # ── Phase 2: Higher Low (Buy) / Lower High (Sell) ──
    def detect_phase2(self, df_15m):
        if self.state.phase != "PHASE1":
            return False
        mb = self.config.get("min_bars_for_HL", self.config.get("min_bars_for_LH", 5))
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

    # ── Dual Entry ──
    def execute_dual_entry(self, price):
        if self.state.phase != "PHASE2":
            return []
        if self.state.price_BOS is None:
            self.state.price_BOS = max(self.state.bb_upper, price * 1.01) if self.state.direction == "BUY" else min(self.state.bb_lower, price * 0.99)

        is_buy = self.state.direction == "BUY"
        v4_sl = self.state.price_HL * (1 - self.config["sl_buffer_pct"]) if is_buy else self.state.price_HL * (1 + self.config["sl_buffer_pct"])
        v4_tp = self.state.price_BOS * self.config["bos_tp_discount"]
        if is_buy and v4_tp <= price:
            v4_tp = price * 1.005
        elif not is_buy and v4_tp >= price:
            v4_tp = price * 0.995

        v5_sl = self.state.price_HL * (1 - self.config["v5_sl_buffer_pct"]) if is_buy else self.state.price_HL * (1 + self.config["v5_sl_buffer_pct"])

        self.state.active_orders = [
            {"type": "V4_SCALP", "entry": price, "sl": v4_sl, "tp": v4_tp, "status": "OPEN", "partial_closed": False},
            {"type": "V5_RUNNER", "entry": price, "sl": v5_sl, "tp": None, "status": "OPEN", "is_breakeven": False, "trailing_active": False}
        ]
        self.state.phase = "PHASE3"
        self.state.log("DUAL_ENTRY", "🔥", price=price)
        return self.state.active_orders

    # ── Manage Trades ──
    def manage_trades(self, price, df_15m=None):
        actions = []
        if df_15m is not None:
            self.state.bb_middle = float(df_15m['close'].rolling(20).mean().iloc[-1])
        is_buy = self.state.direction == "BUY"

        for o in self.state.active_orders:
            if o["status"] != "OPEN":
                continue
            if o["type"] == "V4_SCALP":
                # SL
                if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                    o["status"] = "CLOSED"
                    self.state.log("V4_SL", "🔴", price=price)
                    actions.append({"type": "V4", "action": "SL", "price": price})
                    continue
                # Partial
                half = o["entry"] + (o["tp"] - o["entry"]) * 0.5 if is_buy else o["entry"] - (o["entry"] - o["tp"]) * 0.5
                if (is_buy and price >= half) or (not is_buy and price <= half):
                    if not o["partial_closed"]:
                        o["partial_closed"] = True
                        self.state.log("V4_PARTIAL", "🟡", price=price)
                        actions.append({"type": "V4", "action": "PARTIAL", "price": price})
                # TP
                if (is_buy and price >= o["tp"]) or (not is_buy and price <= o["tp"]):
                    o["status"] = "CLOSED"
                    self.state.price_BOS = price
                    self._breakeven_v5()
                    self.state.log("V4_TP", "🟢", price=price)
                    actions.append({"type": "V4", "action": "TP", "price": price})
            elif o["type"] == "V5_RUNNER":
                # Pre-breakeven SL
                if not o["is_breakeven"]:
                    if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                        o["status"] = "CLOSED"
                        self.state.log("V5_SL", "🔴", price=price)
                        actions.append({"type": "V5", "action": "SL", "price": price})
                        continue
                # Trailing
                if o["is_breakeven"] and self.state.price_BOS:
                    atr = self._atr(df_15m) if df_15m is not None else 10
                    md = atr * self.config["atr_multiplier"]
                    mb = self.state.bb_middle
                    ns = max(o["sl"], mb - md) if is_buy else min(o["sl"], mb + md)
                    if (is_buy and ns > o["sl"]) or (not is_buy and ns < o["sl"]):
                        o["sl"] = ns
                        o["trailing_active"] = True
                        self.state.log("V5_TRAILING", "📈", SL=ns)
                        actions.append({"type": "V5", "action": "TRAILING", "sl": ns})
                # Post-breakeven SL hit
                if o["is_breakeven"]:
                    if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                        o["status"] = "CLOSED"
                        self.state.log("V5_CLOSE", "🛡️", price=price)
                        actions.append({"type": "V5", "action": "CLOSE", "price": price})

        if all(o["status"] == "CLOSED" for o in self.state.active_orders):
            self.state.log("CYCLE_COMPLETE", "✅")
            self.state.reset()
        return actions

    def _breakeven_v5(self, current_price=None):
        """ย้าย SL = Entry ทันทีที่มีกำไรเล็กน้อย (Fast Breakeven)"""
        for o in self.state.active_orders:
            if o["type"] == "V5_RUNNER" and not o["is_breakeven"]:
                # ใช้ min(0.1%, 2 ATR) เป็น buffer — เล็กที่สุดที่ระบบเอื้อ
                min_buffer = o["entry"] * 0.0005  # 0.05% = 2 points บน 4000
                buf = max(min_buffer, self.config["breakeven_buffer_points"])
                
                if self.state.direction == "BUY":
                    o["sl"] = o["entry"] + buf
                else:
                    o["sl"] = o["entry"] - buf
                
                o["is_breakeven"] = True
                self.state.log("V5_BREAKEVEN", "🛡️", SL=o["sl"], 
                              note="Fast Breakeven — Risk Free!")

    def check_fast_breakeven(self, current_price):
        """เช็คว่าควรย้าย SL หรือยัง — เร็วที่สุดที่ระบบเอื้อ"""
        for o in self.state.active_orders:
            if o["type"] == "V5_RUNNER" and not o["is_breakeven"]:
                profit_pct = abs(current_price - o["entry"]) / o["entry"]
                
                # กำไร ≥ 0.15% → ย้าย SL ทันที!
                if profit_pct >= 0.0015:  # 0.15% = ~6 points บน 4000
                    self._breakeven_v5(current_price)
                    return True
        return False

    def _atr(self, df, period=14):
        h, l, c = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([h - l, (h - c).abs(), (l - c).abs()], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])

    def update(self, df_15m, vsa_ok=False, kivanc_ok=False):
        price = float(df_15m['close'].iloc[-1])
        if self.state.phase == "IDLE":
            self.detect_phase1(df_15m, vsa_ok, kivanc_ok)
        if self.state.phase == "PHASE1":
            if self.detect_phase2(df_15m):
                self.execute_dual_entry(price)
        if self.state.phase == "PHASE3":
            return self.manage_trades(price, df_15m)
        return []

# ═══════════════════════════════════════
# SINGLETONS
# ═══════════════════════════════════════
trade_manager = AlphaTradeManager(direction="BUY")
trade_manager_sell = AlphaTradeManager(direction="SELL")
