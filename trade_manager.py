"""
AlphaTradeManager v4.4 — พร้อม Adaptive Hourly Stats
"""
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Optional, List, Dict
from datetime import datetime
import pandas as pd
import logging

logger = logging.getLogger(__name__)

class HourlyStats:
    """Rolling window สถิติรายชั่วโมง"""
    def __init__(self, maxlen=60):
        self.pnls = defaultdict(lambda: deque(maxlen=maxlen))
    def record(self, hour: int, pnl_points: float):
        self.pnls[hour].append(pnl_points)
    def wr(self, hour: int, min_samples: int = 5) -> float:
        vals = self.pnls[hour]
        if len(vals) < min_samples:
            return 0.5   # เป็นกลาง
        wins = sum(1 for v in vals if v > 0)
        return wins / len(vals)
    def avg_pnl(self, hour: int) -> float:
        vals = self.pnls[hour]
        return sum(vals)/len(vals) if vals else 0.0

# Singleton
hourly_stats = HourlyStats()

from scenario_scanner import ScenarioBlueprint

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
    active_orders: List[Dict] = field(default_factory=list)
    history: List[Dict] = field(default_factory=list)

    def reset(self):
        self.phase = "IDLE"
        self.price_L = None
        self.price_HL = None
        self.price_BOS = None
        self.active_orders.clear()
        self.log("State Reset", "🔄")

    def log(self, event, emoji="", **kw):
        entry = {"event": event, "emoji": emoji, "timestamp": datetime.now().isoformat(), **kw}
        self.history.append(entry)
        if len(self.history) > 20: self.history.pop(0)

