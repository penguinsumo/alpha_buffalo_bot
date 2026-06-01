"""
equity_guard.py — Alpha Buffalo Risk Management
(อัปเดตระบบกรองไม้ Sniper เพื่อไม่ให้โดนสั่ง Cut มั่วซั่ว)
"""
import uuid

class EquityGuard:
    def __init__(self, starting_equity=10000.0):
        self.starting_equity = starting_equity
        self.is_paused = False
        
        class S: pass
        self.state = S()
        self.state.mode = "CASHFLOW"
        self.state.mode_reason = ""
        self.state.cycle_id = str(uuid.uuid4())
        
        class T: pass
        t = T()
        t.dd_pct = 0.0
        t.trades_count = 0
        t.win_rate = 0.0
        self.state.today = t

    def ok(self): return not self.is_paused
    def mode(self): return self.state.mode
    
    def new_cycle(self, p=""):
        self.state.cycle_id = str(uuid.uuid4())
        return self.state.cycle_id
        
    def set_sniper_mode(self, r=""):
        self.state.mode = "SNIPER"
        self.state.mode_reason = r
        
    def set_cashflow_mode(self, r=""):
        self.state.mode = "CASHFLOW"
        self.state.mode_reason = r
        
    def reset_pause(self): 
        self.is_paused = False

    def lot_size(self, base_lot=0.01, stress_level="low", is_sniper=False):
        if not self.ok(): return 0.0
        m = 2.0 if is_sniper else 1.0
        s = {"low":1.0, "medium":0.75, "high":0.5, "critical":0.0}.get(stress_level, 1.0)
        return min(round(base_lot * m * s, 2), 0.5)

    # === 🚀 [NEW] ฟังก์ชันเช็คการคัททิ้ง ที่มองข้ามออเดอร์ Sniper ===
    def should_cut_basket(self, open_orders: list, current_price: float, max_dd: float = 15.0) -> tuple[bool, str]:
        """
        ประเมินความเสี่ยงเพื่อสั่ง Cut All 
        โดยจะเพิกเฉยออเดอร์ที่ EA ตั้งชื่อว่า Sniper (เพราะจัดการตัวเองได้)
        """
        normal_orders = []
        sniper_exists = False

        # 1. แยกแยะประเภทออเดอร์
        for order in open_orders:
            comment = order.get("comment", "")
            if "Sniper" in comment:
                sniper_exists = True
                continue 
            normal_orders.append(order)

        # 2. ถ้าระบบเหลือแต่ออเดอร์ Sniper อย่างเดียว ให้แจ้งว่าปลอดภัย ไม่ต้องคัท
        if not normal_orders and sniper_exists:
            return False, "Skipped: Only Sniper orders active"

        # 3. ถ้าไม่มีออเดอร์ใดๆ เลย
        if not normal_orders:
            return False, "No active normal orders"

        # 4. คำนวณความเสี่ยงเฉพาะออเดอร์ปกติที่เหลือ
        total_unrealized = sum([o.get("profit", 0) for o in normal_orders])
        dd_pct = (abs(total_unrealized) / self.starting_equity) * 100 if total_unrealized < 0 else 0

        # 5. สั่งคัทถ้า Drawdown ของไม้ปกติเกินกำหนด
        if dd_pct >= max_dd:
            return True, f"Basket DD {dd_pct:.2f}% exceeded limit"
            
        return False, "Safe"

    def summary(self): 
        return f"Mode: {self.state.mode} | DD: {self.state.today.dd_pct:.2f}% | Trades: {self.state.today.trades_count}"

# Instance สำหรับใช้งานร่วมกันทั่วทั้งแอพ
equity_guard = EquityGuard()
