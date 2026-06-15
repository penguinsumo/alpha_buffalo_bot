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


# ============================================================
# 🆕 PHASE 2: SCALP_BE Mode + Breakeven/Trailing
# ============================================================

# ━━━ Constants ━━━
BE_TRIGGER_PCT = 0.0015  # 0.15% — Early BE (Phase 4A+)   # 0.10% — activate breakeven
TRAIL_DISTANCE_PCT = 0.0008  # 0.08% — trailing distance
SCALP_BE_TP_PCT = 0.0030   # 0.30% — wider TP for BE mode
SCALP_BE_SL_PCT = 0.0015   # 0.15% — initial SL
TIMEOUT_CANDLES = 24        # force close after 24 candles (6 hours)

def open_scalp_be_trade(direction: str, price: float) -> dict:
    """เปิด order โหมด SCALP_BE"""
    if direction == 'buy':
        sl = price * (1 - SCALP_BE_SL_PCT)
        tp = price * (1 + SCALP_BE_TP_PCT)
    else:
        sl = price * (1 + SCALP_BE_SL_PCT)
        tp = price * (1 - SCALP_BE_TP_PCT)
    
    return {
        'mode': 'SCALP_BE',
        'direction': direction,
        'entry_price': price,
        'initial_sl': round(sl, 2),
        'tp': round(tp, 2),
        'current_sl': round(sl, 2),
        'be_activated': False,
        'highest_price': price if direction == 'buy' else None,
        'lowest_price': price if direction == 'sell' else None,
        'candles_held': 0,
        'status': 'OPEN'
    }

def manage_scalp_be_position(trade: dict, current_high: float, current_low: float, 
                              current_close: float) -> dict:
    """
    จัดการตำแหน่ง SCALP_BE:
    - ตรวจสอบ TP
    - เปิด Breakeven เมื่อกำไรถึง BE_TRIGGER
    - Trailing stop หลัง BE activated
    - Timeout หลังจาก TIMEOUT_CANDLES
    """
    direction = trade['direction']
    entry = trade['entry_price']
    
    # Increment candle counter
    trade['candles_held'] += 1
    
    # Check timeout
    if trade['candles_held'] >= TIMEOUT_CANDLES:
        trade['status'] = 'CLOSED'
        trade['exit_reason'] = 'TIMEOUT'
        if direction == 'buy':
            trade['pnl_pct'] = (current_close - entry) / entry * 100
        else:
            trade['pnl_pct'] = (entry - current_close) / entry * 100
        return trade
    
    if direction == 'buy':
        # Check TP
        if current_high >= trade['tp']:
            trade['status'] = 'CLOSED'
            trade['exit_reason'] = 'TP_HIT'
            trade['exit_price'] = trade['tp']
            trade['pnl_pct'] = (trade['tp'] - entry) / entry * 100
            return trade
        
        # Breakeven activation
        if not trade['be_activated'] and current_high >= entry * (1 + BE_TRIGGER_PCT):
            trade['be_activated'] = True
            trade['current_sl'] = entry  # Move SL to entry
        
        # Update highest price
        if current_high > trade.get('highest_price', entry):
            trade['highest_price'] = current_high
        
        # Trailing stop (after BE activated)
        if trade['be_activated'] and trade.get('highest_price'):
            trail_sl = trade['highest_price'] * (1 - TRAIL_DISTANCE_PCT)
            trade['current_sl'] = max(trade['current_sl'], trail_sl)
        
        # Check SL
        if current_low <= trade['current_sl']:
            trade['status'] = 'CLOSED'
            trade['exit_reason'] = 'BE_STOP' if trade['be_activated'] else 'SL_HIT'
            trade['exit_price'] = trade['current_sl']
            trade['pnl_pct'] = (trade['current_sl'] - entry) / entry * 100
            return trade
    
    else:  # sell
        # Check TP
        if current_low <= trade['tp']:
            trade['status'] = 'CLOSED'
            trade['exit_reason'] = 'TP_HIT'
            trade['exit_price'] = trade['tp']
            trade['pnl_pct'] = (entry - trade['tp']) / entry * 100
            return trade
        
        # Breakeven activation
        if not trade['be_activated'] and current_low <= entry * (1 - BE_TRIGGER_PCT):
            trade['be_activated'] = True
            trade['current_sl'] = entry
        
        # Update lowest price
        if current_low < trade.get('lowest_price', entry):
            trade['lowest_price'] = current_low
        
        # Trailing stop
        if trade['be_activated'] and trade.get('lowest_price'):
            trail_sl = trade['lowest_price'] * (1 + TRAIL_DISTANCE_PCT)
            trade['current_sl'] = min(trade['current_sl'], trail_sl)
        
        # Check SL
        if current_high >= trade['current_sl']:
            trade['status'] = 'CLOSED'
            trade['exit_reason'] = 'BE_STOP' if trade['be_activated'] else 'SL_HIT'
            trade['exit_price'] = trade['current_sl']
            trade['pnl_pct'] = (entry - trade['current_sl']) / entry * 100
            return trade
    
    return trade