class AlphaTradeManager:
    def __init__(self, direction="BUY"):
        self.state = TradeState()
        self.state.direction = direction

    def detect_phase1(self, df_15m, vsa_ok=False, kivanc_ok=False):
        if self.state.phase != "IDLE": return False
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
                self.state.log("PHASE1_L", "🟢", L=self.state.price_L)
                return True
        else:
            if c >= self.state.bb_upper * 0.99 and vsa_ok and kivanc_ok:
                self.state.price_L = float(df_15m['high'].max())
                self.state.phase = "PHASE1"
                self.state.log("PHASE1_H", "🔴", H=self.state.price_L)
                return True
        return False

    def detect_phase2(self, df_15m):
        if self.state.phase != "PHASE1": return False
        if self.state.direction == "BUY":
            r = float(df_15m['low'].iloc[-10:].min())
            if r > self.state.price_L:
                self.state.price_HL = r; self.state.phase = "PHASE2"
                self.state.log("PHASE2_HL", "🟡", HL=r); return True
            if r < self.state.price_L: self.state.reset()
        else:
            r = float(df_15m['high'].iloc[-10:].max())
            if r < self.state.price_L:
                self.state.price_HL = r; self.state.phase = "PHASE2"
                self.state.log("PHASE2_LH", "🔵", LH=r); return True
            if r > self.state.price_L: self.state.reset()
        return False

    def execute_dual_entry(self, price):
        if self.state.phase != "PHASE2": return []
        is_buy = self.state.direction == "BUY"
        v4_sl = self.state.price_HL * 0.998 if is_buy else self.state.price_HL * 1.002
        v4_tp = self.state.bb_upper if is_buy else self.state.bb_lower
        v5_sl = self.state.price_HL * 0.995 if is_buy else self.state.price_HL * 1.005
        self.state.active_orders = [
            {"type": "V4_SCALP", "entry": price, "sl": v4_sl, "tp": v4_tp, "status": "OPEN", "partial_closed": False},
            {"type": "V5_RUNNER", "entry": price, "sl": v5_sl, "tp": None, "status": "OPEN", "is_breakeven": False, "trailing_active": False}
        ]
        self.state.phase = "PHASE3"
        self.state.log("DUAL_ENTRY", "🔥", price=price)
        return self.state.active_orders

    def manage_trades(self, price, df_15m=None):
        actions = []
        if df_15m is not None:
            self.state.bb_middle = float(df_15m['close'].rolling(20).mean().iloc[-1])
        is_buy = self.state.direction == "BUY"
        hour = datetime.now().hour  # สำหรับบันทึก stats
        for o in self.state.active_orders:
            if o["status"] != "OPEN": continue
            if o["type"] == "V4_SCALP":
                if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                    o["status"] = "CLOSED"
                    pnl_pts = price - o["entry"] if is_buy else o["entry"] - price
                    hourly_stats.record(hour, pnl_pts)
                    self.state.log("V4_SL", "🔴", price=price)
                    actions.append({"type": "V4", "action": "SL", "price": price})
                    continue
                half = o["entry"] + (o["tp"] - o["entry"]) * 0.5 if is_buy else o["entry"] - (o["entry"] - o["tp"]) * 0.5
                if (is_buy and price >= half) or (not is_buy and price <= half):
                    if not o["partial_closed"]:
                        o["partial_closed"] = True
                        self.state.log("V4_PARTIAL", "🟡", price=price)
                        actions.append({"type": "V4", "action": "PARTIAL", "price": price})
                if (is_buy and price >= o["tp"]) or (not is_buy and price <= o["tp"]):
                    o["status"] = "CLOSED"
                    pnl_pts = price - o["entry"] if is_buy else o["entry"] - price
                    hourly_stats.record(hour, pnl_pts)
                    self._breakeven_v5()
                    self.state.log("V4_TP", "🟢", price=price)
                    actions.append({"type": "V4", "action": "TP", "price": price})
            elif o["type"] == "V5_RUNNER":
                if not o["is_breakeven"]:
                    if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                        o["status"] = "CLOSED"
                        pnl_pts = price - o["entry"] if is_buy else o["entry"] - price
                        hourly_stats.record(hour, pnl_pts)
                        self.state.log("V5_SL", "🔴", price=price)
                        actions.append({"type": "V5", "action": "SL", "price": price})
                        continue
                if o["is_breakeven"] and self.state.price_BOS:
                    atr = self._atr(df_15m) if df_15m is not None else 10
                    md = atr * 0.5
                    base_mid = self.state.bb_middle
                    ns = max(o["sl"], base_mid - md) if is_buy else min(o["sl"], base_mid + md)
                    if (is_buy and ns > o["sl"]) or (not is_buy and ns < o["sl"]):
                        o["sl"] = ns
                        o["trailing_active"] = True
                        self.state.log("V5_TRAILING", "📈", SL=ns)
                        actions.append({"type": "V5", "action": "TRAILING", "sl": ns})
                if o["is_breakeven"]:
                    if (is_buy and price <= o["sl"]) or (not is_buy and price >= o["sl"]):
                        o["status"] = "CLOSED"
                        pnl_pts = price - o["entry"] if is_buy else o["entry"] - price
                        hourly_stats.record(hour, pnl_pts)
                        self.state.log("V5_CLOSE", "🛡️", price=price)
                        actions.append({"type": "V5", "action": "CLOSE", "price": price})
        if all(o["status"] == "CLOSED" for o in self.state.active_orders):
            self.state.log("CYCLE_COMPLETE", "✅")
            self.state.reset()
        return actions

    def _breakeven_v5(self, current_price=None):
        for o in self.state.active_orders:
            if o["type"] == "V5_RUNNER" and not o["is_breakeven"]:
                if self.state.direction == "BUY": o["sl"] = o["entry"] + 0.5
                else: o["sl"] = o["entry"] - 0.5
                o["is_breakeven"] = True
                self.state.log("V5_BREAKEVEN", "🛡️", SL=o["sl"])

    def _atr(self, df, period=14):
        h,l,c = df['high'], df['low'], df['close'].shift(1)
        tr = pd.concat([h-l,(h-c).abs(),(l-c).abs()], axis=1).max(axis=1)
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

trade_manager = AlphaTradeManager(direction="BUY")
trade_manager_sell = AlphaTradeManager(direction="SELL")
