
"""
state_manager.py — Alpha Buffalo v5.4
The Lock: State Machine + Hysteresis + Cooldown
"""

from dataclasses import dataclass, field
from typing import Optional
import time
import logging

logger = logging.getLogger(__name__)

@dataclass
class TradingState:
    """State Machine สำหรับ 3-Step Workflow"""
    state: str = "IDLE"  # IDLE → BRIEFING_SENT → APPROACHING → IN_TRADE
    last_alert_time: float = 0.0
    last_briefing_date: str = ""
    confirmation_bars: int = 0
    price_approaching: bool = False
    last_alert_price: float = 0.0
    blueprint: Optional[object] = None
    
    # Config
    alert_cooldown_sec: int = 120     # 2 นาทีระหว่าง alert
    confirmation_bars_needed: int = 2 # ต้องค้าง 2 แท่งถึงยืนยัน
    reset_distance_pct: float = 0.005 # ออกห่าง 0.5% → reset
    
    # Smart Fetch
    fetch_intervals = {
        "IDLE": 300,
        "BRIEFING_SENT": 120,
        "APPROACHING": 30,
        "IN_TRADE": 15,
    }
    
    def get_fetch_interval(self) -> int:
        return self.fetch_intervals.get(self.state, 300)
    
    def can_send_alert(self) -> bool:
        """เช็คว่าควรส่ง alert หรือยัง (กัน spam)"""
        now = time.time()
        if now - self.last_alert_time < self.alert_cooldown_sec:
            return False
        if abs(self.last_alert_price - self.last_alert_price) < 0.0001 and self.last_alert_time > 0:
            return False  # ราคาไม่เปลี่ยน → ไม่ต้อง alert ซ้ำ
        return True
    
    def update_approaching(self, current_price: float, entry_zone_low: float, entry_zone_high: float) -> bool:
        """อัปเดตสถานะ APPROACHING พร้อม Hysteresis"""
        near = (abs(current_price - entry_zone_low) / entry_zone_low < 0.003 or
                abs(current_price - entry_zone_high) / entry_zone_high < 0.003)
        
        if near:
            self.confirmation_bars += 1
            if self.confirmation_bars >= self.confirmation_bars_needed:
                if self.state != "APPROACHING":
                    self.state = "APPROACHING"
                    logger.info(f"📍 State → APPROACHING (confirmed after {self.confirmation_bars} bars)")
                return True
        else:
            self.confirmation_bars = 0
            if self.state == "APPROACHING":
                # ต้องออกห่าง > 0.5% ถึง reset
                distance = abs(current_price - entry_zone_low) / entry_zone_low
                if distance > self.reset_distance_pct:
                    self.state = "BRIEFING_SENT"
                    logger.info(f"↩️ State → BRIEFING_SENT (price moved {distance*100:.1f}%)")
        
        return False
    
    def mark_alert_sent(self, price: float):
        """บันทึกว่าส่ง alert แล้ว"""
        self.last_alert_time = time.time()
        self.last_alert_price = price
    
    def mark_briefing_sent(self, date: str):
        """บันทึกว่าส่ง briefing แล้ววันนี้"""
        self.last_briefing_date = date
        self.state = "BRIEFING_SENT"
    
    def mark_in_trade(self):
        """บันทึกว่าเข้าเทรดแล้ว"""
        self.state = "IN_TRADE"
    
    def reset(self):
        """Reset ทุกอย่าง"""
        self.state = "IDLE"
        self.confirmation_bars = 0
        self.price_approaching = False


# Singleton
trading_state = TradingState()