def execute_trade_by_mode(trade_mode: str, direction: str, price: float) -> dict:
    """Router: เลือกวิธีเปิด order ตาม trade_mode"""
    if trade_mode == 'SCALP_BE':
        return open_scalp_be_trade(direction, price)
    elif trade_mode in ['V4_SCALP', 'V5_SNIPER']:
        # Standard trade (existing logic)
        sl_pct = 0.0015
        tp_pct = 0.003 if trade_mode == 'V5_SNIPER' else 0.0015
        if direction == 'buy':
            sl = price * (1 - sl_pct)
            tp = price * (1 + tp_pct)
        else:
            sl = price * (1 + sl_pct)
            tp = price * (1 - tp_pct)
        return {
            'mode': trade_mode,
            'direction': direction,
            'entry_price': price,
            'sl': round(sl, 2),
            'tp': round(tp, 2),
            'status': 'OPEN'
        }
    return {'status': 'ERROR', 'message': f'Unknown mode: {trade_mode}'}

# PINE v6.5: USE_ATR_SL=False, SCORE_THRESHOLD=4.0, COOLDOWN_BARS=5
# Exit Collision: use if/elif (not if/if)

# PINE v6.5: Save trade state BEFORE reset
# last_ep, last_dir, last_exit_price must be saved
# before ep := na to ensure Win/Loss tracking works

# ═══════════════════════════════════════════════
# P0 FIX: Intrabar Fill Model (TV Compatible)
# ═══════════════════════════════════════════════

def intrabar_fill(high, low, entry, direction, tp_price, sl_price):
    """
    TV Broker Emulator Logic:
    ใช้ high/low ของแท่ง → ตรวจว่า TP หรือ SL ถึงก่อน
    """
    if direction == 'BUY':
        # TP hit before SL?
        if high >= tp_price:
            return 'TP', tp_price
        elif low <= sl_price:
            return 'SL', sl_price
        else:
            return None, None
    else:
        # SELL: TP hit before SL?
        if low <= tp_price:
            return 'TP', tp_price
        elif high >= sl_price:
            return 'SL', sl_price
        else:
            return None, None

# ═══════════════════════════════════════════════
# P0 FIX: Accurate Win/Loss Tracking
# ═══════════════════════════════════════════════

class ClosedTrade:
    """Trade Record — saved BEFORE state reset"""
    def __init__(self, entry, exit_price, direction, pnl_pct, exit_reason, timestamp):
        self.entry = entry
        self.exit_price = exit_price
        self.direction = direction
        self.pnl_pct = pnl_pct
        self.exit_reason = exit_reason
        self.timestamp = timestamp

# Store BEFORE resetting state
trade_history = []

def record_closed_trade(entry, exit_price, direction, exit_reason, timestamp):
    """บันทึก trade ก่อน reset state"""
    pnl = (exit_price - entry) / entry * 100 if direction == 'BUY'          else (entry - exit_price) / entry * 100
    
    trade = ClosedTrade(entry, exit_price, direction, pnl, exit_reason, timestamp)
    trade_history.append(trade)
    
    # Update equity
    update_equity(pnl)
    
    return trade

def get_stats():
    """คำนวณสถิติจาก trade_history"""
    if not trade_history:
        return {}
    
    wins = [t for t in trade_history if t.pnl_pct > 0]
    losses = [t for t in trade_history if t.pnl_pct <= 0]
    
    return {
        'total': len(trade_history),
        'wins': len(wins),
        'losses': len(losses),
        'wr': len(wins) / len(trade_history) * 100,
        'avg_win': sum(t.pnl_pct for t in wins) / len(wins) if wins else 0,
        'avg_loss': sum(t.pnl_pct for t in losses) / len(losses) if losses else 0,
        'pf': sum(t.pnl_pct for t in wins) / abs(sum(t.pnl_pct for t in losses)) if losses else float('inf'),
        'net_pnl': sum(t.pnl_pct for t in trade_history)
    }

# v10 INTEGRATION
def get_v10_qty(signal, equity, dd_pct):
    try:
        from alpha_buffalo_signal import V10_READY, V10_CONFIG, PositionSizer
        if V10_READY:
            sizer = PositionSizer(V10_CONFIG)
            return sizer.calculate(signal, equity, dd_pct).get('qty', 0.01)
    except ImportError:
        pass
    return signal.get('qty', 0.01)
